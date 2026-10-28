"""BOMGuard API: upload a BOM, stream per-part risk analysis over Server-Sent Events."""
import asyncio
import csv
import datetime
import hmac
import io
import json
import os
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import agent, bom, config, llm, mock, research

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "samples", "flight_controller_bom.csv")
EXAMPLE = os.path.join(ROOT, "samples", "example_report.json")

app = FastAPI(title="BOMGuard")
SESSIONS: dict[str, dict] = {}
# Live scans accepted today (UTC). In memory: resets if the instance restarts.
SCANS = {"day": "", "count": 0}
# After a billing refusal (402) from Token Factory, live scans pause so no further calls are attempted.
PAUSE = {"until": 0.0}
PAUSE_SECONDS = 15 * 60
PAUSED_MSG = "Live scans are paused right now — here is a real report from an earlier live run."


def _paused() -> bool:
    return time.time() < PAUSE["until"]


def _pause_live() -> None:
    PAUSE["until"] = time.time() + PAUSE_SECONDS
    SCANS["count"] = max(0, SCANS["count"] - 1)  # a refused scan doesn't use up today's allowance


def _live_status() -> dict:
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    if SCANS["day"] != today:
        SCANS.update(day=today, count=0)
    need_code = bool(config.DEMO_ACCESS_CODE) or config.ON_RENDER
    return {
        "enabled": not (need_code and not config.DEMO_ACCESS_CODE),
        "code_required": need_code,
        "max_rows": config.MAX_ROWS,
        "daily_limit": config.DAILY_SCAN_LIMIT,
        "scans_left": max(0, config.DAILY_SCAN_LIMIT - SCANS["count"]),
        "paused": _paused(),
    }


async def _check_access(code: str) -> None:
    """Refuse a live scan before any paid call: wrong/missing access code, or live scans off."""
    st = _live_status()
    if not st["enabled"]:
        raise HTTPException(403, "Live scans are turned off on this demo. The example report shows a real run.")
    if st["code_required"] and not hmac.compare_digest(code.strip().encode(), config.DEMO_ACCESS_CODE.encode()):
        await asyncio.sleep(1)  # slows down guessing
        raise HTTPException(403, "Live scans need an access code. The example report shows a real run.")
    if st["paused"]:
        raise HTTPException(503, PAUSED_MSG)


def _count_scan() -> None:
    """Use one of today's live scans, or refuse if they are used up."""
    if _live_status()["scans_left"] <= 0:
        raise HTTPException(429, f"Today's {config.DAILY_SCAN_LIMIT} live scans are used up. "
                                 "The example report shows a real run; try again tomorrow (UTC).")
    SCANS["count"] += 1


@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "static", "index.html"))


@app.get("/api/config")
def get_config():
    return {
        "mock": config.MOCK,
        "models": {"fast": config.MODEL_FAST, "extract": config.MODEL_EXTRACT, "reason": config.MODEL_REASON},
        "keys": {"nebius": bool(config.NEBIUS_API_KEY), "tavily": bool(config.TAVILY_API_KEY)},
        "live": _live_status(),
    }


@app.get("/api/sample.csv")
def sample():
    return FileResponse(SAMPLE, media_type="text/csv", filename="flight_controller_bom.csv")


@app.get("/api/example")
def example():
    """A saved live run of the sample BOM, shown instantly. Registered as session 'example' so the
    report downloads work the same way as for a live run."""
    with open(EXAMPLE, encoding="utf-8") as f:
        ex = json.load(f)
    SESSIONS["example"] = {"name": ex["name"], "parts": ex["parts"], "results": ex["results"], "brief": ex["brief"]}
    return {**ex, "session": "example"}


