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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .config import config_dir

SHIPPED = Path(__file__).with_name("data") / "blueprint_sets.json"
USER_NAME = "blueprint_sets.json"


def _key(value: object) -> str:
    return str(value or "").strip().casefold()


def _log(message: str) -> None:
    """Diagnostics go next to error.log; never let logging break loading."""
    try:
        path = config_dir() / "error.log"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"[blueprint_sets] {message}\n")
    except OSError:
        pass


@dataclass(frozen=True)
class SetInfo:
    """One mission/pool: its name, its members and where it comes from."""

    name: str
    blueprints: tuple[str, ...]
    source: str = ""
    requires: str = ""
    faction: str = ""
    rank: str = ""
    reputation: float = 0.0
    system: str = ""
    location: str = ""
    title: str = ""


class BlueprintSets:
    """Blueprint name -> set name, plus membership for progress counts."""

    def __init__(self, path: Path | str | None = None):
        self.path: Path | None = None
        self.error = ""
        self._set_of: dict[str, str] = {}
        self._members: dict[str, list[str]] = {}
        self._source: dict[str, str] = {}
        self._requires: dict[str, str] = {}
        self._faction: dict[str, str] = {}
        self._rank: dict[str, str] = {}
        self._reputation: dict[str, float] = {}
        self._system: dict[str, str] = {}
        self._location: dict[str, str] = {}
        self._title: dict[str, str] = {}
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
            _log(f"{candidate} -> {len(self._set_of)} names, "
                 f"{len(self._members)} sets"
                 + (f", error={self.error}" if self.error else ""))
            return
        self.path = None
        _log("no blueprint_sets.json found")

    def _read(self, path: Path) -> None:
        self._set_of.clear()
        self._members.clear()
        self._source.clear()
        self._requires.clear()
        self._faction.clear()
        self._rank.clear()
        self._reputation.clear()
        self._system.clear()
        self._location.clear()
        self._title.clear()
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
            for name, value in sets.items():
                set_name = str(name).strip()
                if not set_name:
                    continue
                source = ""
                requires = ""
                faction = ""
                rank = ""
                reputation = 0.0
                system = ""
                location = ""
                title = ""
                if isinstance(value, Mapping):
                    source = str(value.get("source") or "").strip()
                    requires = str(value.get("requires") or "").strip()
                    faction = str(value.get("faction") or "").strip()
                    rank = str(value.get("rank") or "").strip()
                    system = str(value.get("system") or "").strip()
                    location = str(value.get("location") or "").strip()
                    title = str(value.get("title") or "").strip()
                    try:
                        reputation = float(value.get("reputation") or 0.0)
                    except (TypeError, ValueError):
                        reputation = 0.0
                    members = value.get("blueprints")
                else:
                    members = value
                if not isinstance(members, list):
                    members = []
                self._members.setdefault(set_name, [])
                if source:
                    self._source[set_name] = source
                if requires:
                    self._requires[set_name] = requires
                if faction:
                    self._faction[set_name] = faction
                if rank:
                    self._rank[set_name] = rank
                if reputation:
                    self._reputation[set_name] = reputation
                if system:
                    self._system[set_name] = system
                if location:
                    self._location[set_name] = location
                if title:
                    self._title[set_name] = title
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

    def source_for(self, set_name: str) -> str:
        return self._source.get(set_name, "")

    def requires_for(self, set_name: str) -> str:
        return self._requires.get(set_name, "")

    def info_for(self, set_name: str) -> SetInfo | None:
        for info in self.catalogue():
            if info.name == set_name:
                return info
        return None

    def catalogue(self) -> list[SetInfo]:
        """Every known set, for the BLUEPRINTS tab."""
        return [SetInfo(name=name,
                        blueprints=tuple(members),
                        source=self._source.get(name, ""),
                        requires=self._requires.get(name, ""),
                        faction=self._faction.get(name, ""),
                        rank=self._rank.get(name, ""),
                        reputation=self._reputation.get(name, 0.0),
                        system=self._system.get(name, ""),
                        location=self._location.get(name, ""),
                        title=self._title.get(name, ""))
                for name, members in self._members.items()]

    def progress(self, set_name: str,
                 unlocked: Iterable[str]) -> tuple[int, int]:
        """(how many of the set are unlocked, size of the set)."""
        members = self._members.get(set_name) or []
        if not members:
            return 0, 0
        have = {_key(name) for name in unlocked}
        return sum(1 for member in members if _key(member) in have), len(members)
