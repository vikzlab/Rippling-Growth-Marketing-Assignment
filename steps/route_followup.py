"""
steps/route_followup.py
========================
Handles conversational follow-ups without re-running the whole pipeline
(assignment requirement: "handle follow-up requests"). This is PRD Flow C:
"Now dig deeper on their pricing" maps to a category, which then triggers a
SCOPED re-run of just that part of the system.

WHY THIS IS A CLASSIFICATION PROBLEM, NOT A SEMANTIC SEARCH PROBLEM: the
category list is small and known in advance (state.known_followup_categories),
so this is a bounded multiple-choice decision, not an open-ended retrieval
task -- no vector DB needed here, just a cheap-model classification call.

WHY CHEAP MODEL: classifying a short user message against ~6 known
categories doesn't need frontier-level reasoning.
"""

import json
import os

import anthropic

from config import CHEAP_MODEL
from state import AgentState

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

ROUTE_SYSTEM_PROMPT = """A user is following up on a competitor research \
brief. Classify their request into exactly one of these known categories:

{categories}

If the request is a new competitor entirely (e.g. "run this again for \
Gusto"), return category "new_competitor" instead.

If the request doesn't clearly map to any category, return "unclear" -- do \
not guess.

Return ONLY valid JSON, no other text:
  {{"category": "<one of the categories above, or new_competitor, or unclear>",
   "new_competitor_name": "<only if category is new_competitor, else null>"}}
"""


def route_followup(state: AgentState, user_message: str) -> dict:
    """Classifies a follow-up message against the known category list.

    Returns {"category": str, "new_competitor_name": str | None}.

    The caller (flow.py) is responsible for deciding what to actually do
    with "unclear" -- per PRD Section 9 ("Follow-up scope creep"), the safe
    default for low-confidence classification is to ask the user to clarify
    rather than silently guessing which section they meant.
    """
    categories_list = "\n".join(f"- {c}" for c in state.known_followup_categories)
    system_prompt = ROUTE_SYSTEM_PROMPT.format(categories=categories_list)

    response = client.messages.create(
        model=CHEAP_MODEL,
        max_tokens=150,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Fail safe: an unparseable classification is treated the same as
        # "unclear" -- ask, don't guess.
        result = {"category": "unclear", "new_competitor_name": None}

    # Log every follow-up exchange into state, both for the conversational
    # UI (app.py can render this as chat history) and as part of the
    # transparency trail in the final JSON deliverable.
    state.conversation_log.append(
        {"user_message": user_message, "routed_category": result.get("category", "unclear")}
    )

    return result