"""The BOMGuard agent: research -> extract -> score -> (if risky) find + validate alternatives -> brief."""
import re

from . import config, llm, research

LIFECYCLES = ["Active", "NRND", "Last Time Buy", "EOL", "Obsolete", "Unknown"]
LIFECYCLE_RISK = {"Active": 10, "NRND": 55, "Last Time Buy": 80, "EOL": 85, "Obsolete": 95, "Unknown": 45}  # Unknown is never "healthy"
AVAIL_RISK = {"In stock": 0, "Limited": 15, "Out of stock": 25, "Unknown": 5}


def score(facts: dict) -> int:
    """Deterministic, explainable risk score. The model reports facts; code does the math."""
    s = LIFECYCLE_RISK.get(facts.get("lifecycle"), 40)
    s += AVAIL_RISK.get(facts.get("availability"), 5)
    if facts.get("rohs") == "Non-compliant":
        s += 15
    return max(0, min(100, s))


def band(s: int) -> str:
    return "critical" if s >= 75 else "watch" if s >= 45 else "healthy"


def _sources_block(sources: list[dict]) -> str:
    return "\n\n".join(f"[{i}] {s['title']}\nURL: {s['url']}\n{s['content']}" for i, s in enumerate(sources))


def _cite(items: list[dict], sources: list[dict]) -> list[dict]:
    """Keep only claims that point at a real source we retrieved. Uncited claims are dropped."""
    out = []
    for it in items or []:
        idx = it.get("source_index")
        if isinstance(idx, int) and 0 <= idx < len(sources):
            it = dict(it)
            it["url"] = sources[idx]["url"]
            it["source_title"] = sources[idx]["title"]
            out.append(it)
    return out


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w%.\- ]", " ", (t or "").lower())).strip()


def _mentions(source: dict, fam: str) -> bool:
    return fam in research.core(source.get("title", "") + " " + source.get("content", "") + " " + source.get("url", ""))


def _quote_ok(quote: str, source: dict) -> bool:
    """The quote must really be in the page. Allow small tokenisation differences: 80% of its words, in order-free match."""
    q, body = _norm(quote), _norm(source.get("title", "") + " " + source.get("content", ""))
    if not q:
        return False
    if q in body:
        return True
    words = [w for w in q.split() if len(w) > 2]
    return bool(words) and sum(w in body for w in words) / len(words) >= 0.8


# Datasheets carry a legend that DEFINES every status ("NRND: Not recommended for new designs. Device is in
# production to support existing customers..."). Quoting the legend says nothing about this part.
_LEGEND = re.compile(
    r"^\W*(active|nrnd|obsolete|lifebuy|ltb|preview|eol)\s*[:=-]\s*"
    r"(product device|not recommended|ti has|device has been|device is in production|the manufacturer has|"
    r"product is|device recommended|the device)", re.I)
# The same legend definitions quoted without their "LIFEBUY:"/"NRND:" label (TI's standard wording).
_LEGEND_SENTENCE = re.compile(
    r"TI has announced that the device will be discontinued|lifetime-buy period is in effect|"
    r"device is in production to support existing customers|TI has discontinued the production of the device|"
    r"device has been announced but is not in production|product device recommended for new designs|"
    r"\b(nrnd|lifebuy|obsolete|active|preview)\s*:\s*(not recommended for new designs|TI has|product device)", re.I)


# Map a verified lifecycle quote to the status it states. Order matters: "not recommended" before "active", etc.
_QUOTE_STATUS = [
    ("NRND", re.compile(r"not recommended|not for new design|\bnrnd\b", re.I)),
    ("Last Time Buy", re.compile(r"last[- ]time[- ]buy|\blifebuy\b|\bltb\b|last buy", re.I)),
    ("EOL", re.compile(r"end[- ]of[- ]life|\beol\b", re.I)),
    ("Obsolete", re.compile(r"obsolete|discontinued|out of production|no longer (manufactured|produced|in production)", re.I)),
    ("Active", re.compile(r"\bactive\b|in production|\bproduction\b", re.I)),
]


