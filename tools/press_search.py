"""
Web search for press coverage, announcements, and social media presence.
Uses Tavily for clean, pre-summarized results.
"""

import os

from tavily import TavilyClient

from state import SourceResult, SourceStatus

# How many search results to pull per query. Kept small deliberately — more
# results just means more tokens to pass downstream, and search relevance
# drops off fast past the first several hits.
MAX_RESULTS = 8


def search_press(company_name: str, days_back: int = 365) -> SourceResult:
    """Search for recent press, announcements, and news about a competitor.

    days_back controls recency — default 1 year, matching the PRD's "what's
    changed recently" output requirement (O1). A tighter window can be passed
    for a follow-up like "what have they announced this month."
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return SourceResult(
            status=SourceStatus.FAILED,
            note="TAVILY_API_KEY not set in environment.",
        )

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=f"{company_name} announcement OR launch OR press release 2026",
            max_results=MAX_RESULTS,
            search_depth="advanced",
            chunks_per_source=3,
        )
    except Exception as exc:
        return SourceResult(
            status=SourceStatus.FAILED,
            note=f"Tavily search failed: {exc}",
        )

    results = response.get("results", [])
    if not results:
        return SourceResult(
            status=SourceStatus.EMPTY,
            note="No recent press or announcements found for this company.",
        )

    formatted = []
    for item in results:
        title = item.get("title", "")
        content = item.get("content", "")
        url = item.get("url", "")
        formatted.append(f"- {title}\n  {content}\n  Source: {url}")

    return SourceResult(
        status=SourceStatus.SUCCESS,
        raw_content="\n\n".join(formatted),
        note=f"{len(results)} press/news result(s) found.",
        source_url=results[0].get("url") if results else None,
    )


def search_social(company_name: str) -> SourceResult:
    """Search for recent social media presence and content themes.

    Same Tavily client, scoped query. Kept as a separate function (rather
    than folding into search_press) because it maps to its own entry in
    AgentState.sources and its own line item in the assignment's requirements
    (R2), even though the underlying tool call is nearly identical.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return SourceResult(
            status=SourceStatus.FAILED,
            note="TAVILY_API_KEY not set in environment.",
        )

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=f"{company_name} marketing messaging product positioning",
            max_results=MAX_RESULTS,
            search_depth="advanced",
            chunks_per_source=3,
            include_domains=["linkedin.com", "x.com", "twitter.com"],
        )
    except Exception as exc:
        return SourceResult(
            status=SourceStatus.FAILED,
            note=f"Tavily search failed: {exc}",
        )

    results = response.get("results", [])
    if not results:
        return SourceResult(
            status=SourceStatus.EMPTY,
            note="No recent social media presence found via search.",
        )

    formatted = [
        f"- {item.get('title', '')}\n  {item.get('content', '')}"
        for item in results
    ]

    return SourceResult(
        status=SourceStatus.SUCCESS,
        raw_content="\n\n".join(formatted),
        note=f"{len(results)} social-related result(s) found.",
        source_url=results[0].get("url") if results else None,
    )