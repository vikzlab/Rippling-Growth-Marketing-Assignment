# Competitor Marketing Intelligence Agent

A conversational AI agent that takes any competitor's name or domain and produces a structured, evidence-grounded brief on their marketing strategy and positioning — built for Rippling's growth/marketing team.

## What it does

Given a competitor (e.g. "Gusto" or "deel.com"), the agent:

1. **Plans** which sources to check and why (cheap model, real decision — not a fixed script)
2. **Researches** across five public sources: website/pricing, Meta Ad Library, Google Ads Transparency Center, press/announcements, and social media presence
3. **Adapts mid-run** if a source comes back empty or failed — reasons about it, retries where worthwhile
4. **Synthesizes** a grounded markdown brief with a specific, actionable "Relevance to Rippling" section
5. **Handles follow-ups** conversationally ("dig deeper on pricing", "run this for Deel") without re-running the whole pipeline

Outputs: a markdown brief + a structured JSON file with sources, claims, confidence levels, and timestamps.

---

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd Rippling-Growth-Marketing-Assignment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **Note:** `chromadb` downloads a local sentence-transformer embedding model on first use (~100MB). This happens automatically but requires network access and takes ~30 seconds the first time.

### 2. Configure API keys

Copy `.env.example` to `.env` (or create `.env` directly) and fill in your keys:

```env
ANTHROPIC_API_KEY=sk-ant-...

SERPAPI_API_KEY=...          # google_ads source — serpapi.com free trial
TAVILY_API_KEY=tvly-...      # press + social sources — app.tavily.com free tier
SCRAPECREATORS_API_KEY=...   # meta_ads source — scrapecreators.com free credits

# Model overrides (optional — defaults shown below are recommended)
CLAUDE_FRONTIER_MODEL=claude-sonnet-4-6
CLAUDE_CHEAP_MODEL=claude-haiku-4-5-20251001
```

All four API keys are required for full coverage. If any is missing, that source degrades gracefully — the agent reports the gap and continues with what it has.

---

## Running the agent

### Streamlit UI (recommended for the demo)

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Enter a competitor name and optional domain, then interact conversationally.

### CLI

```bash
python flow.py "Gusto" "gusto.com"
# or
python flow.py "Deel"
```

Outputs the brief to stdout and writes files to `output/`.

---

## Running the eval harness

```bash
python -m eval.run_eval
```

Runs the agent against three test competitors (Gusto, Deel, Justworks), scores each output against the rubric, and writes a full scorecard to `output/eval_report.json`.

The rubric checks:
- **Claims grounded**: every claim cites a source that actually exists in state
- **Source diversity**: at least 2 distinct source types contributed content
- **Gaps reported**: any EMPTY/FAILED source is explicitly acknowledged in the brief
- **Required sections**: Messaging and Relevance to Rippling sections are present
- **Rippling relevance quality**: LLM-graded — is the section specific and actionable, or generic filler?

---

## Architecture

```
clarify (cheap model)
    ↓
plan_research (cheap model) — decides which of 5 sources to check
    ↓
run_research (plain code) — fetches all planned sources in parallel
    ↓
[router: any EMPTY/FAILED?]
    ├─ yes → adaptive_replan (cheap model) → retries recommended sources
    └─ no  → skip (save the LLM call)
    ↓
[router: total content tokens > 8,000?]
    ├─ yes → embed_and_retrieve (local ChromaDB) → top-k relevant chunks
    └─ no  → pass content directly
    ↓
synthesize (frontier model) — the one high-judgment call
    ↓
finalize (plain code) — writes output/ files
```

**Why this shape:** Only two steps touch a frontier model. Everything mechanical is plain code. The architecture is cost-proportional — each step uses the minimum required capability. See `PRD.md` §5.1 for the full rationale.

---

## Sources

| Source | Tool | What it covers |
|--------|------|----------------|
| `website` | Direct HTTP fetch + BeautifulSoup | Homepage, product pages, pricing |
| `meta_ads` | ScrapeCreators (Facebook Ad Library) | Active Meta/Instagram ad creative |
| `google_ads` | SerpApi (Google Ads Transparency Center) | Active Google/YouTube ad creative |
| `press` | Tavily web search | Recent press releases, announcements, news |
| `social` | Tavily web search | LinkedIn/X content themes and presence |

---

## Project structure

```
app.py                  # Streamlit UI
flow.py                 # CrewAI Flow orchestrator
state.py                # Pydantic state object (single source of truth)
config.py               # Model names and env var overrides

steps/
  clarify.py            # Ambiguity check before research
  plan_research.py      # Decides which sources to check
  run_research.py       # Executes the plan (plain code)
  adaptive_replan.py    # Mid-run reasoning when sources fail
  volume_router.py      # Token-count routing decision
  embed_and_retrieve.py # ChromaDB path for large-footprint competitors
  synthesize.py         # Final brief generation (frontier model)
  route_followup.py     # Classifies conversational follow-ups

tools/
  web_fetch.py          # Website + pricing page fetcher
  scrapecreators_facebook.py  # Meta Ad Library
  serpapi_google.py     # Google Ads Transparency Center
  press_search.py       # Tavily press + social search
  token_counter.py      # tiktoken-based routing threshold

eval/
  rubric.py             # Scoring rubric (deterministic + LLM-graded checks)
  run_eval.py           # Eval harness across test competitors

output/                 # Generated briefs and JSON files
```

---

## Cost estimate

A standard run costs well under $0.05:

| Step | Model | Est. tokens |
|------|-------|-------------|
| Clarify | Haiku | ~500 |
| Plan | Haiku | ~800 |
| Adaptive replan (if triggered) | Haiku | ~600 |
| Follow-up routing (if used) | Haiku | ~300 |
| **Synthesis** | **Sonnet** | **~5,000–7,000** |
