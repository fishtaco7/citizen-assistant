"""UEX marketplace ads: player listings for items, not commodity price tables.

The marketplace is an ad board rather than a price table: listings appear, sell
out and expire constantly, so it cannot be cached for hours the way the hauling
routes are.  A fetch reads the current board, drops inactive and expired ads,
and keeps the newest first (the site keeps a lot of dead ones visible).

Field names differ between the marketplace and the commodity endpoints, and UEX
has used more than one collection name for this data, so the adapter below
accepts a small set of aliases instead of assuming one exact shape.  If UEX
renames something, the alias tables at the top are the only place to change.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import config_dir

API = "https://api.uexcorp.uk/2.0"
SITE = "https://uexcorp.space/marketplace/"
USER_AGENT = "Citizen-Assistant/1.0 (personal overlay; +https://uexcorp.space)"

# Tried in order; the first collection that answers with rows wins.
ENDPOINTS = ("marketplace", "marketplace_ads", "marketplace_listings")

TTL = 300.0
CACHE_NAME = "marketplace_cache.json"

_NAME = ("item_name", "name", "title", "item", "commodity_name", "vehicle_name")
_PRICE = ("price", "price_auec", "unit_price", "amount")
_QTY = ("quantity", "stock", "count", "amount")
_SIDE = ("type", "ad_type", "side", "operation", "kind")
_CREATED = ("date_created", "date_added", "date_posted", "created_at",
            "date", "timestamp", "time")
_SELLER = ("user_name", "username", "seller", "user", "owner", "author")
_LINK = ("url", "link", "permalink")

EXPIRED_WORDS = {"inactive", "expired", "sold", "closed", "deleted", "removed"}


def _lower(record: dict) -> dict:
    return {str(key).lower(): value for key, value in record.items()}


def _pick(record: dict, keys: tuple[str, ...], default=None):
    """First present, non-empty value among ``keys`` (case-insensitive)."""
    lowered = _lower(record)
    for key in keys:
        if key in lowered:
            value = lowered[key]
            if value not in (None, "", [], {}):
                return value
    return default


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "")
    return str(value or "").strip()


def _num(value, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _unix(value) -> float:
    """Unix seconds from an int, a float or a numeric string (ms tolerated)."""
    stamp = _num(value, 0.0)
    if stamp > 1e11:            # milliseconds
        stamp /= 1000.0
    return stamp


def _side(record: dict) -> str:
    raw = _pick(record, _SIDE, "")
    if isinstance(raw, bool):
        return "SELL" if raw else "BUY"
    words = _text(raw).lower()
    if words.startswith(("buy", "wanted", "want")):
        return "BUY"
    if words.startswith(("sell", "sale", "offer")):
        return "SELL"
    if words in {"b", "0", "false"}:
        return "BUY"
    return "SELL"


def _is_active(record: dict) -> bool:
    for key in ("is_active", "active", "is_available", "available"):
        lowered = _lower(record)
        if key in lowered:
            value = lowered[key]
            if isinstance(value, bool):
                return value
            if _num(value, 1) == 0:
                return False

    status = _text(_pick(record, ("status", "state"), "")).lower()
    if status in EXPIRED_WORDS:
        return False

    expires = _unix(_pick(record, ("date_expiration", "date_expire",
                                   "expires_at", "expiration"), 0))
    if expires and expires < time.time():
        return False
    return True


@dataclass
class Ad:
    name: str
    side: str
    price: float
    quantity: int
    created: float
    seller: str = ""
    url: str = ""

    @property
    def age_text(self) -> str:
        if not self.created:
            return ""
        seconds = max(0.0, time.time() - self.created)
        if seconds < 3600:
            return f"{int(seconds // 60)}m"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h"
        return f"{int(seconds // 86400)}d"


def parse_ad(record: dict) -> Ad | None:
    name = _text(_pick(record, _NAME, ""))
    if not name:
        return None
    return Ad(
        name=name,
        side=_side(record),
        price=_num(_pick(record, _PRICE, 0)),
        quantity=int(_num(_pick(record, _QTY, 0))),
        created=_unix(_pick(record, _CREATED, 0)),
        seller=_text(_pick(record, _SELLER, "")),
        url=_text(_pick(record, _LINK, "")),
    )


def _rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "ads", "marketplace", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _get(url: str, timeout: float = 20.0):
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _cache_path() -> Path:
    return config_dir() / CACHE_NAME


def _read_cache() -> list[dict]:
    try:
        with open(_cache_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        ads = data.get("ads")
        return ads if isinstance(ads, list) else []
    return data if isinstance(data, list) else []


def _write_cache(rows: list[dict]) -> None:
    path = _cache_path()
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)
    except OSError:
        pass


def _sort(ads: list[Ad]) -> list[Ad]:
    return sorted(ads, key=lambda ad: ad.created, reverse=True)


class MarketplaceData:
    """Current board, newest first, filtered to ads that are still live."""

    def __init__(self):
        self.ads: list[Ad] = []
        self.source = ""
        self.error = ""
        self.loaded_at = 0.0

    # ------------------------------------------------------------------
    @property
    def fresh(self) -> bool:
        return bool(self.ads) and (time.time() - self.loaded_at) < TTL

    def load_cached(self) -> None:
        """Show the last good board instantly, before the network answers."""
        if self.ads:
            return
        ads = [ad for ad in (parse_ad(row) for row in _read_cache()) if ad]
        if ads:
            self.ads = _sort(ads)
            self.loaded_at = 0.0

    def fetch(self) -> None:
        """Read the board, trying each known collection name in turn."""
        self.error = ""
        last: Exception | None = None
        for name in ENDPOINTS:
            try:
                payload = _get(f"{API}/{name}")
            except Exception as exc:        # 404, offline, rate limited
                last = exc
                continue

            rows = _rows(payload)
            if not rows:
                continue

            live = [row for row in rows if _is_active(row)]
            ads = [ad for ad in (parse_ad(row) for row in live) if ad]
            if not ads:
                continue

            self.ads = _sort(ads)
            self.source = f"{API}/{name}"
            self.loaded_at = time.time()
            _write_cache(live)
            return

        self.load_cached()
        if not self.ads:
            raise RuntimeError(
                "UEX marketplace unavailable"
                + (f": {last}" if last else " (no ads in the response)"))

    # ------------------------------------------------------------------
    def search(self, query: str, side: str) -> list[Ad]:
        needle = query.strip().lower()
        return [ad for ad in self.ads
                if ad.side == side
                and (not needle
                     or needle in ad.name.lower()
                     or needle in ad.seller.lower())]

    def counts(self) -> tuple[int, int]:
        sell = sum(1 for ad in self.ads if ad.side == "SELL")
        return sell, len(self.ads) - sell


def open_ad(ad: Ad) -> None:
    """Open the listing, or the marketplace front page when there is no link."""
    import webbrowser

    url = ad.url or SITE
    if not url.startswith(("http://", "https://")):
        url = SITE
    try:
        webbrowser.open(url)
    except OSError:
        starter = getattr(os, "startfile", None)    # Windows only
        if starter is not None:
            starter(url)
