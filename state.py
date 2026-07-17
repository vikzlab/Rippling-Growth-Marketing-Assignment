"""
Pydantic state model for the competitor research pipeline.
Carries AgentState through all steps; no argument-passing between steps.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SourceStatus(str, Enum):
    """The three real outcomes for any research source. Deliberately not just
    True/False — "empty" and "failed" need different handling downstream
    (PRD Flow B and the pricing-follow-up failure modes we designed)."""

    NOT_CHECKED = "not_checked"
    SUCCESS = "success"
    EMPTY = "empty"       # source was reachable, but had nothing useful
    FAILED = "failed"     # source was unreachable, errored, or timed out


class SourceResult(BaseModel):
    """The outcome of checking one research source (website, Meta ads, etc.)."""

    status: SourceStatus = SourceStatus.NOT_CHECKED
    raw_content: str = ""          # the actual retrieved text, if any
    note: str = ""                 # human-readable reason, esp. for EMPTY/FAILED
    checked_at: Optional[datetime] = None
    depth: str = "shallow"         # "shallow" | "deep" — used by follow-ups (Flow C)
    source_url: Optional[str] = None  # where the content came from (for verification)


class Claim(BaseModel):
    """One grounded claim in the final brief. Every claim in the markdown output
    must trace back to one of these — a claim with no source is dropped, not kept
    (PRD §5.5, the same grounding pattern used in prior agent work)."""

    text: str
    source: str                    # which SourceResult this came from, e.g. "website"
    confidence: float = Field(ge=0.0, le=1.0)
    section: str                   # "messaging" | "positioning" | "pricing" |
                                    # "recent_changes" | "rippling_relevance"


class AgentState(BaseModel):
    """The full state object carried through the Flow for one competitor research
    session. This is what gets serialized to the JSON deliverable (PRD §6, O2),
    and what a follow-up request reads from before deciding what to re-run.
    """

    # --- input ---
    competitor: str = ""
    competitor_domain: Optional[str] = None
    user_clarifications: dict[str, str] = Field(default_factory=dict)

    # --- research state, one SourceResult per source type ---
    sources: dict[str, SourceResult] = Field(
        default_factory=lambda: {
            "website": SourceResult(),
            "meta_ads": SourceResult(),
            "google_ads": SourceResult(),
            "press": SourceResult(),
            "social": SourceResult(),
        }
    )

    # --- planning state (what the planner decided, and why — PRD Flow A/B) ---
    research_plan: list[str] = Field(default_factory=list)
    planner_reasoning: str = ""

    # --- volume routing (PRD §5.4) ---
    total_content_tokens: int = 0
    used_embedding_path: bool = False

    # --- output ---
    claims: list[Claim] = Field(default_factory=list)
    brief_markdown: str = ""

    # --- conversation state, for follow-ups (Flow C) ---
    known_followup_categories: list[str] = Field(
        default_factory=lambda: [
            "pricing",
            "positioning",
            "messaging",
            "ads",
            "social",
            "recent_changes",
        ]
    )
    conversation_log: list[dict[str, str]] = Field(default_factory=list)

    # --- run metadata ---
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def source_summary(self) -> str:
        """A compact, human-readable summary of what's been checked so far —
        used to give the planner/replanner (steps/plan_research.py,
        steps/adaptive_replan.py) enough context without dumping raw content."""
        lines = []
        for name, result in self.sources.items():
            lines.append(f"- {name}: {result.status.value} ({result.note or 'n/a'})")
        return "\n".join(lines)

    def to_output_json(self) -> dict:
        """Shape matching the required JSON deliverable (PRD §6, O2): sources,
        claims, confidence, timestamps."""
        return {
            "competitor": self.competitor,
            "domain": self.competitor_domain,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": {
                name: {
                    "status": result.status.value,
                    "note": result.note,
                    "checked_at": result.checked_at.isoformat()
                    if result.checked_at
                    else None,
                    "depth": result.depth,
                    "source_url": result.source_url,
                }
                for name, result in self.sources.items()
            },
            "claims": [c.model_dump() for c in self.claims],
            "used_embedding_path": self.used_embedding_path,
            "total_content_tokens": self.total_content_tokens,
        }