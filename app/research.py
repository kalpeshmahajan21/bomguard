"""Live web research via Tavily, with a small disk cache so repeat runs don't burn credits."""
import asyncio
import hashlib
import json
import os
import re
from tavily import AsyncTavilyClient
from . import config

_tv: AsyncTavilyClient | None = None

# Distributors publish an explicit "Product status" field (Active / Obsolete / Not for new designs)
DISTRIBUTORS = ["digikey.com", "mouser.com", "octopart.com", "arrow.com", "lcsc.com", "farnell.com",
                "newark.com", "rs-online.com", "findchips.com", "avnet.com", "tme.eu"]

STATUS_WORDS = ["product status", "lifecycle", "life cycle", "obsolete", "discontinued", "end of life", "eol",
                "not recommended", "nrnd", "last time buy", "ltb", "active", "in stock", "out of stock",
                "rohs", "pcn", "notice", "stock"]
ALT_WORDS = ["replacement", "replace", "successor", "alternative", "recommended", "migrat", "pin-compatible",
             "pin compatible", "drop-in", "drop in", "equivalent", "upgrade", "instead"]


def tavily() -> AsyncTavilyClient:
    global _tv
    if _tv is None:
        _tv = AsyncTavilyClient(api_key=config.TAVILY_API_KEY)
    return _tv


class TavilyStats:
    def __init__(self):
        self.searches = 0
        self.credits = 0
        self.sources = 0
        self.cache_hits = 0


def core(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def family(mpn: str) -> str:
    """The part's identity without packaging/temperature/reel suffix.
    ATMEGA328P-AU -> ATMEGA328P, W25Q32JVSSIQ -> W25Q32JV, TPS63020DSJR -> TPS63020, MPU-6050 -> MPU6050."""
    first = (mpn or "").split("-")[0]
    if "-" in (mpn or "") and len(core(first)) >= 5:
        return core(first)
    c = core(mpn)
    m = re.match(r"^(.*\d)([A-Z]*)$", c)
    if not m:
        return c
    head, tail = m.groups()
    if len(tail) > 4:
        head += tail[:2]
    return head if len(head) >= 4 else c


def _focus(text: str, words: list[str], anchor: str, limit: int = 2200) -> str:
    """Pull the parts of a long page that talk about status/alternatives, preferring spots near the part number."""
    if not text:
        return ""
    low = text.lower()
    hits = []
    for w in words:
        for m in re.finditer(re.escape(w), low):
            hits.append(m.start())
            if len(hits) > 60:
                break
    a = anchor.lower()
    for m in re.finditer(re.escape(a), low) if a else []:
        hits.append(m.start())
    if not hits:
        return text[:limit]
    # Score windows: near the anchor first
    anchor_pos = [m.start() for m in re.finditer(re.escape(a), low)] if a else []

    def near(p):
        return min((abs(p - q) for q in anchor_pos), default=10_000)

    windows = []
    for p in sorted(set(hits), key=near):
        s, e = max(0, p - 300), min(len(text), p + 400)
        if any(s < we and e > ws for ws, we in windows):
            continue
        windows.append((s, e))
        if sum(we - ws for ws, we in windows) >= limit:
            break
    windows.sort()
    return " … ".join(re.sub(r"\s+", " ", text[s:e]) for s, e in windows)[: limit + 200]


def _cache_path(key: str) -> str:
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    return os.path.join(config.CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".json")


async def _search(stats: TavilyStats, **kwargs) -> list[dict]:
    key = json.dumps(kwargs, sort_keys=True)
    path = _cache_path(key)
    if os.path.exists(path):
        stats.cache_hits += 1
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    res = await tavily().search(**kwargs)
    out = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content") or "",
         "raw": (r.get("raw_content") or "")[:80_000]}
        for r in res.get("results", []) if r.get("url")
    ]
    stats.searches += 1
    stats.credits += 2 if kwargs.get("search_depth") == "advanced" else 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


def _finish(stats: TavilyStats, results: list[list[dict]], words: list[str], anchor: str, cap: int = 8) -> list[dict]:
    seen, out = set(), []
    for r in (x for group in results for x in group):
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        body = _focus(r["raw"], words, anchor) if r["raw"] else ""
        content = (r["snippet"] + (" … " + body if body else "")).strip()
        out.append({"title": r["title"], "url": r["url"], "content": content[:2600]})
        if len(out) >= cap:
            break
    stats.sources += len(out)
    return out


async def _safe(coro):
    try:
        return await coro
    except Exception:
        return []


async def research_part(stats: TavilyStats, mpn: str, manufacturer: str) -> list[dict]:
    """Two searches in parallel: distributor product pages (official status field) + discontinuation notices."""
    a = _safe(_search(stats, query=f"{mpn} {manufacturer} product status", search_depth="advanced",
                      max_results=5, include_domains=DISTRIBUTORS, include_raw_content=True))
    b = _safe(_search(stats, query=f'"{mpn}" {manufacturer} end of life discontinued obsolete "not recommended for new designs" notice',
                      search_depth="advanced", max_results=5, include_raw_content=True))
    groups = await asyncio.gather(a, b)
    return _finish(stats, groups, STATUS_WORDS, family(mpn))


async def research_alternatives(stats: TavilyStats, mpn: str, manufacturer: str, description: str) -> list[dict]:
    """Only for risky parts, to save credits."""
    g = await _safe(_search(stats, query=f"{mpn} {manufacturer} replacement successor recommended for new designs {description}",
                            search_depth="advanced", max_results=6, include_raw_content=True))
    return _finish(stats, [g], ALT_WORDS, family(mpn))


async def market_signals(stats: TavilyStats, manufacturer: str) -> list[dict]:
    """Recent supply-chain news for a manufacturer: shortages, lead times, discontinuations."""
    g = await _safe(_search(stats, query=f"{manufacturer} semiconductor supply shortage lead time discontinuation allocation",
                            topic="news", days=120, max_results=3))
    return _finish(stats, [g], [], "", cap=3)
