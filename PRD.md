# PRD: Competitor Marketing Intelligence Agent

|  |  |
| :---- | :---- |
| **Author** | Vikram Murali |
| **One-liner** | A conversational agent that takes any competitor's name or domain and produces a structured, evidence-grounded brief on their marketing strategy and positioning — architected so autonomy is spent only where judgment is genuinely required. |

---

## 1\. Problem

Rippling competes across payroll, HRIS, spend management, IT, and workforce management. Understanding how a competitor markets and positions itself today requires manually checking scattered public sources, and that process doesn't scale across a growing competitor list or stay current as positioning shifts. Marketing and growth teams need a fast, repeatable, evidence-grounded way to answer: *how is this competitor positioning themselves right now, what changed recently, and where's the opening for us.*

## 2\. Goals and non-goals

**Goals**

- Given any competitor name or domain, autonomously research public marketing presence and produce a structured, evidence-backed brief.  
- Make real agentic decisions about what to research deeper and what to skip — genuine autonomy where the task needs judgment.  
- Support a conversational loop: clarifying questions up front, follow-ups after, without re-running the full pipeline.  
- Every claim traceable to a source with a confidence level.  
- Handle source failures gracefully.  
- **Keep the architecture cost-proportional to the task** — spend agent autonomy (and its token cost) only on the two or three steps that genuinely require judgment; everything mechanical is plain code or a single scoped LLM call.  
    
  **Non-goals**  
    
- Not a general-purpose research agent.  
- No access behind logins or attempts to infer product roadmaps.  
- Not a monitoring/scheduling pipeline — on-demand only.  
- No UI required.  
- Not using a vector database as a default — only introduced where content volume genuinely requires it (see §5.4).

  ## 3\. Target user

  A Rippling marketer or growth engineer prepping for a competitive positioning discussion or reacting to a competitor's move, who wants a fast, trustworthy brief without manually checking five tools.

  ## 4\. User stories

- *As a marketer,* I want a structured brief from just a competitor's domain.  
- *As a marketer,* I want to know what sources came up empty, not just what was found.  
- *As a marketer,* I want to ask a follow-up ("dig deeper on pricing") without waiting for a full re-run.  
- *As a marketer,* I want every claim traceable to a real source.  
- *As a marketer,* I want the "what would we exploit" section to be specific, not generic.

  ## 5\. Architecture

  ### 5.1 Orchestration: CrewAI Flow, not a single monolithic Crew

  The initial instinct — one CrewAI Crew with four full agents running sequentially — has a real cost problem: every agent carries its own role/goal/backstory context, and that overhead compounds across a multi-agent chain regardless of whether a given step actually needs autonomous reasoning.  
    
  Instead, the top-level orchestrator is a **CrewAI Flow** — a Python class using @start, @listen, and @router decorators that gives explicit control over sequencing and state. Inside the Flow, each step independently chooses its own execution mode:


| Step | Genuinely needs judgment? | Execution mode |
| :---- | :---- | :---- |
| Fetch website/pricing page | No — mechanical | Plain code, no LLM |
| Call SerpApi (Meta / Google ad data) | No — mechanical | Plain code, no LLM |
| General web/press search | No — mechanical | Plain code, no LLM |
| **Decide what's worth researching deeper / what to skip** | **Yes** | Single LLM call (or small Crew if multi-step reasoning is needed) — cheap/fast model |
| **Clarifying-question generation** | **Yes**, but low-stakes | Single LLM call — cheap/fast model |
| **Synthesis: positioning brief \+ Rippling-relevance section** | **Yes** — the highest-judgment step | Single LLM call — frontier model |
| **Follow-up routing** ("dig deeper on pricing") | **Yes**, but a narrow classification | Single LLM call — cheap/fast model |


  Only two points in the whole pipeline touch a frontier model; everything else is either free (plain code) or running on a cheap, fast model. This is the direct answer to the JD's "knows when to use Kimi vs. Opus" line — the tiering isn't decorative, it's structural.

  ### 5.2 State management: a plain state object, not a vector DB

  The Flow carries a structured state object (a Pydantic model) through the run: {competitor, sources\_checked: {source\_type: status}, claims: \[...\], brief: str}. This is what makes follow-ups cheap — "dig deeper on pricing" doesn't need semantic search, it needs (a) a cheap classification call mapping the follow-up to one of a small fixed set of categories already present in state, and (b) re-invoking only the relevant research/synthesis step, using everything else already in state. No database technology is required for this; the state object persists for the session, and optionally serializes to a JSON file if the CLI needs to resume across separate runs.

  ### 5.3 Source tooling