def quote_status(quote: str) -> str | None:
    for status, rx in _QUOTE_STATUS:
        if rx.search(quote or ""):
            return status
    return None


# Manufacturer names that don't match their web domain.
_MFR_DOMAINS = {"texas": "ti", "ti": "ti", "silicon": "silabs", "silabs": "silabs", "maxim": "analog",
                "adi": "analog", "analog": "analog", "stmicroelectronics": "st", "st": "st", "on": "onsemi",
                "bosch": "bosch-sensortec", "tdk": "tdk", "invensense": "tdk"}


def _host_labels(url: str) -> list[str]:
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    return host.split(".")


def source_tier(url: str, manufacturer: str) -> int:
    """3 = the manufacturer's own site, 2 = a distributor, 1 = anything else (blogs, forums, datasheet mirrors)."""
    labels = _host_labels(url)
    words = re.findall(r"[a-z0-9]+", (manufacturer or "").lower())
    keys = {_MFR_DOMAINS.get(w, w) for w in words[:1]} | {_MFR_DOMAINS[w] for w in words if w in _MFR_DOMAINS}
    if any(k and k in labels for k in keys):
        return 3
    if any(d.split(".")[0] in labels for d in research.DISTRIBUTORS):
        return 2
    return 1


# An explicit status field inside a quote, e.g. "Part Status. Active" or "Manufacturer Lifecycle StatusFULL PRODUCTION".
_STATUS_FIELD = re.compile(r"(?:part|product|lifecycle|life cycle)\s*status\W{0,4}\s*[A-Za-z][A-Za-z\- ]{1,40}", re.I)


def status_field_claims(claims: list[dict]) -> list[dict]:
    """A verified quote filed under another field (e.g. rohs) may still contain the part's status field.
    Re-file that exact snippet as lifecycle evidence; it is a substring of a verified quote, so still on the page."""
    out = []
    for c in claims:
        if c.get("field") == "lifecycle":
            continue
        m = _STATUS_FIELD.search(c.get("quote", ""))
        if m and quote_status(m.group(0)) and not _LEGEND_SENTENCE.search(c.get("quote", "")):
            out.append(dict(c, field="lifecycle", quote=m.group(0).strip()))
    return out


def lifecycle_conflict(status: str, claims: list[dict]) -> bool:
    """True if verified quotes disagree on whether the part is Active at all (Obsolete vs EOL is not a conflict)."""
    stated = {quote_status(c.get("quote", "")) for c in claims if c.get("field") == "lifecycle"} - {None}
    return any((s == "Active") != (status == "Active") for s in stated)


def reconcile_lifecycle(verdict: str, claims: list[dict], manufacturer: str = "") -> str:
    """The lifecycle verdict must agree with the verified quotes. A quote from the manufacturer's own site decides.
    Without one, if any sources disagree on Active vs at-risk (distributor fields lag), the answer is Unknown.
    Otherwise the best tier that states a status decides; if that tier disagrees with itself, the answer is Unknown."""
    by_tier: dict[int, set] = {}
    for c in claims:
        if c.get("field") != "lifecycle":
            continue
        s = quote_status(c.get("quote", ""))
        if s:
            by_tier.setdefault(source_tier(c.get("url", ""), manufacturer), set()).add(s)
    if not by_tier:
        return verdict
    everything = set().union(*by_tier.values())
    if max(by_tier) < 3 and "Active" in everything and len(everything) > 1:
        return "Unknown"
    stated = by_tier[max(by_tier)]
    if len(stated) == 1:
        return next(iter(stated))
    return verdict if verdict in stated else "Unknown"


