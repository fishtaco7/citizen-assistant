"""Optional mission/set grouping for blueprints.

``Game.log`` only says ``Received Blueprint: <name>:``: it never names the
mission or the pool the blueprint came from. A progress line like ``3 of 8``
therefore needs a mapping, and that is community data rather than something the
game exposes.

Two files are consulted, first match wins:

1. ``%APPDATA%\\Citizen Assistant\\blueprint_sets.json`` - your own edits, no
   install touched, survives updates.
2. ``app/data/blueprint_sets.json`` - shipped, empty by default.

Both forms may be used, together or separately::

    {
      "sets": {
        "Covalex Supply Run": ["Defiance Helmet Tactical", "Pembroke RSI"]
      },
      "blueprints": {
        "Some Other Blueprint": "Vaughn's Salvage"
      }
    }

``sets`` lists a pool in full; ``blueprints`` assigns one name at a time. Names
are matched case-insensitively with surrounding whitespace ignored, because the
log line and any list you copy will rarely agree on capitalisation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

from .config import config_dir

SHIPPED = Path(__file__).with_name("data") / "blueprint_sets.json"
USER_NAME = "blueprint_sets.json"


def _key(value: object) -> str:
    return str(value or "").strip().casefold()


class BlueprintSets:
    """Blueprint name -> set name, plus membership for progress counts."""

    def __init__(self, path: Path | str | None = None):
        self.path: Path | None = None
        self.error = ""
        self._set_of: dict[str, str] = {}
        self._members: dict[str, list[str]] = {}
        self.load(path)

    # ------------------------------------------------------------------
    def load(self, path: Path | str | None = None) -> None:
        candidates = ([Path(path).expanduser()] if path is not None
                      else [config_dir() / USER_NAME, SHIPPED])
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
        self._set_of.clear()
        self._members.clear()
        self.error = ""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError) as exc:
            self.error = f"Could not read {path.name}: {exc}"
            return
        if not isinstance(raw, Mapping):
            self.error = f"{path.name} must contain an object"
            return

        sets = raw.get("sets")
        if isinstance(sets, Mapping):
            for name, members in sets.items():
                set_name = str(name).strip()
                if not set_name or not isinstance(members, list):
                    continue
                self._members.setdefault(set_name, [])
                for member in members:
                    text = str(member).strip()
                    if not text:
                        continue
                    self._members[set_name].append(text)
                    self._set_of.setdefault(_key(text), set_name)

        single = raw.get("blueprints")
        if isinstance(single, Mapping):
            for blueprint, set_name in single.items():
                text = str(blueprint).strip()
                target = str(set_name).strip()
                if not text or not target:
                    continue
                self._set_of[_key(text)] = target
                self._members.setdefault(target, [])

    # ------------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return bool(self._set_of)

    def set_for(self, blueprint: str) -> str | None:
        return self._set_of.get(_key(blueprint))

    def progress(self, set_name: str,
                 unlocked: Iterable[str]) -> tuple[int, int]:
        """(how many of the set are unlocked, size of the set)."""
        members = self._members.get(set_name) or []
        if not members:
            return 0, 0
        have = {_key(name) for name in unlocked}
        return sum(1 for member in members if _key(member) in have), len(members)
