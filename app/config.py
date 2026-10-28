import os
from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    """Read a setting, trimming stray spaces/newlines (a pasted key with a newline breaks the auth header)."""
    return os.getenv(name, default).strip()

NEBIUS_API_KEY = _env("NEBIUS_API_KEY", "")
NEBIUS_BASE_URL = _env("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")

# Three Nemotron tiers, each with one job:
#   FAST    -> normalize messy BOM rows (cheap, many rows per call)
#   EXTRACT -> turn web evidence into structured, cited facts per part
#   REASON  -> validate alternatives + BOM-level risk briefing
# IDs from the Token Factory catalog. Run `python scripts/check_setup.py` to confirm on your account.
MODEL_FAST = _env("MODEL_FAST", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
MODEL_EXTRACT = _env("MODEL_EXTRACT", "nvidia/nemotron-3-super-120b-a12b")
MODEL_REASON = _env("MODEL_REASON", "nvidia/Nemotron-3-Ultra-550b-a55b")

TAVILY_API_KEY = _env("TAVILY_API_KEY", "")
CONCURRENCY = int(_env("CONCURRENCY", "6"))
MOCK = _env("MOCK", "0") == "1"
MAX_ROWS = int(_env("MAX_ROWS", "30"))

# Spend guards for the public demo. A live scan needs DEMO_ACCESS_CODE when it is set. On Render (which sets
# RENDER) live scans are off entirely unless a code is set, so a forgotten env var can't burn credits.
DEMO_ACCESS_CODE = _env("DEMO_ACCESS_CODE", "")
ON_RENDER = bool(_env("RENDER", ""))
DAILY_SCAN_LIMIT = int(_env("DAILY_SCAN_LIMIT", "20"))

# MOCK only: fake a Token Factory 402 to test the "live scans paused" path. "upload" fails while cleaning
# the BOM, "scan" fails on the first part. Ignored when MOCK is off.
SIMULATE_402 = _env("SIMULATE_402", "") if MOCK else ""
CACHE_DIR = _env("CACHE_DIR", ".cache")

# A part at or above this risk score gets an alternatives search + Ultra validation.
ALT_THRESHOLD = int(_env("ALT_THRESHOLD", "50"))
