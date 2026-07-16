"""
steps/volume_router.py
=======================
The literal implementation of the PRD Section 5.4 decision point:

    if total_token_count(retrieved_content) < THRESHOLD:
        pass content directly into synthesis prompt
    else:
        chunk + embed content -> retrieve top-k relevant chunks

This file is PLAIN CODE -- it calls tools/token_counter.py (also plain code)
to make the routing decision. No LLM is involved in deciding whether to use
embeddings; that would be circular (paying for a model call to decide
whether to save money on a later model call defeats the purpose).
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