| Source | Access method | Cost |
| :---- | :---- | :---- |
| Website / pricing pages | Direct fetch (generic HTML extraction, no site-specific logic) | Free |
| Meta Ad Library | ScrapeCreators, two-step lookup (see below) | Free starting credits cover assignment scope |
| Google Ads Transparency Center | SerpApi's dedicated google\_ads\_transparency\_center engine | Free trial tier covers assignment scope |
| Press / announcements | Tavily web search | Free tier |
| Social media | Tavily web search (scoped to LinkedIn, X/Twitter) | Free tier |


  Neither Meta nor Google offers an official commercial API for ad data — this is documented, not an oversight. Both ad sources are accessed through third-party wrappers with confirmed, documented response schemas, rather than raw scraping or an unverified assumption about response shape.


  **Google** uses SerpApi's google\_ads\_transparency\_center engine directly with a single call (text parameter for free-text company search). This is the most-verified integration in the whole system — confirmed against SerpApi's own docs, an official launch blog post, and multiple independent working code samples, all agreeing on the same response shape (ad\_creatives, a flat list, each with advertiser, format, total\_days\_shown).


  **Meta** uses ScrapeCreators' purpose-built Facebook Ad Library API, via a deliberate two-step lookup rather than a single free-text call:


1. search\_companies(name) — resolves a company name to a Facebook page\_id.  
2. get\_company\_ads(page\_id) — fetches that exact page's active ad creative.  
     
   This two-call design is a considered tradeoff, not an oversight. The ads endpoint also accepts a raw companyName directly, which would cut the call count in half — but doing so hands disambiguation to an opaque, unverifiable process inside their API. A company search can return multiple plausible matches for one name (e.g. querying "Nike" returns both "Nike" and "Nike Football" as candidates, with "Nike Football" actually having the higher like count). Resolving the match in our own code — with an explicit, testable ranking (exact name match, then verification status, then like count) — means we can see and defend exactly which page was selected, instead of trusting a black box that could silently return ad data for a sub-brand or fan page. This was verified with a unit test proving the ranking correctly prefers the exact-name match over the higher-engagement decoy.  
     
   We chose different providers for Meta and Google deliberately, not for lack of a single-vendor option — ScrapeCreators does offer a Google Ad Library section too. The principle applied: each source uses whichever integration has the most-verified, best-documented schema for that specific source, rather than optimizing for fewest vendors. SerpApi's Google integration was independently confirmed multiple times before ScrapeCreators' Google offering was discovered, so there was no reason to trade a fully-verified integration for an unverified one just for vendor consistency.

   ### 5.4 The content-volume threshold — when embeddings actually get used

   For most competitors, total scraped raw content (site copy, a handful of press hits, ad creative text) is small enough to pass directly into the synthesis prompt — simple, cheap, and still fully grounded, since the model is generating from real retrieved text, just not via embedding search.  
     
   For a competitor with a very large public footprint (many product lines, years of press, dozens of active campaigns — think a Microsoft or Google-scale competitor), raw content could exceed what's useful to pass in one prompt, and most of it would be irrelevant noise diluting the signal.  
     
   **Design rule, implemented as an explicit decision point in the Flow, not a hardcoded assumption:**  
     
   if total\_token\_count(retrieved\_content) \< THRESHOLD:  
     
       pass content directly into synthesis prompt  
     
   else:  
     
       chunk \+ embed content → retrieve top-k relevant chunks → pass those into synthesis  
     
   This is the one legitimate use of a vector store in this system, and it's conditional, not default — the architecture scales its own strategy to the size of the actual problem, which is directly demonstrable in the Loom as a real design decision rather than an assumption.

   ### 5.5 Grounding and confidence

   Every claim in the synthesized brief is generated with an explicit source citation and confidence level, following the same anti-hallucination pattern used in prior agent work: the model is instructed to ground every claim in retrieved content, not model memory, and a claim with no traceable source is dropped rather than kept.

   ## 6\. Requirements

   ### Core agent behavior

