"""
Fetches active Google Ads via SerpApi's Ads Transparency Center.
Two-step: search creatives, then fetch copy for text-format ads.
"""

import os

from serpapi import GoogleSearch

from state import SourceResult, SourceStatus

# How many per-creative detail calls to make. Each call costs 1 SerpApi search.
# Text-format creatives only — image/video have no copy to extract.
MAX_DETAIL_CALLS = 6


def _best_advertiser_match(
    by_advertiser: dict[str, list[dict]], competitor_name: str
) -> tuple[str, list[dict]] | None:
    """Pick the advertiser whose name best matches the competitor.

    Prevents text-search noise (e.g. "BlueVision Interactive" showing up when
    searching "apple.com") from polluting the brief with unrelated ad data.
    """
    competitor_lower = competitor_name.strip().lower()

    # Exact name match first.
    for name, creatives in by_advertiser.items():
        if name.strip().lower() == competitor_lower:
            return name, creatives

    # Substring match (handles "Gusto HR" matching "Gusto", etc.)
    for name, creatives in by_advertiser.items():
        name_lower = name.strip().lower()
        if competitor_lower in name_lower or name_lower in competitor_lower:
            return name, creatives

    # Fallback: largest result set (most likely to be the real brand page).
    if by_advertiser:
        best = max(by_advertiser.items(), key=lambda x: len(x[1]))
        return best

    return None


def _fetch_ad_copy(
    advertiser_id: str, ad_creative_id: str, api_key: str
) -> str | None:
    """Fetch actual headline + description for a single text creative.

    The base google_ads_transparency_center engine returns metadata only
    (format, dates, impression count). Actual copy requires a second call
    to the ad_details engine keyed on advertiser_id + ad_creative_id.
    Returns None on any failure — callers degrade gracefully to metadata-only.
    """
    try:
        search = GoogleSearch({
            "engine": "google_ads_transparency_center_ad_details",
            "advertiser_id": advertiser_id,
            "creative_id": ad_creative_id,
            "api_key": api_key,
        })
        result = search.get_dict()
        if "error" in result:
            return None

        # Try common response shapes — SerpApi may nest differently.
        creative = result.get("ad_creative") or result.get("creative") or {}
        headline = creative.get("headline") or creative.get("title", "")
        description = creative.get("description") or creative.get("body", "")

        parts = [p for p in [headline, description] if p]
        return " | ".join(parts) if parts else None
    except Exception:
        return None


def search_google_ads(company_name: str, domain: str | None = None) -> SourceResult:
    """Search the Google Ads Transparency Center for a company's active ads.

    Uses text search (not advertiser_id) since we don't have a pre-known ID.
    No region filter — global results give fuller picture of messaging strategy,
    especially for global-first competitors like Deel or Remote. Noise from
    unrelated advertisers is handled by _best_advertiser_match, not region.
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
        return SourceResult(
            status=SourceStatus.EMPTY,
            note="No verified advertiser profile found on Google Ads "
            "Transparency Center — competitor may not be a verified advertiser.",
        )

    # Group by advertiser and filter to the best-matching company.
    by_advertiser: dict[str, list[dict]] = {}
    for creative in ad_creatives:
        name = creative.get("advertiser", "Unknown advertiser")
        by_advertiser.setdefault(name, []).append(creative)

    match = _best_advertiser_match(by_advertiser, company_name)
    if match is None:
        return SourceResult(
            status=SourceStatus.EMPTY,
            note="No advertiser matching the competitor found in results — "
            "likely a text-search noise case with no real match.",
        )

    advertiser_name, matched_creatives = match

    formatted = [f"Advertiser: {advertiser_name} ({len(matched_creatives)} ad(s))"]
    detail_calls = 0

    for creative in matched_creatives:
        fmt = creative.get("format", "unknown")
        days_shown = creative.get("total_days_shown", "?")
        advertiser_id = creative.get("advertiser_id", "")
        ad_creative_id = creative.get("ad_creative_id", "")

        line = f"  - Format: {fmt}, shown for {days_shown} day(s)"

        # Fetch real copy for text creatives, up to the cap.
        if (
            fmt == "text"
            and advertiser_id
            and ad_creative_id
            and detail_calls < MAX_DETAIL_CALLS
        ):
            copy = _fetch_ad_copy(advertiser_id, ad_creative_id, api_key)
            detail_calls += 1
            if copy:
                line += f' Copy: "{copy}"'

        formatted.append(line)

    return SourceResult(
        status=SourceStatus.SUCCESS,
        raw_content="\n".join(formatted),
        note=(
            f"{len(matched_creatives)} ad creative(s) found for '{advertiser_name}'. "
            f"{detail_calls} detail call(s) made for copy."
        ),
        source_url="https://ads.google.com/intl/en/home/transparency-center/",
    )
