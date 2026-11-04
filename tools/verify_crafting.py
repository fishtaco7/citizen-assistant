"""Audit the generated crafting data against the calculator.

Checks every recipe for segment gaps, overlaps and mixed aggregation modes, then
prints one representative blueprint per property at stock (500) and perfect
(1000) quality, so the numbers can be compared with a crafting site.

    python -m tools.verify_crafting [path/to/blueprint_crafting.json]

Exit code is non-zero if the audit or the reference check fails.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from app import blueprint_calc

DEFAULT = (Path(__file__).resolve().parent.parent
           / "app" / "data" / "blueprint_crafting.json")


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle).get("crafting") or {}


def _modes(key: str) -> str:
    normalised = "".join(character for character in key.casefold()
                         if character.isalnum())
    if normalised in blueprint_calc._STACKED_KEYS:
        return "stacked"
    return "additive"


def _family(key: str) -> str:
    """Group keys into comparable families; coolant and power are separate."""
    if key.startswith("itemresource_"):
        return key
    return key.split("_")[0]


def _audit(crafting: dict) -> tuple[list[str], dict[str, dict]]:
    problems: list[str] = []
    seen: dict[str, dict] = {}
    slots = 0

    for name, entry in sorted(crafting.items()):
        for tier_index, tier in enumerate(entry.get("tiers") or []):
            for material in tier.get("materials") or []:
                slots += 1
                groups: dict[str, list[dict]] = defaultdict(list)
                for modifier in material.get("modifiers") or []:
                    groups[modifier["property"]].append(modifier)

                for key, group in groups.items():
                    group.sort(key=lambda modifier: modifier["start"])
                    where = f"{name} T{tier_index + 1} {material.get('slot')}"
                    if group[0]["start"] > 0:
                        problems.append(
                            f"{where}: {key} starts at {group[0]['start']}, "
                            "not 0")
                    if group[-1]["end"] < 1000:
                        problems.append(
                            f"{where}: {key} ends at {group[-1]['end']}, "
                            "not 1000")
                    for lower, upper in zip(group, group[1:]):
                        if upper["start"] > lower["end"] + 1:
                            problems.append(
                                f"{where}: {key} gap "
                                f"{lower['end']}-{upper['start']}")
                        elif upper["start"] <= lower["end"]:
                            problems.append(
                                f"{where}: {key} overlap "
                                f"{upper['start']}-{lower['end']}")
                    if len({bool(item.get("additive")) for item in group}) > 1:
                        problems.append(
                            f"{where}: {key} mixes additive and multiplier")

                    info = seen.setdefault(key, {
                        "name": group[0].get("name") or key,
                        "slots": 0,
                        "low": group[0]["atStart"],
                        "high": group[-1]["atEnd"],
                        "additive": bool(group[0].get("additive")),
                    })
                    info["slots"] += 1

    return problems, {"slot_count": slots, "keys": seen}


def _report(modes: dict[str, dict], problems: list[str]) -> None:
    keys = modes["keys"]
    print(f"\n{modes['slot_count']:,} material slots, "
          f"{len(keys)} distinct properties\n")
    print(f"{'PROPERTY':<34} {'SLOTS':>6} {'FROM':>7} {'TO':>7}  MODE")
    for key in sorted(keys):
        info = keys[key]
        mode = "absolute" if info["additive"] else _modes(key)
        print(f"{key:<34} {info['slots']:>6} {info['low']:>7.2f} "
              f"{info['high']:>7.2f}  {mode}")
        print(f"  {info['name']}")

    if problems:
        print(f"\n{len(problems)} anomalies (first 15):")
        for problem in problems[:15]:
            print(f"  {problem}")
    else:
        print("\nSegments: every property covers 0-1000 with no gaps, "
              "overlaps or mixed modes")


def _total(crafting: dict, name: str, qualities: list[int]) -> str:
    entry = crafting.get(name)
    if not entry:
        return f"{name}: missing"
    tier = (entry.get("tiers") or [{}])[0]
    materials = tier.get("materials") or []
    if len(materials) != len(qualities):
        qualities = qualities + [500] * (len(materials) - len(qualities))
    stats = blueprint_calc.changes(materials, qualities[:len(materials)])
    quality = blueprint_calc.average_quality(qualities[:len(materials)],
                                             materials)
    parts = []
    for entry_stat in sorted(stats.values(), key=lambda item: item["name"]):
        if entry_stat["percent"] is None:
            parts.append(f"{entry_stat['name']} {entry_stat['delta']:+.0f}")
        else:
            parts.append(f"{entry_stat['name']} {entry_stat['percent']:+.2f}%")
    return f"{name}: craft {quality:,.0f} · " + " · ".join(parts)


def _reference(crafting: dict) -> bool:
    """The one externally confirmed value: sc-craft.tools on the JS-500."""
    entry = crafting.get("JS-500")
    if not entry:
        print("\nReference check: JS-500 not in this file (skipped)")
        return True
    materials = (entry.get("tiers") or [{}])[0].get("materials") or []
    qualities = ([834, 759, 735] + [500] * len(materials))[:len(materials)]
    stats = blueprint_calc.changes(materials, qualities)
    integrity = stats.get("health_maxhealth", {}).get("percent")
    pips = stats.get("itemresource_powergeneration", {}).get("delta")
    ok = (integrity is not None and abs(integrity - 13.3467) < 0.01
          and pips == 2)
    print("\nReference check (sc-craft.tools, JS-500 at 834/759/735)")
    print(f"  Integrity  {integrity if integrity is None else round(integrity, 4)}"
          "  expected +13.35%")
    print(f"  Power Pips {pips if pips is None else round(pips, 0)}"
          "  expected +2")
    print("  PASS" if ok else "  FAIL")
    return ok


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv else DEFAULT
    crafting = _load(path)
    problems, modes = _audit(crafting)
    _report(modes, problems)

    print("\nRepresentative blueprints (stock 500, then perfect 1000)")
    seen: set[str] = set()
    for name in sorted(crafting):
        entry = crafting[name]
        keys = {modifier["property"]
                for tier in entry.get("tiers") or []
                for material in tier.get("materials") or []
                for modifier in material.get("modifiers") or []}
        family = {_family(key) for key in keys}
        marker = ",".join(sorted(family))
        if not marker or marker in seen:
            continue
        seen.add(marker)
        count = len((entry.get("tiers") or [{}])[0].get("materials") or [])
        print("  " + _total(crafting, name, [500] * count))
        print("  " + _total(crafting, name, [1000] * count))

    ok = _reference(crafting)
    if problems or not ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