def verify_claims(items: list[dict], sources: list[dict], fam: str) -> list[dict]:
    """Keep a claim only if: it cites a real retrieved source, that source mentions THIS part family,
    and the quoted text actually appears in the source. Everything else is dropped."""
    out = []
    for it in items or []:
        idx = it.get("source_index")
        if not (isinstance(idx, int) and 0 <= idx < len(sources)):
            continue
        src = sources[idx]
        if not _mentions(src, fam) or not _quote_ok(it.get("quote", ""), src):
            continue
        if it.get("field") == "lifecycle" and (_LEGEND.search(it.get("quote", "")) or _LEGEND_SENTENCE.search(it.get("quote", ""))):
            continue
        it = dict(it, url=src["url"], source_title=src["title"])
        out.append(it)
    return out


def same_part(a: str, b: str) -> bool:
    """True if two MPNs are the same chip in a different package / grade / reel (ATMEGA328P-AU vs -PU, -TR...)."""
    x, y = research.core(a), research.core(b)
    if not x or not y:
        return False
    n = 0
    while n < min(len(x), len(y)) and x[n] == y[n]:
        n += 1
    return x == y or (n >= 6 and n >= min(len(x), len(y)) - 2) or research.family(a) == research.family(b)


EXTRACT_SYSTEM = f"""You are a component-engineering analyst. You are given web pages about ONE electronic part.
Report only what the pages state about THIS EXACT part number or its exact base part.
Statements about a different variant or related part do NOT apply: e.g. W25Q32FV is not W25Q32JV,
ATmega328 is not ATmega328P, BMP280 is not BMP180, CP2102N is not CP2102.
Datasheet legends that DEFINE status codes (e.g. "NRND: Not recommended for new designs. Device is in production...")
are definitions, not this part's status: in a TI orderable table, the status is the word in this part's own row.
Distributor "Product status" fields are strong evidence. Manufacturer EOL/PCN notices are the strongest.
If the manufacturer's own product page is among the sources and shows a status, quote it (distributor fields can lag).
If nothing on the pages states it for this part, answer "Unknown". Unknown is a good answer; guessing is a bad one.
lifecycle is one of {LIFECYCLES}. NRND = not recommended for new designs. "Discontinued" = Obsolete.
availability is one of ["In stock","Limited","Out of stock","Unknown"].
rohs is one of ["Compliant","Non-compliant","Unknown"].
confidence is one of ["high","medium","low"].
For every fact you report, add a claim with field = lifecycle | availability | rohs | successor,
the source index, and quote = the exact words copied from that page (max 25 words) that state it.
successor: an MPN the manufacturer or a distributor explicitly names as the replacement for THIS part, else null.
Return JSON:
{{"lifecycle":str,"availability":str,"rohs":str,"confidence":str,
"summary":"one plain sentence an engineer can act on",
"claims":[{{"field":str,"claim":str,"quote":str,"source_index":int}}],
"successor":str|null}}"""


ALT_SYSTEM = """You are a senior hardware engineer choosing replacements for an at-risk electronic part.
From the pages, propose at most 3 real, currently orderable alternatives that are named in the pages.
Rules:
- Never propose the same chip in another package, temperature grade or reel (e.g. -AU vs -PU, -TR, SSIQ vs SSIM).
- Never propose a part the pages say is itself obsolete, discontinued or not recommended for new designs.
- A through-hole package (DIP) is never pin-compatible with a surface-mount one (TQFP, QFN, SOIC).
Judge fit honestly:
- "Drop-in": same footprint, pinout and register/electrical behaviour; no PCB or firmware change.
- "Pin-compatible": same footprint/pinout but firmware or passive changes needed.
- "Functional": does the same job but needs a PCB change.
List the concrete differences an engineer must check (voltage, package, interface, register map, accuracy).
Cite the source index where the alternative is named. Never invent part numbers.
Return JSON:
{"alternatives":[{"mpn":str,"manufacturer":str,"fit":"Drop-in"|"Pin-compatible"|"Functional","differences":str,"source_index":int}],
"recommendation":"one sentence: which to pick and why"}"""


