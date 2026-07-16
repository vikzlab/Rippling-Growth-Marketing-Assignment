"""
steps/run_research.py
======================
Executes the research plan produced by steps/plan_research.py -- calling the
actual tools/ functions for each source the planner decided to check.

PLAIN CODE, no LLM call here. "Call this function for each source in the
plan" is mechanical dispatch, not a judgment call -- the judgment already
happened in plan_research.py. This file just does what it's told.
"""

from datetime import datetime, timezone

from state import AgentState, SourceStatus
from tools import press_search, scrapecreators_facebook, serpapi_google, web_fetch

# Maps a source name (as used in AgentState.sources and the planner's output)
# to the function that actually fetches it. Keeping this as a lookup table
# rather than a chain of if/elif makes it trivial to add a new source later --
# just add one line here and one key to AgentState.sources.
_SOURCE_FETCHERS = {
    "meta_ads": lambda competitor, domain: scrapecreators_facebook.search_meta_ads(competitor),
    "google_ads": lambda competitor, domain: serpapi_google.search_google_ads(
        competitor, domain
    ),
    "press": lambda competitor, domain: press_search.search_press(competitor),
    "social": lambda competitor, domain: press_search.search_social(competitor),
}


def retry_source(state: AgentState, source_name: str) -> None:
    """Re-run a single source and update state. Called by adaptive_replan when
    a source is worth retrying after an initial EMPTY or FAILED result."""
    if source_name == "website":
        _run_website_research(state)
        return

    fetcher = _SOURCE_FETCHERS.get(source_name)
    if fetcher is None:
        return

    result = fetcher(state.competitor, state.competitor_domain)
    result.checked_at = datetime.now(timezone.utc)
    state.sources[source_name] = result


def run_research(state: AgentState) -> None:
    """Runs every source in state.research_plan and writes results back into
    state.sources. Mutates state in place, matching the pattern the rest of
    the Flow uses.

    The website source is handled slightly differently from the others,
    since it needs a URL (built from the domain) rather than just a company
    name -- see the branch below.
    """
    for source_name in state.research_plan:
        if source_name == "website":
            _run_website_research(state)
            continue

        fetcher = _SOURCE_FETCHERS.get(source_name)
        if fetcher is None:
            # Planner returned a source name we don't recognize. Skip it
            # rather than crash -- this is a defensive fallback, not an
            # expected path, but a malformed planner response shouldn't take
            # down the whole run.
            continue

        result = fetcher(state.competitor, state.competitor_domain)
        result.checked_at = datetime.now(timezone.utc)
        state.sources[source_name] = result


def _run_website_research(state: AgentState) -> None:
    """Fetches the competitor's homepage. If we have a domain, we build the
    URL directly; otherwise this source is skipped (we don't guess domains --
    that's exactly the kind of ambiguity steps/clarify.py should have caught
    earlier if it mattered).
    """
    if not state.competitor_domain:
        state.sources["website"] = state.sources["website"].model_copy(
            update={
                "status": SourceStatus.FAILED,
                "note": "No domain provided; cannot fetch website.",
            }
        )
        return

    url = state.competitor_domain
    if not url.startswith("http"):
        url = f"https://{url}"

    result = web_fetch.fetch_page(url)
    result.checked_at = datetime.now(timezone.utc)
    state.sources["website"] = result