"""UEX marketplace links.

The board is rendered server-side behind Cloudflare and has no public JSON feed:
the API only serves a capped page of commodity ads. So the overlay links to the
real page instead of scraping it, and this module just assembles the URL — one
place for the site address and the query escaping.
"""
from __future__ import annotations

from urllib.parse import quote_plus

SITE = "https://uexcorp.space/marketplace/home/"


def search_url(term: str) -> str:
    """Marketplace URL for ``term``; the home page when the term is empty."""
    term = (term or "").strip()
    if not term:
        return SITE
    return f"{SITE}?search={quote_plus(term)}"