BRIEF_SYSTEM = """You are the hardware program's supply-chain lead. You get an analysed bill of materials
(each part with a verified lifecycle, availability, risk score, alternatives) plus recent supply-chain news.
Write a short risk briefing for an engineering manager. Be specific: name MPNs, say what to do and how urgent.
Treat "Unknown" as "not verified — check manually", never as obsolete.
urgency "now" only for parts that are Obsolete, EOL, Last Time Buy or Out of stock.
Do not restate every part. Do not invent facts beyond the input. Do not put source numbers in the text.
Keep each "why" and "action" under 25 words, and the headline under 20 words.
Return JSON:
{"headline":"one sentence verdict on whether this BOM is safe to take to production",
"top_risks":[{"mpn":str,"why":str,"action":str,"urgency":"now"|"this quarter"|"monitor"}],
"market_notes":[{"note":str,"source_index":int}],
"next_steps":[str]}"""


async def analyze_part(part: dict, usage: llm.Usage, stats: research.TavilyStats, emit) -> dict:
    mpn, mfr = part["mpn"], part["manufacturer"]
    result = {**part, "sources": [], "claims": [], "alternatives": []}

    if part.get("is_generic") or not mpn:
        result.update(lifecycle="Generic", availability="Multi-source", rohs="Unknown", confidence="high",
                      summary="Generic passive with many interchangeable sources; low single-source risk.",
                      risk=5, band="healthy", successor=None)
        return result

    await emit("stage", {"row": part["row"], "stage": "research"})
    sources = await research.research_part(stats, mpn, mfr)
    result["sources"] = sources

    await emit("stage", {"row": part["row"], "stage": "extract"})
    try:
        facts = await llm.chat_json(
            "extract", EXTRACT_SYSTEM,
            f"PART: {mfr} {mpn} ({part.get('category') or part.get('description')})\n\nSOURCES:\n{_sources_block(sources)}",
            usage, max_tokens=2500,
        )
    except Exception as e:
        facts = {"lifecycle": "Unknown", "availability": "Unknown", "rohs": "Unknown", "confidence": "low",
                 "summary": f"Could not extract facts automatically ({type(e).__name__}). Check sources manually.",
                 "claims": [], "successor": None}

    for k, allowed in (("lifecycle", LIFECYCLES), ("availability", list(AVAIL_RISK)), ("rohs", ["Compliant", "Non-compliant", "Unknown"])):
        if facts.get(k) not in allowed:
            facts[k] = "Unknown"
    fam = research.family(mpn)
    claims = verify_claims(facts.get("claims"), sources, fam)
    claims += status_field_claims(claims)
    verified = {c.get("field") for c in claims}
    dropped = []
    for k in ("lifecycle", "availability", "rohs"):
        if facts[k] != "Unknown" and k not in verified:
            dropped.append(k)
            facts[k] = "Unknown"
    overridden = False
    if "lifecycle" in verified:
        fixed = reconcile_lifecycle(facts["lifecycle"], claims, mfr)
        # Only rewrite the model's summary when the meaning changed (Active vs at-risk, or now Unknown);
        # EOL -> Obsolete keeps the model's more useful wording.
        before = facts["lifecycle"]
        overridden = fixed != before and ("Unknown" in (fixed, before) or (fixed == "Active") != (before == "Active"))
        facts["lifecycle"] = fixed
    successor = facts.get("successor")
    if successor and ("successor" not in verified or same_part(successor, mpn)):
        successor = None
    summary = facts.get("summary", "")
    if "lifecycle" in dropped or (facts["lifecycle"] == "Unknown" and not summary):
        summary = "No page confirmed this exact part's lifecycle status. Check the manufacturer's product page before ordering."
    elif overridden:
        summary = (f"Verified sources state: {facts['lifecycle']}." if facts["lifecycle"] != "Unknown"
                   else "Sources disagree on this part's lifecycle status. Check the manufacturer's product page before ordering.")
    confidence = facts.get("confidence", "low") if "lifecycle" in verified else "low"
    if facts["lifecycle"] == "Unknown":
        confidence = "low"
    elif lifecycle_conflict(facts["lifecycle"], claims):
        confidence = "low"
        summary = (summary + " Some sources disagree; confirm on the manufacturer's product page.").strip()
    result.update(
        lifecycle=facts["lifecycle"], availability=facts["availability"], rohs=facts["rohs"],
        confidence=confidence, summary=summary, claims=claims, successor=successor,
        unverified=dropped,
    )
    result["risk"] = score(result)
    result["band"] = band(result["risk"])

    if result["risk"] >= config.ALT_THRESHOLD:
        await emit("stage", {"row": part["row"], "stage": "alternatives"})
        alt_sources = await research.research_alternatives(stats, mpn, mfr, part.get("category") or part.get("description", ""))
        base = len(sources)
        all_sources = sources + alt_sources
        try:
            alts = await llm.chat_json(
                "reason", ALT_SYSTEM,
                f"AT-RISK PART: {mfr} {mpn}\nCategory: {part.get('category')}\nDescription: {part.get('description')}\n"
                f"Status: {result['lifecycle']}, {result['availability']}\nKnown successor: {result['successor']}\n\n"
                f"SOURCES:\n{_sources_block(all_sources)}",
                usage, max_tokens=8000,
            )
            cands = _cite(alts.get("alternatives"), all_sources)
            result["alternatives"] = [
                a for a in cands
                if a.get("mpn") and not same_part(a["mpn"], mpn)
                and _mentions(all_sources[a["source_index"]], research.family(a["mpn"]))
                and a.get("fit") in ("Drop-in", "Pin-compatible", "Functional")
            ][:3]
            result["recommendation"] = alts.get("recommendation", "")
        except Exception as e:
            result["recommendation"] = f"Alternative search failed ({type(e).__name__})."
        result["sources"] = all_sources
        result["alt_source_offset"] = base
    return result


