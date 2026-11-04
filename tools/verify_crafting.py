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
    return "multiplied"


def _family(key: str) -> str:
    """Group keys into comparable families; coolant and power are separate."""
    if key.startswith("itemresource_"):
        return key
    return key.split("_")[0]


def _audit(crafting: dict) -> tuple[list[str], list[str], dict[str, dict]]:
    problems: list[str] = []
    notes: list[str] = []
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
                    # A curve may legitimately only start at 500 (the stat holds
                    # its first value below that), so this is a note, not a bug.
                    if group[0]["start"] > 0:
                        notes.append(
                            f"{where}: {key} starts at {group[0]['start']}")
                    if group[-1]["end"] < 1000:
                        notes.append(
                            f"{where}: {key} ends at {group[-1]['end']}")
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

    return problems, notes, {"slot_count": slots, "keys": seen}


def _report(modes: dict[str, dict], problems: list[str],
            notes: list[str]) -> None:
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
        print(f"\n{len(problems)} problems (first 15):")
        for problem in problems[:15]:
            print(f"  {problem}")
    else:
        print("\nSegments: no gaps, no overlaps, no mixed modes")

    if notes:
        print(f"\n{len(notes)} curves start above quality 0 or end below 1000; "
              "the modifier simply holds at its first or last value there")
        for note in notes[:5]:
            print(f"  {note}")


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


def _coverage(crafting: dict, sets_path: Path) -> None:
    """How many recipes no mission pool awards (unreleased variants)."""
    try:
        raw = json.loads(sets_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    known: set[str] = set()
    for entry in (raw.get("sets") or {}).values():
        members = entry.get("blueprints") if isinstance(entry, dict) else entry
        for name in members or []:
            known.add(str(name).strip().casefold())
    unsourced = [name for name in crafting
                 if name.strip().casefold() not in known]
    print(f"\nAvailability: {len(crafting) - len(unsourced):,} recipes are "
          f"awarded by a mission pool, {len(unsourced):,} are not")
    for name in sorted(unsourced)[:8]:
        print(f"  no source: {name}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv else DEFAULT
    crafting = _load(path)
    problems, notes, modes = _audit(crafting)
    _report(modes, problems, notes)
    _coverage(crafting, path.with_name("blueprint_sets.json"))

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
