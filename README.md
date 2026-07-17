# Competitor Marketing Intelligence Agent

A conversational AI agent that takes any competitor's name or domain and produces a structured, evidence-grounded brief on their marketing strategy and positioning — built for Rippling's growth/marketing team.

---

## What it does

Given a competitor name (e.g. `Gusto`) and optional domain (`gusto.com`), the agent:

1. **Decides what to research** — a planning step (cheap model) chooses which of 5 sources to check and why. Not a fixed script.
2. **Fetches across 5 public sources** — website/pricing, Meta Ad Library, Google Ads Transparency Center, press/announcements, social media presence.
3. **Adapts mid-run** — if a source comes back empty or failed, a replanning step reasons about it and retries where worthwhile.
4. **Synthesizes a grounded brief** — one frontier-model call produces a markdown brief with a specific, actionable "Relevance to Rippling" section, plus a JSON array of source-cited claims.
5. **Handles follow-ups conversationally** — "dig deeper on their pricing", "run this for Deel", "what are their current ads?" — without re-running the full pipeline.

**Outputs:**
- `output/{competitor}_brief.md` — markdown brief
- `output/{competitor}_output.json` — structured data (sources, claims, confidence levels, timestamps, source URLs)

---

## Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **Note:** `chromadb` downloads a local embedding model (~100MB) on first use. Requires network access and ~30s the first time.

### 2. Configure environment

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=sk-ant-...

SERPAPI_API_KEY=...           # Google Ads Transparency Center — serpapi.com
TAVILY_API_KEY=tvly-...       # Press + social search — app.tavily.com
SCRAPECREATORS_API_KEY=...    # Meta Ad Library — scrapecreators.com

# Model overrides (optional — defaults below are recommended)
CLAUDE_FRONTIER_MODEL=claude-sonnet-4-6
CLAUDE_CHEAP_MODEL=claude-haiku-4-5-20251001
```

All four API keys have free tiers that cover the assignment scope. If any key is missing, that source degrades gracefully — the agent reports the gap and continues.

---

## Running

### Streamlit UI

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Enter a competitor name and optional domain, then interact conversationally with follow-ups.

### CLI

```bash
python flow.py Gusto gusto.com
python flow.py Deel deel.com
python flow.py "Justworks"
```

Prints the brief to stdout and saves files to `output/`.

### Eval harness

```bash
python -m eval.run_eval
```

Runs the agent against Gusto, Deel, and Justworks. Scores each output on 5 rubric checks and writes `output/eval_report.json`.

---

## Architecture

```
clarify        (Haiku)  — asks a question only if the request is genuinely ambiguous
    ↓
plan           (Haiku)  — decides which of 5 sources to check and why
    ↓
research       (code)   — fetches all planned sources
    ↓
check_for_gaps (router) — any EMPTY/FAILED results?
    ├── yes → replan   (Haiku) — reasons about gaps, retries where worthwhile
    └── no  → skip
    ↓
check_volume   (router) — total content > 8,000 tokens?
    ├── yes → embed_and_retrieve  (ChromaDB, local) — top-k relevant chunks
    └── no  → pass content directly
    ↓
synthesize     (Sonnet) — produces brief + grounded claims
    ↓
finalize       (code)   — writes output/ files
```

LLM calls are tiered: Haiku for bounded classification tasks, Sonnet for the single synthesis step that determines output quality. Everything mechanical (fetching, routing, token counting) is plain code.

---

## Sources

| Key | What it covers | Tool |
|-----|---------------|------|
| `website` | Homepage, product pages, pricing | Direct HTTP + BeautifulSoup |
| `meta_ads` | Active Meta/Instagram ad creative | ScrapeCreators (Facebook Ad Library) |
| `google_ads` | Active Google/YouTube ad creative | SerpApi (Ads Transparency Center) |
| `press` | Press releases, announcements, news | Tavily web search |
| `social` | LinkedIn/X content themes | Tavily web search (scoped to social domains) |

---

## Eval rubric

| Check | Type | What it verifies |
|-------|------|-----------------|
| `claims_grounded` | Deterministic | Every claim cites a source that exists in state |
| `source_diversity` | Deterministic | At least 2 distinct source types contributed content |
| `gaps_reported` | Deterministic | EMPTY/FAILED sources are acknowledged in the brief |
| `required_sections` | Deterministic | Brief contains Messaging and Relevance to Rippling sections |
| `rippling_relevance` | LLM-graded (Haiku) | Relevance section is specific and actionable, not generic filler |

---

## Project structure

```
app.py                        Streamlit UI
flow.py                       CrewAI Flow orchestrator + CLI entry point
state.py                      Pydantic state model (AgentState, SourceResult, Claim)
config.py                     Model names (reads from env, sane defaults)

steps/
  clarify.py                  Ambiguity check before research
  plan_research.py            Source selection planning
  run_research.py             Executes the plan (plain code)
  adaptive_replan.py          Mid-run reasoning when sources fail
  volume_router.py            Token-count routing decision
  embed_and_retrieve.py       ChromaDB path for large-footprint competitors
  synthesize.py               Brief generation (frontier model)
  route_followup.py           Classifies conversational follow-ups

tools/
  web_fetch.py                Website + pricing page fetcher
  scrapecreators_facebook.py  Meta Ad Library (two-step: company → ads)
  serpapi_google.py           Google Ads Transparency Center
  press_search.py             Tavily press + social search
  token_counter.py            tiktoken-based volume threshold
  json_parser.py              Robust LLM JSON parser (handles code blocks, partial output)

eval/
  rubric.py                   Scoring rubric (deterministic + LLM-graded checks)
  run_eval.py                 Eval harness (Gusto, Deel, Justworks)

output/                       Generated briefs, JSON files, eval report
```

---

## Cost

A standard run costs under $0.05:

| Step | Model | ~Tokens |
|------|-------|---------|
| Clarify | Haiku | 500 |
| Plan | Haiku | 800 |
| Replan (if triggered) | Haiku | 600 |
| Follow-up routing (if used) | Haiku | 300 |
| **Synthesis** | **Sonnet** | **5,000–7,000** |
