"""
tools/web_fetch.py
===================
Fetches a competitor's website or pricing page and extracts clean, readable
text. This is PLAIN CODE — no LLM call happens here. Per the PRD's cost design
(§5.1), fetching a URL is mechanical, not a judgment call, so it costs nothing
but a network request.

Used by steps/run_research.py (initial shallow fetch) and again by
steps/route_followup.py when a follow-up needs a DEEPER fetch (e.g. "dig
deeper on pricing" — see PRD Flow C).
"""

import re

import requests
from bs4 import BeautifulSoup

from state import SourceResult, SourceStatus

# Keep the timeout short — a slow site shouldn't stall the whole research step.
# If a site takes longer than this, we treat it as FAILED, not hang forever.
REQUEST_TIMEOUT_SECONDS = 10

# A normal-looking browser User-Agent. Some sites block requests that look
# like bots by default; this is a courtesy, not an attempt to evade anything —
# we only ever fetch public pages, same as a human visitor would see.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _clean_text(html: str) -> str:
    """Strip a raw HTML page down to readable body text.

    We deliberately drop <script>, <style>, <nav>, <footer> content — none of
    that is "marketing content," it's page furniture, and including it would
    just waste tokens (and dilute signal) when this text later gets passed
    into a synthesis prompt.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    # Collapse repeated whitespace left behind after stripping tags.
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_page(url: str) -> SourceResult:
    """Fetch a single URL and return a SourceResult.

    This is the core building block. It's used for both the initial shallow
    fetch (just the homepage or pricing page) and the deeper fetch a follow-up
    might trigger (e.g. a linked plan-comparison page).

    Returns SourceResult with status:
      SUCCESS - page fetched and had usable text
      EMPTY   - page fetched fine, but had essentially no text (e.g. a page
                that's just an image, or a redirect to nothing useful)
      FAILED  - request errored, timed out, or returned a non-200 status
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        # Covers timeouts, connection errors, DNS failures, etc. This is
        # Failure Mode 2 from the Flow C walkthrough: the fetch itself failed,
        # not "there's nothing there."
        return SourceResult(
            status=SourceStatus.FAILED,
            note=f"Request failed: {exc}",
        )

    if response.status_code != 200:
        return SourceResult(
            status=SourceStatus.FAILED,
            note=f"Non-200 response: HTTP {response.status_code}",
        )

    text = _clean_text(response.text)

    # A page that "succeeded" but has almost no real text is functionally
    # empty for our purposes — Failure Mode 1 from Flow C: the fetch worked,
    # there just wasn't anything useful to find.
    if len(text) < 100:
        return SourceResult(
            status=SourceStatus.EMPTY,
            note="Page fetched but contained little to no readable text.",
        )

    return SourceResult(
        status=SourceStatus.SUCCESS,
        raw_content=text,
    )


def find_pricing_links(homepage_url: str) -> list[str]:
    """Given a homepage, try to find a pricing page link.

    This is a lightweight heuristic (checking anchor text and href for the
    word 'pricing'), not an LLM call — pattern-matching a link is mechanical,
    it doesn't need judgment. If this doesn't find anything, that's fine; it
    just means we fall back to whatever content we already have.
    """
    try:
        response = requests.get(
            homepage_url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException:
        return []

    if response.status_code != 200:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    pricing_links = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text().lower()
        if "pricing" in href.lower() or "pricing" in text:
            # Handle relative URLs (e.g. "/pricing" instead of a full URL).
            if href.startswith("/"):
                base = homepage_url.rstrip("/")
                href = f"{base}{href}"
            pricing_links.append(href)

    # De-duplicate while preserving order.
    seen = set()
    unique_links = []
    for link in pricing_links:
        if link not in seen:
            seen.add(link)
            unique_links.append(link)

    return unique_links