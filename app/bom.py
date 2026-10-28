"""BOM ingestion: read any CSV export (KiCad, Altium, Eagle, hand-made), then let Nemotron Nano clean it up."""
import csv
import io
import re
from . import config, llm

# Header synonyms seen in KiCad / Altium / Eagle / Octopart / distributor exports
SYNONYMS = {
    "mpn": ["mpn", "manufacturer part number", "mfr part number", "mfr part #", "mfr. part #", "part number",
            "manufacturer_part_number", "mfg part number", "mfr_pn", "part #", "pn", "part"],
    "manufacturer": ["manufacturer", "mfr", "mfg", "manufacturer name", "mfr name", "brand", "vendor"],
    "description": ["description", "desc", "comment", "value", "part description", "notes"],
    "qty": ["qty", "quantity", "qnty", "count", "amount"],
    "refdes": ["designator", "reference", "references", "refdes", "ref des", "ref", "designators"],
}


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower().replace("_", " "))


def detect_columns(headers: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    normed = [_norm(h) for h in headers]
    for field, names in SYNONYMS.items():
        for name in names:  # synonyms are ordered by specificity
            if name in normed and normed.index(name) not in found.values():
                found[field] = normed.index(name)
                break
    return found


def read_csv(text: str) -> tuple[list[str], list[list[str]]]:
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def heuristic_parts(headers: list[str], rows: list[list[str]]) -> list[dict]:
    cols = detect_columns(headers)

    def get(row, field):
        i = cols.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    parts = []
    for i, row in enumerate(rows[: config.MAX_ROWS]):
        parts.append({
            "row": i + 1,
            "mpn": get(row, "mpn"),
            "manufacturer": get(row, "manufacturer"),
            "description": get(row, "description"),
            "qty": get(row, "qty") or "1",
            "refdes": get(row, "refdes"),
            "raw": " | ".join(c for c in row if c.strip()),
        })
    return parts


NORMALIZE_SYSTEM = """You clean up electronic bill-of-materials rows.
For each input row return the manufacturer part number (MPN) exactly as an orderable part number,
the manufacturer's canonical company name (e.g. "TI" -> "Texas Instruments", "ST" -> "STMicroelectronics"),
a short category (e.g. "MCU", "LDO regulator", "IMU", "USB-UART bridge", "Op-amp", "Passive"),
and is_generic=true for generic passives (plain resistors/capacitors/headers with no specific MPN).
Never invent an MPN that is not in the row. If you cannot tell, keep the given value.
Return JSON: {"rows":[{"row":int,"mpn":str,"manufacturer":str,"category":str,"is_generic":bool}]}"""


async def normalize(parts: list[dict], usage: llm.Usage) -> list[dict]:
    """Nemotron Nano: fast, cheap pass over every row in batches."""
    by_row = {p["row"]: p for p in parts}
    for start in range(0, len(parts), 40):
        batch = parts[start : start + 40]
        lines = "\n".join(
            f'{p["row"]}: mpn="{p["mpn"]}" mfr="{p["manufacturer"]}" desc="{p["description"]}" raw="{p["raw"][:160]}"'
            for p in batch
        )
        try:
            out = await llm.chat_json("fast", NORMALIZE_SYSTEM, lines, usage, max_tokens=4000)
            for r in out.get("rows", []):
                p = by_row.get(int(r.get("row", -1)))
                if not p:
                    continue
                # Only accept an MPN that literally appears in the original row — never an invented one
                if r.get("mpn") and r["mpn"].replace(" ", "").upper() in p["raw"].replace(" ", "").upper():
                    p["mpn"] = r["mpn"].strip()
                if r.get("manufacturer"):
                    p["manufacturer"] = r["manufacturer"].strip()
                p["category"] = (r.get("category") or "").strip()
                p["is_generic"] = bool(r.get("is_generic"))
        except llm.BillingPaused:
            raise
        except Exception as e:  # never block the run on normalization
            for p in batch:
                p.setdefault("normalize_error", str(e)[:200])
    for p in parts:
        p.setdefault("category", "")
        p.setdefault("is_generic", not p["mpn"])
    return parts
