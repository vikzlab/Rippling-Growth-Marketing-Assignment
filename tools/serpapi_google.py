"""
tools/serpapi_google.py
========================
Pulls a competitor's active ad creative from Google's Ads Transparency
Center, via SerpApi. This is SerpApi's own official, documented engine
(confirmed against serpapi.com/google-ads-transparency-center-api).

Real response shape, confirmed from SerpApi's documentation:
  {
    "search_metadata": {...},
    "search_parameters": {...},
    "search_information": {"total_results": N},
    "ad_creatives": [
      {
        "advertiser_id": "AR...",
        "advertiser": "Company Name",
        "ad_creative_id": "CR...",
        "format": "text" | "image" | "video",
        "image": "<url, if format is image>",
        "total_days_shown": N,
        "first_shown": <unix timestamp>,
        "last_shown": <unix timestamp>,
        "details_link": "<url>"
      },
      ...
    ]
  }


This is the source most likely to come back EMPTY in practice -- most
companies aren't verified advertisers on Google, so that's a completely
normal outcome and needs to be handled as a first-class case (PRD Flow B /
Requirement E2).

PLAIN CODE -- no LLM call happens here.
"""

import os

from serpapi import GoogleSearch

from state import SourceResult, SourceStatus


def search_google_ads(company_name: str, domain: str | None = None) -> SourceResult:
    """Search the Google Ads Transparency Center for a company's active ads.

    Uses the "text" parameter (free-text search) rather than advertiser_id,
    since we don't have a pre-known advertiser ID for an arbitrary
    competitor -- text search is the documented way to search by name or
    domain instead.
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        return SourceResult(
            status=SourceStatus.FAILED,
            note="SERPAPI_API_KEY not set in environment.",
        )

    query = domain if domain else company_name

    params = {
        "engine": "google_ads_transparency_center",
        "text": query,
        "api_key": api_key,
    }

    try:
        search = GoogleSearch(params)
        results = search.get_dict()
    except Exception as exc:
        return SourceResult(
            status=SourceStatus.FAILED,
            note=f"SerpApi request failed: {exc}",
        )

    if "error" in results:
        return SourceResult(
            status=SourceStatus.EMPTY,
            note=f"No Google Ads Transparency data found: {results['error']}",
        )

    ad_creatives = results.get("ad_creatives", [])
    if not ad_creatives:
        # This is the expected, common case (PRD Section 9 risk:
        # "verified-advertiser gaps"). Many legitimate, actively-marketing
        # companies simply won't show up here.
        return SourceResult(
            status=SourceStatus.EMPTY,
            note="No verified advertiser profile found on Google Ads "
            "Transparency Center -- competitor may not be a verified "
            "advertiser, or may not run Search/Display/YouTube ads.",
        )

    # Group by advertiser name in case the text search matched more than one
    # distinct advertiser (a real possibility with common company names).
    by_advertiser: dict[str, list[dict]] = {}
    for creative in ad_creatives:
        name = creative.get("advertiser", "Unknown advertiser")
        by_advertiser.setdefault(name, []).append(creative)

    formatted = []
    for advertiser_name, creatives in by_advertiser.items():
        formatted.append(f"Advertiser: {advertiser_name} ({len(creatives)} ad(s))")
        for creative in creatives[:10]:
            fmt = creative.get("format", "unknown")
            days_shown = creative.get("total_days_shown", "?")
            formatted.append(f"  - Format: {fmt}, shown for {days_shown} day(s)")

    return SourceResult(
        status=SourceStatus.SUCCESS,
        raw_content="\n".join(formatted),
        note=f"{len(ad_creatives)} ad creative(s) found across "
        f"{len(by_advertiser)} advertiser(s).",
        source_url="https://ads.google.com/intl/en/home/transparency-center/",
    )