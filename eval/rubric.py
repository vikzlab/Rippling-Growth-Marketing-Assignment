"""
Scoring rubric for evaluating competitor briefs.
Deterministic checks: grounding, diversity, gaps, sections.
LLM-graded check: Relevance to Rippling section specificity.
"""

import json
import os

import anthropic

from config import CHEAP_MODEL
from state import AgentState, SourceStatus
from tools.json_parser import parse_json_response


# ---------------------------------------------------------------------- #
# DETERMINISTIC CHECKS (no LLM -- objective, mechanical)
# ---------------------------------------------------------------------- #

def check_claims_are_grounded(state: AgentState) -> dict:
    """Every claim must cite a source that's in the known set. A claim citing
    a source that doesn't exist (or citing nothing) is an ungrounded claim --
    exactly the hallucination failure mode the system is designed to prevent.
    """
    if not state.claims:
        return {"pass": False, "score": 0.0, "detail": "No claims produced at all."}

    valid_sources = set(state.sources.keys())
    grounded = [c for c in state.claims if c.source in valid_sources]
    ratio = len(grounded) / len(state.claims)
    return {
        "pass": ratio == 1.0,
        "score": ratio,
        "detail": f"{len(grounded)}/{len(state.claims)} claims cite a valid source.",
    }


def check_source_diversity(state: AgentState) -> dict:
    """A good brief draws on multiple source types, not just one. This checks
    how many DISTINCT sources actually contributed successful content -- a
    brief built entirely off the website alone is weaker than one triangulated
    across website + ads + press.
    """
    successful = [
        name for name, r in state.sources.items() if r.status == SourceStatus.SUCCESS
    ]
    count = len(successful)
    # 2+ successful distinct sources is a reasonable bar for "diverse."
    return {
        "pass": count >= 2,
        "score": min(count / 3.0, 1.0),  # 3+ sources = full marks
        "detail": f"{count} source type(s) contributed content: {successful}",
    }


def check_gaps_are_reported(state: AgentState) -> dict:
    """Graceful degradation check (assignment requirement E2). If any source
    came back EMPTY or FAILED, the brief should ACKNOWLEDGE it rather than
    silently omit it. We verify the brief text actually references the gap,
    so a missing source is transparent to the reader, not hidden.
    """
    gap_sources = [
        name
        for name, r in state.sources.items()
        if r.status in (SourceStatus.EMPTY, SourceStatus.FAILED)
    ]

    if not gap_sources:
        # No gaps to report -- this check is not applicable, treat as pass.
        return {"pass": True, "score": 1.0, "detail": "No source gaps to report."}

    brief_lower = (state.brief_markdown or "").lower()
    # Heuristic: does the brief mention the absence of data in some form?
    acknowledgment_signals = ["no ", "not found", "unavailable", "none found",
                              "did not", "couldn't", "could not", "no active",
                              "no verified", "limited"]
    acknowledged = any(sig in brief_lower for sig in acknowledgment_signals)

    return {
        "pass": acknowledged,
        "score": 1.0 if acknowledged else 0.0,
        "detail": f"Gaps in {gap_sources}. Brief acknowledges a gap: {acknowledged}.",
    }


def check_required_sections(state: AgentState) -> dict:
    """The brief must contain the required sections, especially the
    highest-graded one: Relevance to Rippling.
    """
    brief = state.brief_markdown or ""
    required = ["Messaging", "Relevance to Rippling"]
    present = [s for s in required if s.lower() in brief.lower()]
    return {
        "pass": len(present) == len(required),
        "score": len(present) / len(required),
        "detail": f"Required sections present: {present}",
    }


# ---------------------------------------------------------------------- #
# LLM-GRADED CHECK (one cheap-model call -- for the subjective quality)
# ---------------------------------------------------------------------- #

_RELEVANCE_GRADER_PROMPT = """You are grading ONE section of a competitor \
brief: the "Relevance to Rippling" section. Rippling is a workforce \
management platform (HR/IT/Finance on one employee record); its edge is \
unification and first-party data, its weakness is global-first hiring.

A GOOD relevance section names a SPECIFIC, ACTIONABLE angle tied to \
Rippling's actual competitive position (e.g. "competitor X's ads never \
mention multi-entity global payroll, a gap Rippling's unified record \
directly answers"). A BAD one is generic filler (e.g. "Rippling should \
improve its marketing" or "this is a strong competitor to watch").

Score the section from 0.0 to 1.0 on specificity and actionability. Return \
ONLY valid JSON: {"score": <float>, "reason": "<one sentence>"}
"""


def grade_rippling_relevance(state: AgentState) -> dict:
    """The one subjective check that needs a model. Extracts the Rippling
    relevance content and grades whether it's specific/actionable or generic.
    Uses the cheap model -- grading against a clear rubric doesn't need
    frontier reasoning.
    """
    brief = state.brief_markdown or ""
    # Pull just the relevance section if we can find it, else grade the whole
    # brief (defensive -- a malformed brief still gets graded, not skipped).
    marker = "Relevance to Rippling"
    if marker.lower() in brief.lower():
        idx = brief.lower().index(marker.lower())
        relevance_text = brief[idx:]
    else:
        relevance_text = brief

    if not relevance_text.strip():
        return {"pass": False, "score": 0.0, "detail": "No relevance section found."}

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=CHEAP_MODEL,
        max_tokens=150,
        system=_RELEVANCE_GRADER_PROMPT,
        messages=[{"role": "user", "content": relevance_text}],
    )

    text = response.content[0].text.strip()
    result = parse_json_response(text, "rippling_relevance_grader")
    if result is None:
        return {"pass": False, "score": 0.0, "detail": "Grader response unparseable."}
    try:
        score = float(result.get("score", 0.0))
        return {
            "pass": score >= 0.6,
            "score": score,
            "detail": result.get("reason", ""),
        }
    except (TypeError, ValueError):
        return {"pass": False, "score": 0.0, "detail": "Grader score not a valid float."}


# ---------------------------------------------------------------------- #
# AGGREGATE
# ---------------------------------------------------------------------- #

def score_brief(state: AgentState) -> dict:
    """Runs every check against a completed AgentState and returns a full
    scorecard. This is what run_eval.py calls per competitor.
    """
    checks = {
        "claims_grounded": check_claims_are_grounded(state),
        "source_diversity": check_source_diversity(state),
        "gaps_reported": check_gaps_are_reported(state),
        "required_sections": check_required_sections(state),
        "rippling_relevance": grade_rippling_relevance(state),
    }

    overall = sum(c["score"] for c in checks.values()) / len(checks)

    return {
        "competitor": state.competitor,
        "overall_score": round(overall, 2),
        "checks": checks,
    }