@app.post("/api/bom")
async def upload_bom(file: UploadFile | None = File(None), text: str = Form(""), use_sample: bool = Form(False),
                     access_code: str = Form("")):
    await _check_access(access_code)
    if use_sample:
        with open(SAMPLE) as f:
            raw, name = f.read(), "flight_controller_bom.csv"
    elif file is not None:
        raw, name = (await file.read()).decode("utf-8", errors="replace"), file.filename or "bom.csv"
    elif text.strip():
        raw, name = text, "pasted.csv"
    else:
        raise HTTPException(400, "Upload a CSV file or paste BOM rows.")

    headers, rows = bom.read_csv(raw)
    if not rows:
        raise HTTPException(400, "That file has no rows. Export your BOM as CSV with a header row.")
    parts = bom.heuristic_parts(headers, rows)
    if not any(p["mpn"] for p in parts):
        raise HTTPException(400, "Couldn't find a part-number column. Name it 'MPN' or 'Manufacturer Part Number'.")

    _count_scan()  # after free validation, before the first model call
    usage, stats = llm.Usage(), research.TavilyStats()
    try:
        if config.MOCK:
            await asyncio.sleep(0.6)
            usage.add("fast", 1800, 0.6)
            parts = mock.normalize(parts)
        else:
            parts = await bom.normalize(parts, usage)
    except llm.BillingPaused:
        _pause_live()
        raise HTTPException(503, PAUSED_MSG)

    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = {"name": name, "parts": parts, "results": [], "brief": None, "usage": usage, "stats": stats}
    return {"session": sid, "name": name, "parts": parts, "usage": usage.tiers, "truncated": len(rows) > config.MAX_ROWS}


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/analyze/{sid}")
async def analyze(sid: str):
    s = SESSIONS.get(sid)
    if not s or "usage" not in s:
        raise HTTPException(404, "Session expired. Upload the BOM again.")
    if s.get("started"):  # one analysis per accepted scan, so the daily limit can't be bypassed
        raise HTTPException(409, "This BOM was already analyzed. Upload it again to run a new scan.")
    s["started"] = True
    impl = mock if config.MOCK else agent
    q: asyncio.Queue = asyncio.Queue()
    usage, stats = s["usage"], s["stats"]

    async def emit(event, data):
        await q.put(_sse(event, data))

    def snapshot():
        return {"tiers": usage.tiers, "tavily": vars(stats)}

    async def run():
        t0 = time.perf_counter()
        sem = asyncio.Semaphore(config.CONCURRENCY)

        async def one(p):
            async with sem:
                try:
                    r = await impl.analyze_part(p, usage, stats, emit)
                except llm.BillingPaused:
                    raise
                except Exception as e:
                    r = {**p, "lifecycle": "Unknown", "availability": "Unknown", "rohs": "Unknown", "confidence": "low",
                         "summary": f"Research failed: {type(e).__name__}: {str(e)[:160]}", "risk": 45, "band": "watch",
                         "sources": [], "claims": [], "alternatives": []}
                s["results"].append(r)
                await emit("part", r)
                await emit("usage", snapshot())

        s["results"] = []
        if _paused():
            await emit("paused", {"message": PAUSED_MSG})
            await q.put(None)
            return
        tasks = [asyncio.create_task(one(p)) for p in s["parts"]]
        try:
            await asyncio.gather(*tasks)
            await emit("stage", {"row": 0, "stage": "brief"})
            s["brief"] = await impl.brief(s["results"], usage, stats)
        except llm.BillingPaused:
            # Stop every other part at once so no more paid calls are attempted, and tell the page.
            for t in tasks:
                t.cancel()
            _pause_live()
            await emit("paused", {"message": PAUSED_MSG})
            await q.put(None)
            return
        await emit("brief", s["brief"])
        await emit("usage", snapshot())
        await emit("done", {"seconds": round(time.perf_counter() - t0, 1)})
        await q.put(None)

    async def stream():
        task = asyncio.create_task(run())
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            if task.done() and task.exception():
                yield _sse("error", {"message": str(task.exception())[:300]})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sorted(s):
    return sorted(s["results"], key=lambda r: -r.get("risk", 0))


@app.get("/api/report/{sid}.csv")
def report_csv(sid: str):
    s = SESSIONS.get(sid) or {}
    if not s.get("results"):
        raise HTTPException(404, "Run an analysis first.")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Risk", "Band", "Designator", "Manufacturer", "MPN", "Category", "Lifecycle", "Availability",
                "RoHS", "Confidence", "Summary", "Alternatives", "Sources"])
    for r in _sorted(s):
        w.writerow([r.get("risk"), r.get("band"), r.get("refdes"), r.get("manufacturer"), r.get("mpn"), r.get("category"),
                    r.get("lifecycle"), r.get("availability"), r.get("rohs"), r.get("confidence"), r.get("summary"),
                    "; ".join(f"{a['mpn']} ({a['fit']})" for a in r.get("alternatives", [])),
                    " ".join(c["url"] for c in r.get("claims", []))])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="bomguard-report-{sid}.csv"'})


@app.get("/api/report/{sid}.md")
def report_md(sid: str):
    s = SESSIONS.get(sid) or {}
    if not s.get("results"):
        raise HTTPException(404, "Run an analysis first.")
    b = s.get("brief") or {}
    lines = [f"# BOMGuard report — {s['name']}", "", f"**BOM health: {b.get('health', '?')}/100**", "",
             b.get("headline", ""), "", "## Top risks", ""]
    for t in b.get("top_risks", []):
        lines.append(f"- **{t.get('mpn')}** ({t.get('urgency')}): {t.get('why')} → {t.get('action')}")
    lines += ["", "## Next steps", ""] + [f"- {n}" for n in b.get("next_steps", [])]
    lines += ["", "## Parts", "", "| Risk | Part | Lifecycle | Availability | Alternatives |", "|---|---|---|---|---|"]
    for r in _sorted(s):
        alts = ", ".join(f"{a['mpn']} ({a['fit']})" for a in r.get("alternatives", [])) or "—"
        lines.append(f"| {r.get('risk')} | {r.get('manufacturer')} {r.get('mpn') or r.get('description')} | "
                     f"{r.get('lifecycle')} | {r.get('availability')} | {alts} |")
    lines += ["", "## Evidence", ""]
    for r in _sorted(s):
        for c in r.get("claims", []):
            lines.append(f"- {r.get('mpn')}: {c['claim']} [source]({c['url']})")
    lines += ["", "_Generated by BOMGuard — Nemotron on Nebius Token Factory + Tavily live research._"]
    return PlainTextResponse("\n".join(lines), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="bomguard-report-{sid}.md"'})


app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "static")), name="static")
