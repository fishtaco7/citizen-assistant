"""Crafting recipes for blueprints, generated from SCMDB game data.

The importer writes this next to ``blueprint_sets.json``. It is optional: if the
file is missing the BLUEPRINTS tab simply shows no recipe line, and everything
else keeps working.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import config_dir

FILE_NAME = "blueprint_crafting.json"
SHIPPED = Path(__file__).with_name("data") / FILE_NAME


def _key(value: object) -> str:
    return str(value or "").strip().casefold()


class CraftingData:
    """Blueprint name -> tiers of materials.

    Like the mission mapping, the file is looked up in the config folder first
    and then in the bundled data, so a locally generated copy wins over the one
    that shipped with the app.
    """

    def __init__(self, path: Path | str | None = None):
        self.path: Path | None = None
        self._by_name: dict[str, dict] = {}
        self.loaded = False
        self.load(path)

    def load(self, path: Path | str | None = None) -> None:
        candidates = ([Path(path).expanduser()] if path is not None
                      else [config_dir() / FILE_NAME, SHIPPED])
        for candidate in candidates:
            try:
                if not candidate.is_file():
                    continue
            except OSError:
                continue
            self.path = candidate
            self._read(candidate)
            return
        self.path = None

    def _read(self, path: Path) -> None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            self._by_name = {}
            self.loaded = False
            return
        entries = raw.get("crafting") if isinstance(raw, dict) else None
        self._by_name = {}
        if isinstance(entries, dict):
            for name, entry in entries.items():
                key = _key(name)
                if key and isinstance(entry, dict):
                    stored = dict(entry)
                    stored["name"] = str(name)
                    self._by_name[key] = stored
        self.loaded = bool(self._by_name)

    # ------------------------------------------------------------------
    def get(self, name: str) -> dict | None:
        return self._by_name.get(_key(name))

    def awarded(self, name: str) -> bool:
        """True when a blueprint pool can grant this recipe.

        Older files predate the flag, so treat a missing value as awarded.
        """
        entry = self.get(name)
        return bool(entry.get("awarded", True)) if entry else False

    def names(self) -> list[str]:
        return [str(entry.get("name"))
                for entry in self._by_name.values() if entry.get("name")]

    def summary(self, name: str, tier: int = 0) -> str:
        """'2 Titanium · 2 Riccite · 4 Yormandi Eye' for one tier."""
        entry = self.get(name)
        if not entry:
            return ""
        tiers = entry.get("tiers") or []
        if not isinstance(tiers, list) or tier >= len(tiers):
            return ""
        bits: list[str] = []
        for material in tiers[tier].get("materials") or []:
            if not isinstance(material, dict):
                continue
            label = str(material.get("name") or material.get("slot") or "").strip()
            if not label:
                continue
            quantity = material.get("quantity")
            if isinstance(quantity, (int, float)) and quantity:
                label = f"{quantity:g} {label}"
            bits.append(label)
        return "  ·  ".join(bits)

    def tier_count(self, name: str) -> int:
        entry = self.get(name)
        tiers = (entry or {}).get("tiers") or []
        return len(tiers) if isinstance(tiers, list) else 0
