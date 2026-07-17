"""
Decides which sources are worth checking for a competitor. Called before
research starts (initial plan) and mid-run if sources fail (adaptive replan).
"""

import json
import os

import anthropic

from config import CHEAP_MODEL
from state import AgentState
from tools.json_parser import parse_json_response

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

PLAN_SYSTEM_PROMPT = """You are planning research for a competitor marketing \
analysis. You have five possible source types to check:

- website: their site, product pages, pricing
- meta_ads: active ad creative on Meta (Facebook/Instagram)
- google_ads: active ad creative via Google Ads Transparency Center
- press: recent press releases and news announcements
- social: social media content themes (LinkedIn, X/Twitter) via web search

Decide which sources are worth checking for this competitor, and briefly \
explain your reasoning. Default to checking all five unless you have a \
specific reason not to (e.g. the user's request explicitly narrows scope).

Return ONLY valid JSON, no other text:
  {"sources_to_check": ["website", "meta_ads", "google_ads", "press", "social"],
   "reasoning": "<one or two sentences>"}
"""


def plan_research(competitor: str, user_clarifications: dict[str, str]) -> dict:
    """Initial research plan, called once at the start of a run (PRD Flow A).

    Returns {"sources_to_check": [...], "reasoning": str}.
    """
    context = f"Competitor: {competitor}"
    if user_clarifications:
        context += f"\nClarifications from user: {json.dumps(user_clarifications)}"

    response = client.messages.create(
        model=CHEAP_MODEL,
        max_tokens=250,
        system=PLAN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": context}],
    )

    text = response.content[0].text.strip()
    parsed = parse_json_response(text, "plan_research")

    if parsed is None:
        # Fail safe: if parsing fails, default to checking everything rather
        # than blocking the run -- a conservative default beats a crash.
        return {
            "sources_to_check": ["website", "meta_ads", "google_ads", "press", "social"],
            "reasoning": "Defaulted to checking all sources (planner response "
            "could not be parsed).",
        }

    return parsed


def apply_plan_to_state(state: AgentState, plan: dict) -> None:
    """Writes the plan's output into AgentState so later steps (and the final
    JSON deliverable) can see what was decided and why.
    """
    state.research_plan = plan.get("sources_to_check", [])
    state.planner_reasoning = plan.get("reasoning", "")