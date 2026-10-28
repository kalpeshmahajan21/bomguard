# BOMGuard

**Find the dead parts in your bill of materials before your production run does.**

Upload a BOM CSV. An agent checks every part against the live web for lifecycle, stock and RoHS status, finds replacements for the risky ones, and writes a briefing on what to fix first. Every claim links to the page it came from.

Built for the Nebius x NVIDIA Global AI Hackathon (Best Apps and Agents track).

## The problem

A hardware product is built from 50–300 parts. If one of them goes end-of-life or out of stock after the design is frozen, the team faces a board respin, a delayed launch and broker prices with counterfeit risk. Large companies pay for enterprise component-intelligence tools. Startups, small manufacturers and student teams check distributor pages by hand, one part at a time, and still miss things.

BOMGuard does that check in minutes, for the whole BOM, with sources.

## How it works

```
BOM CSV ─▶ Nemotron Nano ─▶ Tavily search ─▶ Nemotron Super ─▶ risk score ─▶ (risky?) Tavily search ─▶ Nemotron Ultra ─▶ briefing
          clean rows         live web per part   cited facts       arithmetic       find successors          judge fit, write brief
```

| Step | Model / tool | Why this tier |
|---|---|---|
| Clean the BOM | **Nemotron 3 Nano** (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`) | One cheap, fast call per 40 rows. Normalizes manufacturer names, assigns categories, flags generic passives so no searches are wasted on a 10k resistor. |
| Research each part | **Tavily** search (advanced) | Manufacturer pages, distributors and notices for lifecycle, stock and compliance. |
| Extract facts | **Nemotron 3 Super** (`nvidia/nemotron-3-super-120b-a12b`) | Many parallel calls that need accurate reading of messy web text. Every claim must cite a source index; uncited claims are dropped in code. "Unknown" is a valid answer. |
| Find replacements | **Tavily** + **Nemotron 3 Ultra** (`nvidia/Nemotron-3-Ultra-550b-a55b`) | Only for parts scoring 50+. Ultra judges each candidate as drop-in, pin-compatible or functional (PCB change) and lists what an engineer must check. |
| Briefing | **Tavily news** + **Nemotron 3 Ultra** | Recent supply-chain news for the riskiest vendors, plus a prioritized action list. |

Design choices that make results trustworthy:

- **The model reports facts; code does the math.** The risk score is plain arithmetic on lifecycle, stock and RoHS (`app/agent.py: score`), so every number is explainable.
- **No source, no claim.** Claims that don't point at a retrieved page are discarded, and a part with no surviving citations is marked low confidence.
- **Replacements must appear in the sources.** Ultra may not invent part numbers.
- **Credits are spent where risk is.** Generic passives are skipped, the alternatives search only runs for risky parts, and results are cached on disk.

The UI shows live usage per tier (calls, tokens) and Tavily searches/sources, streamed over Server-Sent Events as each part finishes.

## Where Nebius Token Factory helped

All model calls go through Token Factory's OpenAI-compatible endpoint, so one client serves three Nemotron sizes. Parts are researched in parallel (`CONCURRENCY`, default 6), and Token Factory's throughput keeps a 15-part BOM to roughly a minute end to end.

## Run it

Requires Python 3.11+.

```bash
git clone <this repo> && cd bomguard
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then paste your NEBIUS_API_KEY and TAVILY_API_KEY
python scripts/check_setup.py   # verifies keys and lists the Nemotron IDs on your account
uvicorn app.main:app --reload
```

Open http://localhost:8000 and click **Check the sample drone flight-controller BOM**, or drop in your own CSV.

No keys yet? `MOCK=1 uvicorn app.main:app` runs the full UI with clearly labelled demo data.

### BOM format

Any CSV with a header row and a part-number column. Recognized headers include `MPN`, `Manufacturer Part Number`, `Mfr Part #`, `Manufacturer`, `Description`/`Value`, `Qty`, `Designator`/`Reference`. KiCad, Altium and Eagle exports work as-is.

### Docker

```bash
docker build -t bomguard . && docker run -p 8000:8000 --env-file .env bomguard
```

## Project layout

```
app/main.py       FastAPI routes, SSE stream, CSV/Markdown report export
app/bom.py        CSV parsing, header detection, Nemotron Nano normalization
app/agent.py      per-part pipeline, scoring, Ultra alternatives + briefing
app/research.py   Tavily search wrappers with disk cache
app/llm.py        Token Factory client, JSON extraction, per-tier usage counters
app/mock.py       demo-data mode for UI work without keys
static/           front end (vanilla HTML/CSS/JS)
samples/          example BOM
```

## Roadmap

- Scheduled re-scans of released BOMs with Nebius Serverless Jobs, and alerts when a part's status changes
- Direct KiCad/Altium plugin
- Cross-checking stock and price across distributors

## License

MIT
