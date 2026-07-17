"""
Routes content to synthesis directly or via embedding based on token count.
Decision point: if content < threshold, skip embedding overhead.
"""

from state import AgentState
from tools.token_counter import should_use_embedding_path


def route_by_volume(state: AgentState) -> str:
    """Returns "direct" or "embed", and updates state.used_embedding_path as
    a side effect so the JSON deliverable can report which path was taken
    (PRD Section 6, E4 -- this is what proves the cost-proportional design
    actually held up on a given run, not just in theory).
    """
    if should_use_embedding_path(state):
        state.used_embedding_path = True
        return "embed"

    state.used_embedding_path = False
    return "direct"