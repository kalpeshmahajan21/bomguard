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
MAX_ROWS = int(_env("MAX_ROWS", "150"))
CACHE_DIR = _env("CACHE_DIR", ".cache")

# A part at or above this risk score gets an alternatives search + Ultra validation.
ALT_THRESHOLD = int(_env("ALT_THRESHOLD", "50"))
