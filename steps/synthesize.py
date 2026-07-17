"""
Takes retrieved raw content and produces a markdown brief (messaging, positioning,
recent changes, Rippling relevance) plus a list of grounded claims with sources.
"""

import json
import os

import anthropic

from config import FRONTIER_MODEL
from state import AgentState, Claim
from tools.json_parser import parse_json_response

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYNTHESIS_SYSTEM_PROMPT = """You are a competitive marketing analyst \
producing a brief for Rippling's growth/marketing team.

ABOUT RIPPLING:
Rippling is a workforce management platform unifying HR, IT, and Finance \
around a single employee record -- payroll, benefits, device/app \
provisioning, spend management, EOR/PEO, across 30+ products. Its core \
differentiator is that competitors bolt separate tools together, while \
Rippling's products share one underlying data foundation, which is what \
makes deeper automation and AI features possible.

Rippling's competitive position:
- Strong and growing in the US-first SMB-to-mid-market segment (recently \
  became the #1 mid-market payroll provider while continuing to lead SMB).
- Its known relative weakness is global-first hiring: competitors like \
  Deel and Remote lead on international EOR/global payroll breadth, since \
  Rippling built US-first and expanded globally after.
- It competes against point solutions (Gusto, BambooHR, Paylocity -- \
  simpler, narrower) and enterprise incumbents (Workday, ADP -- broader \
  but harder to implement and less unified).
- Its GTM thesis: unified first-party data (product usage signals, cross- \
  product triggers) is a moat competitors without that data foundation \
  can't replicate.

When identifying what Rippling's marketing could exploit, ground it in \
this actual competitive position -- e.g. does the competitor's messaging \
expose a gap in global reach, unification, or breadth that Rippling's \
positioning already answers? A generic "improve your ads" observation is \
not useful; a specific gap tied to Rippling's real differentiators is.

You will be given raw research content pulled from a competitor's public \
website, ad libraries, and press coverage. Some sources may be marked EMPTY \
or FAILED -- this means that source had no data, not that you should invent \
data to fill the gap.

CRITICAL GROUNDING RULE: every factual claim you make must be traceable to \
the raw content you were given. If you don't have evidence for something, \
say so explicitly rather than guessing. Never state a claim you cannot point \
to a specific source for.

Produce two things:

1. A markdown brief with these exact sections:
   ## Messaging & Positioning Themes
   ## What's Changed Recently  (new campaigns, new ICPs being targeted, messaging pivots)
   ## Relevance to Rippling
   (This last section must be SPECIFIC and ACTIONABLE -- name a concrete \
   angle Rippling's marketing could exploit, tied to Rippling's actual \
   competitive position above, not a generic restatement of the \
   competitor's positioning. This is the most important section.)

2. A JSON array of claims, one per factual statement in the brief, in \
   this exact shape:
   [{"text": "<the claim>", "source": "<website|meta_ads|google_ads|press|social>",
     "confidence": <0.0-1.0>, "section": "<messaging|positioning|
     recent_changes|rippling_relevance>"}]

Return your response as:
---MARKDOWN---
<the markdown brief>
---CLAIMS---
<the JSON array, nothing else>
"""


def _build_research_context(state: AgentState) -> str:
    """Formats everything gathered so far into the input the model reads.
    Explicitly includes EMPTY/FAILED sources with their notes, so the model
    knows what it doesn't have -- this is what lets the brief say "no
    verified Google Ads presence found" instead of silently omitting it
    (PRD Flow B).
    """
    sections = [f"Competitor: {state.competitor}"]

    for name, result in state.sources.items():
        sections.append(f"\n--- Source: {name} ({result.status.value}) ---")
        if result.raw_content:
            sections.append(result.raw_content)
        else:
            sections.append(f"[No content. Note: {result.note or 'n/a'}]")

    return "\n".join(sections)


def synthesize(state: AgentState) -> None:
    """Runs the single frontier-model synthesis call and writes results back
    into state.brief_markdown and state.claims. Mutates state in place.
    """
    research_context = _build_research_context(state)

    response = client.messages.create(
        model=FRONTIER_MODEL,
        max_tokens=4096,
        system=SYNTHESIS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": research_context}],
    )

    raw_text = response.content[0].text

    markdown, claims_raw = _parse_synthesis_output(raw_text)

    state.brief_markdown = markdown
    state.claims = _parse_claims(claims_raw)


def _parse_synthesis_output(raw_text: str) -> tuple[str, str]:
    """Splits the model's response into the markdown and JSON halves using
    the delimiters specified in the system prompt.
    """
    if "---MARKDOWN---" in raw_text and "---CLAIMS---" in raw_text:
        markdown_part = raw_text.split("---MARKDOWN---")[1].split("---CLAIMS---")[0]
        claims_part = raw_text.split("---CLAIMS---")[1]
        return markdown_part.strip(), claims_part.strip()

    # Fail-safe: if the model didn't follow the delimiter format exactly,
    # treat the whole response as markdown and return no claims rather than
    # crashing the run. A brief with no claims is still useful; a crashed
    # run is not.
    return raw_text.strip(), "[]"


def _parse_claims(claims_json: str) -> list[Claim]:
    """Parses the claims JSON into validated Claim objects. Any individual
    claim that fails Pydantic validation (e.g. confidence out of 0-1 range,
    missing field) is dropped rather than failing the whole batch.
    """
    # Try direct parse first; fall back to parse_json_response which handles
    # markdown code blocks (```json...```) that the model sometimes wraps claims in.
    try:
        raw_claims = json.loads(claims_json)
    except json.JSONDecodeError:
        raw_claims = parse_json_response(claims_json, "claims")

    if not isinstance(raw_claims, list):
        return []

    claims = []
    for item in raw_claims:
        try:
            claims.append(Claim(**item))
        except Exception:
            continue

    return claims