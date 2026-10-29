"""Bundled station/location names used by the manual stash notes.

The list is a snapshot of UEX terminal metadata. It is deliberately local: the
inventory panel must work offline and must not make a network request just to
offer a station name. Players can still type a location that is not in the list.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "data" / "stations.json"


@lru_cache(maxsize=1)
def stations() -> tuple[dict, ...]:
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return ()
    entries = payload.get("stations") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return ()
    return tuple(entry for entry in entries
                 if isinstance(entry, dict) and entry.get("name"))


@lru_cache(maxsize=1)
def station_names() -> tuple[str, ...]:
    return tuple(entry["name"] for entry in stations())


def _normalise(value: str) -> str:
    value = value.casefold()
    value = value.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", value)


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    index: dict[str, str] = {}
    for entry in stations():
        name = entry["name"]
        key = _normalise(name)
        index[key] = name
        short = re.sub(r"\s+station$", "", name, flags=re.IGNORECASE)
        if short != name:
            index[_normalise(short)] = name
        # Lagrange stations are commonly written as just "CRU-L4".
        match = re.match(r"^([A-Z]{3}-L\d+)", name)
        if match:
            index[_normalise(match.group(1))] = name
    return index


@lru_cache(maxsize=1)
def _system_by_alias() -> dict[str, str]:
    """Map every known station alias to its star system."""
    by_name = {_normalise(entry["name"]): str(entry.get("system") or "")
               for entry in stations()}
    index: dict[str, str] = {}
    for alias, name in _aliases().items():
        system = by_name.get(_normalise(name), "")
        if system:
            index[alias] = system
    return index


def system_for_station(name: str) -> str:
    """Best-effort star-system lookup for a station or location label."""
    if not name:
        return ""
    cleaned = clean_terminal_name(name)
    if not cleaned:
        return ""
    return _system_by_alias().get(_normalise(cleaned), "")


def clean_terminal_name(name: str) -> str:
    """Turn a UEX terminal label into the station/location players know.

    Route data uses shop-level labels such as ``Admin - Seraphim`` or
    ``Locker Room - CRU-L4``. The last dash-separated segment is a good fallback
    even when the bundled snapshot no longer has the exact location.
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    short = re.split(r"\s+-\s+", raw)[-1].strip()
    aliases = _aliases()
    key = _normalise(short)
    exact = aliases.get(key)
    if exact:
        return exact

    # Match station prefixes such as "CRU-L4" or "Seraphim".
    for alias, full in aliases.items():
        if len(key) >= 4 and alias.startswith(key):
            return full
    return short