def health(results: list[dict]) -> int:
    """BOM health 0-100: weighted toward the worst parts, because one dead part stops production."""
    scored = [r["risk"] for r in results if "risk" in r]
    if not scored:
        return 100
    worst = sorted(scored, reverse=True)[:3]
    avg = sum(scored) / len(scored)
    return max(0, round(100 - (0.5 * avg + 0.5 * (sum(worst) / len(worst)))))


async def brief(results: list[dict], usage: llm.Usage, stats: research.TavilyStats) -> dict:
    risky = sorted([r for r in results if r.get("risk", 0) >= 45], key=lambda r: -r["risk"])
    mfrs = []
    for r in risky:
        if r["manufacturer"] and r["manufacturer"] not in mfrs:
            mfrs.append(r["manufacturer"])
    news: list[dict] = []
    for m in mfrs[:3]:
        try:
            news += await research.market_signals(stats, m)
        except Exception:
            pass

    table = "\n".join(
        f"- {r['mpn']} ({r['manufacturer']}, {r.get('category','')}): {r.get('lifecycle')}, {r.get('availability')}, "
        f"risk {r.get('risk')}, alts: {', '.join(a['mpn'] + ' [' + a['fit'] + ']' for a in r.get('alternatives', [])) or 'none'}"
        for r in results if r.get("lifecycle") != "Generic"
    )
    news_block = "\n".join(f"[{i}] {n['title']} — {n['content'][:400]}" for i, n in enumerate(news)) or "(none)"
    out = {"health": health(results), "headline": "", "top_risks": [], "market_notes": [], "next_steps": []}
    try:
        b = await llm.chat_json("reason", BRIEF_SYSTEM, f"BOM:\n{table}\n\nNEWS:\n{news_block}", usage, max_tokens=8000)
        out.update(headline=b.get("headline", ""), top_risks=b.get("top_risks", [])[:5],
                   next_steps=b.get("next_steps", [])[:5], market_notes=_cite(
                       [{"claim": n.get("note"), "source_index": n.get("source_index")} for n in b.get("market_notes", [])], news))
    except Exception as e:
        out["headline"] = f"Briefing unavailable ({type(e).__name__}); see per-part results."
    return out
