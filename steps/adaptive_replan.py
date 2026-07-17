"""
steps/adaptive_replan.py
=========================
Re-invokes planning judgment mid-run if sources came back empty or failed.
Adapts the research plan based on actual results.
"""

import os

import anthropic

from config import CHEAP_MODEL
from state import AgentState, SourceStatus
from tools.json_parser import parse_json_response

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

REPLAN_SYSTEM_PROMPT = """You are reviewing competitor research results \
partway through a run. Some sources succeeded, some may be empty or failed.

Given what's been found so far, decide:
1. Is there anything worth re-trying or digging into further, given what \
   succeeded?
2. Should any planned-but-not-yet-run source be deprioritized, given the \
   pattern so far?

Be concrete and brief. This reasoning will be shown to the end user as part \
of the final brief's transparency, so write it as you'd want a colleague to \
read it -- not generic filler.

Return ONLY valid JSON, no other text:
  {"reasoning": "<your reasoning, 1-3 sentences>",
   "retry_sources": ["<source names to retry, if any>"],
   "deprioritize_sources": ["<source names to skip if not yet run, if any>"]}
"""


def needs_replan(state: AgentState) -> bool:
    """Returns True only if at least one source came back EMPTY or FAILED.
    This is the cost-control gate -- if every source succeeded cleanly,
    there's no new information to reason about, so we skip the LLM call
    entirely rather than paying for a no-op.
    """
    return any(
        result.status in (SourceStatus.EMPTY, SourceStatus.FAILED)
        for result in state.sources.values()
    )


def adaptive_replan(state: AgentState) -> dict:
    """Runs the mid-run reasoning call. Only call this after run_research()
    and only when needs_replan(state) is True.
    """
    context = f"Competitor: {state.competitor}\n\nResults so far:\n{state.source_summary()}"

    response = client.messages.create(
        model=CHEAP_MODEL,
        max_tokens=250,
        system=REPLAN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": context}],
    )

    text = response.content[0].text.strip()
    result = parse_json_response(text, "adaptive_replan")

    if result is None:
        result = {
            "reasoning": "Unable to parse replan response; proceeding with "
            "existing results as-is.",
            "retry_sources": [],
            "deprioritize_sources": [],
        }

    # Append this reasoning to the planner_reasoning trail rather than
    # overwriting it -- the JSON deliverable should show the FULL decision
    # history (initial plan + any mid-run adaptation), not just the latest
    # thought, since that trail is itself evidence of "a real agentic loop."
    state.planner_reasoning += f"\n\n[Mid-run adaptation] {result.get('reasoning', '')}"

    return result