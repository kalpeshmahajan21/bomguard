import os
from dotenv import load_dotenv

load_dotenv()

NEBIUS_API_KEY = os.getenv("NEBIUS_API_KEY", "")
NEBIUS_BASE_URL = os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")

# Three Nemotron tiers, each with one job:
#   FAST    -> normalize messy BOM rows (cheap, many rows per call)
#   EXTRACT -> turn web evidence into structured, cited facts per part
#   REASON  -> validate alternatives + BOM-level risk briefing
# IDs from the Token Factory catalog. Run `python scripts/check_setup.py` to confirm on your account.
MODEL_FAST = os.getenv("MODEL_FAST", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
MODEL_EXTRACT = os.getenv("MODEL_EXTRACT", "nvidia/nemotron-3-super-120b-a12b")
MODEL_REASON = os.getenv("MODEL_REASON", "nvidia/Nemotron-3-Ultra-550b-a55b")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
CONCURRENCY = int(os.getenv("CONCURRENCY", "6"))
MOCK = os.getenv("MOCK", "0") == "1"
MAX_ROWS = int(os.getenv("MAX_ROWS", "150"))
CACHE_DIR = os.getenv("CACHE_DIR", ".cache")

# A part at or above this risk score gets an alternatives search + Ultra validation.
ALT_THRESHOLD = int(os.getenv("ALT_THRESHOLD", "50"))