| ID | Requirement | Priority |
| :---- | :---- | :---- |
| R1 | Accept competitor name/domain; not hardcoded to any one company | Must |
| R2 | Research at minimum: ads (Meta \+ Google), website/pricing, social, recent press | Must |
| R3 | Real agentic decision at the research-planning step — not a fixed sequence | Must |
| R4 | Clarifying questions when genuinely ambiguous | Must |
| R5 | Conversational follow-ups without full re-run | Must |
| R6 | Re-run for a new competitor within the same session | Should |
| R7 | Graceful degradation when a source is empty/fails, explicitly noted in output | Must |
| R8 | Cost-proportional execution — LLM calls only where judgment is required, plain code elsewhere | Must |

   ### Output

| ID | Requirement | Priority |
| :---- | :---- | :---- |
| O1 | Markdown brief: messaging themes, positioning, recent changes, Rippling-relevance section | Must |
| O2 | JSON: sources checked, claims, confidence, timestamps | Must |
| O3 | Every markdown claim traceable to a JSON source | Must |
| O4 | Rippling-relevance section is specific and actionable | Must |

   ### Evaluation

| ID | Requirement | Priority |
| :---- | :---- | :---- |
| E1 | Rubric-scored eval across 2-3 competitors: source diversity, groundedness, relevance-section specificity | Must |
| E2 | At least one run demonstrating graceful degradation on an empty source | Must |
| E3 | Documented model-tiering rationale (§5.1 table) | Should |
| E4 | Per-run token/cost report, demonstrating the cost-proportional design actually holds in practice | Should |

   ## 7\. Worked end-to-end example flows

   ### Flow A — Standard case: small public footprint (e.g., a payroll-space startup)

   User: "Research Gusto for me."

   

   \[Flow.start\] → Clarifying-question step (cheap model)

   

     → Ambiguity check: is there a clear default scope? Yes (SMB payroll is well-known).

   

     → No clarifying question needed; proceeds directly.

   

   \[Flow.listen: research\_plan\] → Planner step (cheap model)

   

     → Decision: check all 4 source types, no strong reason yet to skip any.

   

     → Plan: \[website/pricing, Meta ads, Google ads, press/social\]

   

   \[Flow.listen: run\_research\] → Plain code, parallel where possible

   

     → Website fetch: SUCCESS (pricing page, product pages retrieved)

   

     → SerpApi Meta: SUCCESS (12 active ad creatives found)

   

     → SerpApi Google ATC: SUCCESS (8 active ads found)

   

     → Web search (press): SUCCESS (3 relevant recent articles)

   

     → State updated: sources\_checked \= {website: ok, meta: ok, google: ok, press: ok}

   

   \[Flow.listen: check\_volume\] → Plain code

   

     → Total retrieved content: \~4,200 tokens → below threshold

   

     → Route: direct context-stuffing, skip embedding step

   

   \[Flow.listen: synthesize\] → Frontier model, single call

   

     → Input: all raw retrieved content \+ explicit grounding instruction

   

     → Output: markdown brief with cited claims \+ JSON with per-claim source/confidence

   

   \[Flow.end\] → Present brief \+ JSON to user

   ### Flow B — Graceful degradation: a source comes up empty

   User: "Research \[small regional competitor\] for me."

   

   \[Flow.listen: run\_research\]

   

     → Website fetch: SUCCESS

   

     → SerpApi Google ATC: EMPTY — no verified advertiser profile found for this domain

   

     → SerpApi Meta: SUCCESS (2 ad creatives found)

   

     → Web search (press): SUCCESS (1 article, 8 months old)

   

   \[Flow.listen: adaptive\_replan\] → Planner step re-invoked (cheap model)

   

     → Reasoning surfaced: "No Google ATC profile — likely unverified advertiser or

   

        not running Search/YouTube ads. Deprioritizing further Google-specific

   

        research. Meta presence found — worth checking Meta for recency signal

   

        instead of assuming inactive."

   

     → Plan adjusted: skip further Google-specific queries, weight Meta findings higher

   

   \[Flow.listen: synthesize\]

   

     → Brief explicitly states: "No active Google Ads Transparency Center presence

   

        found for this domain as of \[date\] — competitor may not be a verified

   

        advertiser on Google, or may not run Search/Display/YouTube ads. Assessment

   

        below is grounded primarily in Meta ad data and available press."

   

     → JSON: google\_ads: {status: "not\_found", confidence: n/a, note: "..."}

   

   \[Flow.end\] → User receives an honest, gap-flagged brief, not a silently incomplete one

   ### Flow C — Conversational follow-up, no full re-run

   User: "Now dig deeper on their pricing."

   

   \[Flow.listen: route\_followup\] → Classification call (cheap model)

   

     → Input: user follow-up text \+ list of known categories from current state

   

        (pricing, positioning, ads, social, recent\_changes)

   

     → Output: category \= "pricing"

   

   \[Flow.listen: targeted\_research\] → Plain code

   

     → Checks state: was pricing page already fetched? Yes, but only top-level.

   

     → Runs a scoped, deeper fetch: full pricing page \+ any linked plan-comparison page

   

     → State updated: sources\_checked.website.pricing\_depth \= "deep"

   

   \[Flow.listen: targeted\_synthesis\] → Frontier model, single call

   

     → Input: ONLY the new pricing content \+ prior brief's pricing section (not the

   

        entire original research set)

   

     → Output: updated pricing section, merged into existing brief

   

   \[Flow.end\] → User receives updated brief; ads/social/press sections untouched,

   

     no re-fetch, no re-synthesis of unrelated sections

   ### Flow D — Large public footprint: the embedding threshold triggers

   User: "Research \[a large, multi-product competitor\] for me."

   

   \[Flow.listen: run\_research\]

   

     → Website fetch: SUCCESS — but multiple product lines, large site

   

     → SerpApi Meta: SUCCESS — 40+ active ad creatives across campaigns

   

     → SerpApi Google ATC: SUCCESS — 60+ active ads

   

     → Web search (press): SUCCESS — 15+ relevant articles over the past year

   

   \[Flow.listen: check\_volume\] → Plain code

   

     → Total retrieved content: \~38,000 tokens → ABOVE threshold

   

   \[Flow.listen: chunk\_and\_embed\] → Triggered only here

   

     → Content chunked, embedded, stored in a lightweight vector index for this run

   

     → Retrieval query: "messaging themes, positioning, recent campaign changes

   

        relevant to HR/IT/payroll/workforce management"

   

     → Top-k relevant chunks retrieved (\~5,000 tokens), rest discarded for this run

   

   \[Flow.listen: synthesize\] → Frontier model, single call

   

     → Input: retrieved top-k chunks only, not the full 38K tokens

   

     → Output: brief grounded in the highest-relevance subset, not diluted by

   

        off-topic product-line noise

   ## 8\. Cost estimate (per full run, standard case)

