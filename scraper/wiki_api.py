"""
MediaWiki API client for Genshin Impact Fandom Wiki.

Provides page content fetching and category member listing
with a shared session and configurable rate limiting.
"""

import time
import requests
from typing import Optional

BASE_URL = "https://genshin-impact.fandom.com/api.php"

_session = requests.Session()
_session.headers.update({"User-Agent": "GenshinLoreRAGScraper/1.0"})

_request_delay = 0.5  # seconds between API calls
_last_request_time = 0.0


def _throttled_get(params: dict) -> dict:
    """Send a GET request with rate limiting between calls."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _request_delay:
        time.sleep(_request_delay - elapsed)

    response = _session.get(BASE_URL, params=params)
    _last_request_time = time.time()
    response.raise_for_status()
    return response.json()


def set_request_delay(seconds: float):
    """Override the default delay between API calls."""
    global _request_delay
    _request_delay = seconds


def get_page_wikitext(title: str) -> Optional[str]:
    """Fetch the raw wikitext content of a wiki page by title.

    Returns None if the page does not exist.
    """
    params = {
        "action": "query",
        "titles": title,
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
    }
    data = _throttled_get(params)
    pages = data["query"]["pages"]
    for page_id, page_data in pages.items():
        if page_id == "-1":
            return None
        if "revisions" in page_data:
            return page_data["revisions"][0]["slots"]["main"]["*"]
    return None


def get_category_members(category: str, namespace: int = 0) -> list[str]:
    """Fetch all page titles in a MediaWiki category, handling pagination.

    Args:
        category: Category name without the "Category:" prefix,
                  e.g. "Playable Characters".
        namespace: MediaWiki namespace (0 = main articles).

    Returns:
        Sorted list of page titles.
    """
    titles = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmnamespace": str(namespace),
        "cmlimit": "500",
        "format": "json",
    }

    while True:
        data = _throttled_get(params)
        members = data.get("query", {}).get("categorymembers", [])
        titles.extend(member["title"] for member in members)

        continuation = data.get("continue")
        if continuation and "cmcontinue" in continuation:
            params["cmcontinue"] = continuation["cmcontinue"]
        else:
            break

    return sorted(titles)
