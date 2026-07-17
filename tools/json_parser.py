"""
tools/json_parser.py
=====================
Robust JSON parser for LLM responses. Handles wrapped code blocks, extra
whitespace, and partial outputs without crashing — logs issues and returns
a sensible fallback instead of silently using defaults.

Used by all cheap-model steps (plan_research, adaptive_replan, clarify,
route_followup) so unparseable responses fail gracefully and are logged.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


def parse_json_response(text: str, context: str = "") -> dict | None:
    """
    Parse JSON from LLM response text, handling common wrapping patterns.

    Tries in order:
    1. Direct json.loads() on stripped text
    2. Extract from ```json...``` code block
    3. Extract from ```...``` code block
    4. Return None if all fail (caller provides fallback)

    Args:
        text: raw response.content[0].text from Anthropic API
        context: brief description for logging (e.g. "plan_research response")

    Returns:
        Parsed dict on success, None on failure (log a warning, let caller fallback)
    """
    if not text or not text.strip():
        logger.warning(f"Empty text in {context}")
        return None

    text = text.strip()

    # Try 1: Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try 2: Extract from ```json...``` block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try 3: Find the first { and last } and try that span
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        try:
            candidate = text[first_brace : last_brace + 1]
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # All strategies failed
    logger.warning(
        f"Could not parse JSON from {context}. "
        f"Text (first 100 chars): {text[:100]}"
    )
    return None