| Step | Model | Est. tokens | Est. cost |
| :---- | :---- | :---- | :---- |
| Clarifying-question check | Cheap/fast | \~500 | Fraction of a cent |
| Research planner | Cheap/fast | \~800 | Fraction of a cent |
| Adaptive replan (if triggered) | Cheap/fast | \~600 | Fraction of a cent |
| Synthesis | Frontier | \~4,000-6,000 | A few cents |
| Follow-up routing (if used) | Cheap/fast | \~300 | Fraction of a cent |
| **Total, standard case** | — | — | **Well under $0.05 per competitor** |

   

   Running this across the 2-3 competitors needed for the eval harness costs pennies, not dollars — worth stating explicitly in the Loom, since it's a direct, quantified answer to "why should we trust this scales."

   ## 9\. Risks and open questions

- **Source fragility.** SerpApi wraps a scraped interface; could break. *Mitigation: explicit fallback path, treated as an expected failure mode (Flow B), not a crash.*  
- **Verified-advertiser gaps.** Many competitors simply won't have ad-library data. *Mitigation: this is a first-class case in the eval harness, not an edge case.*  
- **Threshold tuning.** The content-volume cutoff (§5.4) is a judgment call I'd validate empirically against a few real competitors rather than pick arbitrarily.  
- **Follow-up scope creep.** A follow-up that doesn't map cleanly to an existing category needs a fallback — likely "re-run full research" as the safe default when classification confidence is low.

