"""
Counts tokens in retrieved content to decide if chunking/embedding is needed.
Routes based on content volume threshold.
"""

import tiktoken

from state import AgentState

# The threshold itself. Chosen as a starting point, not a magic number --
# per PRD Section 9 ("Threshold tuning"), this should be validated
# empirically against a few real competitor runs rather than trusted blindly.
#
# Reasoning for this starting value: a frontier-model prompt has room for
# well over 100K tokens, but past a few thousand tokens of *raw marketing
# copy* specifically, relevance starts dropping -- most of a large company's
# content won't be about positioning at all. This threshold is about
# SIGNAL QUALITY, not hitting a hard context-window wall.
DEFAULT_THRESHOLD_TOKENS = 8000

# cl100k_base is the encoding used by Claude and most modern LLMs' rough
# token-counting equivalents. It won't be byte-exact for Claude specifically,
# but it's close enough to make a routing decision -- we don't need perfect
# precision here, just "is this closer to 2,000 tokens or 40,000."
#
# tiktoken downloads this encoding file on first use, which means it needs
# network access once. If that download fails (offline, blocked, first-run
# race condition), we fall back to a plain character-count approximation
# (~4 characters per token is a standard rule of thumb for English text)
# rather than letting the whole research step crash over a routing decision
# that doesn't need to be exact.
try:
    _ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENCODING = None


def count_tokens(text: str) -> int:
    """Count tokens in a string of text.

    Uses tiktoken's real tokenizer when available; falls back to a ~4
    chars-per-token approximation otherwise. Either way this is only used
    for a routing decision (PRD Section 5.4), not for anything that needs
    to be exact, like billing.
    """
    if _ENCODING is not None:
        return len(_ENCODING.encode(text))
    return len(text) // 4


def total_content_tokens(state: AgentState) -> int:
    """Sum token counts across every successfully-retrieved source in the
    current state. Sources that are EMPTY, FAILED, or NOT_CHECKED contribute
    nothing, since there's no raw_content to count.
    """
    total = 0
    for source in state.sources.values():
        if source.raw_content:
            total += count_tokens(source.raw_content)
    return total


def should_use_embedding_path(
    state: AgentState, threshold: int = DEFAULT_THRESHOLD_TOKENS
) -> bool:
    """The actual routing decision from PRD Section 5.4:

        if total_token_count(retrieved_content) < THRESHOLD:
            pass content directly into synthesis prompt
        else:
            chunk + embed content -> retrieve top-k relevant chunks

    Also updates state.total_content_tokens as a side effect, so the number
    is available later for the JSON deliverable's cost/volume reporting
    (PRD Section 6, E4) without re-counting.
    """
    total = total_content_tokens(state)
    state.total_content_tokens = total
    return total >= threshold