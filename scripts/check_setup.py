"""Run this first: `python scripts/check_setup.py`
Checks your keys, lists the Nemotron model IDs your Token Factory account can see,
makes one tiny call per configured model, and runs one Tavily search."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openai import OpenAI  # noqa: E402
from tavily import TavilyClient  # noqa: E402
from app import config  # noqa: E402

ok = True
print("Token Factory:", config.NEBIUS_BASE_URL)
if not config.NEBIUS_API_KEY or config.NEBIUS_API_KEY.startswith("your_"):
    sys.exit("  ✗ NEBIUS_API_KEY missing in .env")

client = OpenAI(api_key=config.NEBIUS_API_KEY, base_url=config.NEBIUS_BASE_URL)
try:
    ids = [m.id for m in client.models.list().data]
    nem = sorted(i for i in ids if "nemotron" in i.lower())
    print(f"  ✓ {len(ids)} models visible. Nemotron models:")
    for i in nem:
        print("     ", i)
except Exception as e:
    sys.exit(f"  ✗ Could not list models: {e}")

for tier, model in (("FAST", config.MODEL_FAST), ("EXTRACT", config.MODEL_EXTRACT), ("REASON", config.MODEL_REASON)):
    if model not in ids:
        print(f"  ✗ MODEL_{tier}={model} is not in your catalog. Pick one from the list above and set it in .env")
        ok = False
        continue
    try:
        r = client.chat.completions.create(model=model, max_tokens=300,
                                           messages=[{"role": "user", "content": 'Reply with JSON {"ok": true}'}])
        print(f"  ✓ MODEL_{tier} {model} answered: {(r.choices[0].message.content or '').strip()[:80]!r}")
    except Exception as e:
        print(f"  ✗ MODEL_{tier} {model} failed: {e}")
        ok = False

print("Tavily:")
if not config.TAVILY_API_KEY or config.TAVILY_API_KEY.startswith("your_"):
    sys.exit("  ✗ TAVILY_API_KEY missing in .env")
try:
    res = TavilyClient(api_key=config.TAVILY_API_KEY).search("MPU-6050 lifecycle status", max_results=2)
    for r in res.get("results", []):
        print("  ✓", r.get("title"), "—", r.get("url"))
except Exception as e:
    print("  ✗ Tavily search failed:", e)
    ok = False

print("\nAll good. Start the app: uvicorn app.main:app --reload" if ok else "\nFix the ✗ lines above, then run this again.")
