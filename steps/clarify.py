"""
Decides whether to ask a clarifying question before starting research.
"""

import os

import anthropic

from config import CHEAP_MODEL
from tools.json_parser import parse_json_response

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

CLARIFY_SYSTEM_PROMPT = """You help decide whether a competitor marketing \
research request needs a clarifying question before starting.

Only ask a clarifying question if the request is GENUINELY ambiguous in a \
way that would change what you research. Most requests are NOT ambiguous --
a well-known company name is enough to proceed. Do not ask a question just \
to seem thorough.

Real reasons to ask:
- The company name is ambiguous (multiple companies share the name)
- The user's intent is unclear (e.g. "check out Stripe" -- payments \
  processing broadly, or a specific product line?)
- Segment focus genuinely changes the research (e.g. a company that sells \
  very differently to enterprise vs. SMB)

Return ONLY valid JSON, no other text:
  {"needs_clarification": true, "question": "<the question to ask>"}
  or
  {"needs_clarification": false, "question": null}
"""


def check_needs_clarification(user_request: str) -> dict:
    """Returns {"needs_clarification": bool, "question": str | None}.

    Called once, early in the Flow, before any research begins (PRD Flow A:
    "Ambiguity check... No clarifying question needed; proceeds directly.").
    """
    response = client.messages.create(
        model=CHEAP_MODEL,
        max_tokens=150,
        system=CLARIFY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_request}],
    )

    text = response.content[0].text.strip()
    parsed = parse_json_response(text, "clarify")

    if parsed is None:
        # If the model doesn't return clean JSON, fail safe: proceed without
        # a clarifying question rather than blocking the whole run on a
        # parsing error for what's meant to be a low-stakes check.
        return {"needs_clarification": False, "question": None}

    return parsed