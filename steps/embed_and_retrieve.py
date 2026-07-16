"""
steps/embed_and_retrieve.py
============================
The ONLY place in this system that touches a vector database. Only called
when steps/volume_router.py decides content volume crosses the threshold
(PRD Flow D -- the large-footprint case, e.g. a Microsoft-scale competitor).

For a typical competitor, this file is never invoked -- content goes
directly into synthesis. This is deliberate: a vector DB solves a real
problem (too much raw content to usefully fit in one prompt) and shouldn't
be reached for by default just because it's available.
"""

import re

import chromadb

from state import AgentState

# How much overlap to allow between chunks. A little overlap prevents a
# relevant sentence from being awkwardly split across two chunks and losing
# context on both sides.
CHUNK_SIZE_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150

# How many of the most relevant chunks to keep per source after retrieval.
# This is the number that actually controls how much gets passed into
# synthesis -- tune this, not the chunk size, if the brief feels too thin or
# too padded.
TOP_K_PER_SOURCE = 5

# The retrieval query. This encodes what we actually care about extracting
# from a large, noisy competitor footprint -- it's deliberately scoped to
# marketing/positioning signal, matching this system's actual purpose,
# rather than a generic "most important content" query.
RELEVANCE_QUERY = (
    "messaging themes, product positioning, pricing, recent campaign changes, "
    "and marketing strategy relevant to HR, IT, payroll, and workforce "
    "management software"
)


def _chunk_text(text: str) -> list[str]:
    """Splits text into overlapping chunks by character count.

    A simple sliding-window chunker rather than anything more sophisticated
    (e.g. sentence-boundary-aware splitting) -- for this system's scale and
    purpose, a straightforward approach is enough, and it's easy to reason
    about and debug if retrieval quality ever looks off.
    """
    if len(text) <= CHUNK_SIZE_CHARS:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE_CHARS
        chunks.append(text[start:end])
        start = end - CHUNK_OVERLAP_CHARS  # step forward, keeping overlap

    return chunks


def embed_and_retrieve(state: AgentState) -> None:
    """For every source with content large enough to matter, chunk it,
    embed it into a fresh in-memory Chroma collection, and replace
    source.raw_content with only the top-k most relevant chunks.

    This mutates state.sources in place, using the SAME field
    (SourceResult.raw_content) that the direct path uses -- so
    steps/synthesize.py needs no changes at all to work with either path.
    That's a deliberate interface decision: the embedding step's job is to
    shrink and focus content, not to change the shape other steps expect.
    """
    # A fresh in-memory client per run -- we don't need this data to persist
    # across sessions (that would be a genuine "cross-session comparison"
    # feature, noted as a possible future extension in the PRD, not
    # something this assignment requires).
    chroma_client = chromadb.EphemeralClient()

    for source_name, result in state.sources.items():
        if not result.raw_content:
            continue

        chunks = _chunk_text(result.raw_content)

        if len(chunks) <= TOP_K_PER_SOURCE:
            # Not enough chunks for retrieval to matter -- skip the overhead
            # of embedding, just keep the content as-is.
            continue

        collection = chroma_client.create_collection(name=f"source_{source_name}")
        collection.add(
            documents=chunks,
            ids=[f"{source_name}_{i}" for i in range(len(chunks))],
        )

        retrieved = collection.query(
            query_texts=[RELEVANCE_QUERY],
            n_results=min(TOP_K_PER_SOURCE, len(chunks)),
        )

        top_chunks = retrieved["documents"][0]
        result.raw_content = "\n\n[...]\n\n".join(top_chunks)
        result.note = (
            f"{result.note} (content reduced via embedding retrieval: "
            f"{len(chunks)} chunks -> top {len(top_chunks)})"
        ).strip()