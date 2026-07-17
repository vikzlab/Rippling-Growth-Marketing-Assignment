"""
Fetches active Meta ad creative via ScrapeCreators' Facebook Ad Library API.
Two-step: company name → page_id → ads.
"""

import os

import requests

from state import SourceResult, SourceStatus

SEARCH_COMPANIES_URL = "https://api.scrapecreators.com/v1/facebook/adLibrary/search/companies"
COMPANY_ADS_URL = "https://api.scrapecreators.com/v1/facebook/adLibrary/company/ads"
REQUEST_TIMEOUT_SECONDS = 15

# How many ads to keep in the formatted summary. ScrapeCreators can return
# many pages of results (via cursor pagination); we only fetch the first
# page and cap what we format, since more than this rarely adds signal for
# a marketing-positioning brief and just burns tokens downstream.
MAX_ADS_TO_FORMAT = 20


def search_companies(query: str) -> list[dict]:
    """Search the Facebook Ad Library for companies matching a name.

    Returns the raw list of candidate results (each a dict with page_id,
    name, category, verification, etc). Returns an empty list on any
    failure or zero-match case, never raises -- callers should treat an
    empty list as "no match found," a normal, expected outcome
    (PRD Flow B: not every source has data for every competitor).
    """
    api_key = os.getenv("SCRAPECREATORS_API_KEY")
    if not api_key:
        return []

    headers = {"x-api-key": api_key}
    params = {"query": query}

    try:
        response = requests.get(
            SEARCH_COMPANIES_URL,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return []

    if response.status_code != 200:
        return []

    try:
        data = response.json()
    except ValueError:
        return []

    return data.get("searchResults", [])


def find_best_company_match(query: str, results: list[dict]) -> dict | None:
    """Given the raw list of candidate companies from search_companies(),
    pick the single best match for the competitor we're actually researching.

    The API can return multiple plausible hits for one query -- e.g.
    searching "nike" returns both "Nike Football" and "Nike" itself. We want
    the actual brand page, not a sub-brand or fan page, so we rank by:

      1. Exact, case-insensitive name match (best signal this is the actual
         company, not a related page)
      2. Verified pages (blue checkmark) over unverified
      3. Higher like count as a tiebreaker

    Returns None if results is empty -- callers must handle that as a
    genuine "no match" case, not assume this always returns something.
    """
    if not results:
        return None

    query_lower = query.strip().lower()

    exact_matches = [r for r in results if r.get("name", "").strip().lower() == query_lower]
    if exact_matches:
        exact_matches.sort(
            key=lambda r: (r.get("verification") == "BLUE_VERIFIED", r.get("likes") or 0),
            reverse=True,
        )
        return exact_matches[0]

    ranked = sorted(
        results,
        key=lambda r: (r.get("verification") == "BLUE_VERIFIED", r.get("likes") or 0),
        reverse=True,
    )
    return ranked[0]


def get_company_ads(page_id: str) -> tuple[dict | None, str]:
    """Fetch a company's active ad library entries given their Facebook page_id.

    Returns (data, error_note). On success, data is the parsed JSON response
    and error_note is "". On failure, data is None and error_note describes why.
    Returning both lets the caller include the specific failure reason in the
    SourceResult note rather than just logging "request failed."
    """
    api_key = os.getenv("SCRAPECREATORS_API_KEY")
    if not api_key:
        return None, "SCRAPECREATORS_API_KEY not set."

    headers = {"x-api-key": api_key}
    params = {
        "pageId": page_id,
        "status": "ACTIVE",
        "sort_by": "total_impressions",
    }

    try:
        response = requests.get(
            COMPANY_ADS_URL,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return None, f"Request exception: {exc}"

    if response.status_code != 200:
        return None, f"HTTP {response.status_code}: {response.text[:200]}"

    try:
        return response.json(), ""
    except ValueError:
        return None, "Response was not valid JSON."


def _format_ads(ads_response: dict) -> str:
    """Turns the raw ads API response into compact, readable text for the
    synthesis prompt -- pulling just the marketing-relevant fields (body
    copy, CTA, format, platform) out of a much larger raw object that also
    contains internal IDs, image URLs, and other noise a synthesis model
    doesn't need.
    """
    results = ads_response.get("results", [])
    lines = []

    for ad in results[:MAX_ADS_TO_FORMAT]:
        snapshot = ad.get("snapshot", {}) or {}
        title = snapshot.get("title", "")
        body = (snapshot.get("body") or {}).get("text", "")
        cta = snapshot.get("cta_text", "")
        display_format = snapshot.get("display_format", "")
        platforms = ", ".join(ad.get("publisher_platform", []) or [])

        line = f"- [{display_format}, {platforms}]"
        if title:
            line += f' Headline: "{title}"'
        if body:
            line += f' Copy: "{body.strip()}"'
        if cta:
            line += f" CTA: {cta}"
        lines.append(line)

    return "\n".join(lines)


def search_meta_ads(company_name: str) -> SourceResult:
    """The single entry point steps/run_research.py actually calls. Ties the
    two-step lookup together: find the company's page, then fetch their ads.
    From the rest of the system's point of view, this is indistinguishable
    from any other single-call source fetcher -- the two-step complexity is
    fully contained here.

    Returns SourceResult with status:
      SUCCESS - a company page was found and it has active ad creative
      EMPTY   - either no matching company page was found, or a page was
                found but has zero active ads right now (both are normal,
                common outcomes -- not every competitor advertises on Meta)
      FAILED  - the API itself errored (missing key, network failure, etc.)
    """
    api_key = os.getenv("SCRAPECREATORS_API_KEY")
    if not api_key:
        return SourceResult(
            status=SourceStatus.EMPTY,
            note="Meta Ad Library data is an optional source -- "
            "SCRAPECREATORS_API_KEY not configured.",
        )

    # Step 1: resolve the company name to a page_id.
    candidates = search_companies(company_name)
    best_match = find_best_company_match(company_name, candidates)

    if best_match is None:
        return SourceResult(
            status=SourceStatus.EMPTY,
            note=f"No Facebook page found matching '{company_name}' in the "
            "Ad Library company search.",
        )

    page_id = best_match.get("page_id")
    matched_name = best_match.get("name", company_name)

    # Step 2: fetch that page's active ads.
    ads_response, error_note = get_company_ads(page_id)

    if ads_response is None:
        return SourceResult(
            status=SourceStatus.FAILED,
            note=f"Found company page '{matched_name}' (page_id={page_id}) "
            f"but the ad-fetch request failed: {error_note}",
        )

    results = ads_response.get("results", [])
    if not results:
        return SourceResult(
            status=SourceStatus.EMPTY,
            note=f"Found company page '{matched_name}' but it has zero "
            "active ads on Meta right now.",
        )

    formatted = _format_ads(ads_response)

    return SourceResult(
        status=SourceStatus.SUCCESS,
        raw_content=formatted,
        note=f"{len(results)} active ad(s) found for '{matched_name}' "
        f"(page_id={page_id}).",
        source_url=f"https://www.facebook.com/{page_id}",
    )