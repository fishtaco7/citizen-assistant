"""Signature -> mineral lookup.

Ship mining:   scan signature = base signature x number of rocks in the cluster.
               The base signature identifies the dominant mineral.
Ground mining: signature only encodes deposit size (ROC 4000, FPS 3000),
               multiplied by the number of deposits.
Salvage:       exterior hull panels / wreck scrap read as multiples of 2000.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA_PATH = Path(__file__).with_name("data") / "signatures.json"

MAX_CLUSTER = 10
MAX_GROUND_CLUSTER = 8
# Debris is not a rock cluster: each hull panel contributes 2,000 to the
# signature, so a large wreck can read far beyond a 10-rock cluster. Capping
# this at MAX_CLUSTER made 22,000 (11 panels) fall through to a fuzzy
# Quantanium match instead of being recognised as salvage.
MAX_DEBRIS_CLUSTER = 50


@dataclass(frozen=True)
class Match:
    kind: str            # "ship" | "roc" | "fps"
    name: str
    count: int
    target: int
    delta: int
    rarity: int
    value: float | None
    secondary: str | None
    short: str = ""
    # True when another reading of the same signature is equally exact, e.g.
    # 16,000 = Savrilium x5 = eight debris panels. The headline still prefers
    # the ore, but nothing should be announced as fact.
    ambiguous: bool = False

    @property
    def exact(self) -> bool:
        return self.delta == 0

    @property
    def label(self) -> str:
        if self.kind == "ship":
            return self.name
        if self.kind == "debris":
            return "Debris"
        return "ROC deposit" if self.kind == "roc" else "FPS deposit"

    @property
    def is_ground(self) -> bool:
        return self.kind in ("roc", "fps")


class MiningData:
    def __init__(self, path: Path = DATA_PATH):
        self.path = path
        self.version = "?"
        self.source = ""
        self.ores: list[dict] = []
        self.max_cluster = MAX_CLUSTER
        self.ground_max_cluster = MAX_GROUND_CLUSTER
        self.roc_base = 4000
        self.fps_base = 3000
        self.ground_minerals: list[str] = []
        self.debris_base = 2000
        self.debris_max_cluster = MAX_CLUSTER
        self.debris_name = "Debris"
        self.debris_detail = "HULL PANELS / SCRAP METAL"
        self.reload()

    def reload(self) -> None:
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.version = raw.get("version", "?")
        self.source = raw.get("source", "")
        self.max_cluster = int(raw.get("max_cluster", MAX_CLUSTER))
        self.ores = sorted(raw.get("ship_ores", []), key=lambda o: o["base"])
        ground = raw.get("ground", {})
        self.ground_max_cluster = int(
            ground.get("max_cluster", MAX_GROUND_CLUSTER))
        self.roc_base = int(ground.get("roc_base", 4000))
        self.fps_base = int(ground.get("fps_base", 3000))
        self.ground_minerals = list(ground.get("minerals", []))
        debris = raw.get("debris", {})
        self.debris_base = int(debris.get("base", 2000))
        self.debris_max_cluster = int(debris.get("max_cluster", MAX_DEBRIS_CLUSTER))
        self.debris_name = debris.get("name", "Debris")
        self.debris_detail = debris.get("detail", "HULL PANELS / SCRAP METAL")

    # ------------------------------------------------------------------
    def lookup(self, value: int, tolerance: float = 0.08) -> list[Match]:
        """Return every plausible interpretation of a scan signature."""
        if value <= 0:
            return []

        matches: list[Match] = []
        for ore in self.ores:
            base = int(ore["base"])
            for count in range(1, self.max_cluster + 1):
                target = base * count
                delta = abs(value - target)
                if delta <= max(35, int(round(target * tolerance))):
                    matches.append(Match(
                        kind="ship",
                        name=ore["name"],
                        short=ore.get("short", ""),
                        count=count,
                        target=target,
                        delta=delta,
                        rarity=int(ore.get("rarity", 1)),
                        value=ore.get("value"),
                        secondary=ore.get("secondary"),
                    ))

        for kind, base in (("roc", self.roc_base), ("fps", self.fps_base)):
            count = round(value / base)
            if count < 1 or count > self.ground_max_cluster:
                continue
            target = base * count
            delta = abs(value - target)
            if delta <= max(35, int(round(target * tolerance))):
                matches.append(Match(
                    kind=kind,
                    name="Ground deposit",
                    count=count,
                    target=target,
                    delta=delta,
                    rarity=0,
                    value=None,
                    secondary=None,
                ))

        # Salvage: exterior hull panels and wreck scrap read as clean
        # multiples of 2,000, and in practice a signature that divides evenly by
        # 2,000 is debris even when an ore cluster could also produce it
        # (16,000 = Savrilium x5). Debris therefore wins exact ties, and the ore
        # reading is kept as an alternative in the detail line.
        if self.debris_base > 0 and value % self.debris_base == 0:
            count = value // self.debris_base
            if 1 <= count <= self.debris_max_cluster:
                alternatives: list[str] = []
                ship = next((m for m in matches
                             if m.kind == "ship" and m.exact), None)
                if ship is not None:
                    alternatives.append(f"{ship.name.upper()} ×{ship.count}")
                ground = next((m for m in matches
                               if m.is_ground and m.exact), None)
                if ground is not None:
                    alternatives.append(f"{ground.label.upper()} ×{ground.count}")

                detail = self.debris_detail
                if alternatives:
                    detail = (f"{detail}  ·  OR "
                              + " / ".join(alternatives))
                matches.append(Match(
                    kind="debris",
                    name=self.debris_name,
                    count=count,
                    target=value,
                    delta=0,
                    rarity=1,
                    value=None,
                    secondary=detail,
                    ambiguous=bool(alternatives),
                ))

        # Debris wins exact ties: a signature that is a clean multiple of 2,000
        # is salvage in practice, even when an ore cluster divides into it.
        matches.sort(key=lambda m: (not m.exact, m.delta, m.is_ground,
                                    0 if m.kind == "debris" else 1, -m.rarity))
        # In-game signatures are exact multiples of the base value, so an
        # exact hit is authoritative. Only fall back to fuzzy matches when
        # nothing lands exactly (usually an OCR misread).
        exact = [m for m in matches if m.exact]
        if exact:
            return exact
        return matches

    def nearest(self, value: int, limit: int = 3) -> list[Match]:
        """Closest signatures when nothing matches exactly (OCR sanity check)."""
        pool: list[Match] = []
        for ore in self.ores:
            base = int(ore["base"])
            count = max(1, min(self.max_cluster, round(value / base)))
            target = base * count
            pool.append(Match(
                kind="ship", name=ore["name"], short=ore.get("short", ""),
                count=count, target=target, delta=abs(value - target),
                rarity=int(ore.get("rarity", 1)), value=ore.get("value"),
                secondary=ore.get("secondary"),
            ))
        pool.sort(key=lambda m: m.delta)
        return pool[:limit]

    # ------------------------------------------------------------------
    def ore_by_name(self, name: str) -> dict | None:
        for ore in self.ores:
            if ore["name"].lower() == name.lower():
                return ore
        return None
