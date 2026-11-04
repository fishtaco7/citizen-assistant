"""Build blueprint_sets.json from SCMDB's merged game data.

    python -m tools.scmdb_import ~/Documents/projects/scmdb/merged.json

SCMDB publishes a merged database dump per patch. Contracts carry
``blueprintRewards[].blueprintPool`` and ``blueprintPools`` names the blueprints
each pool can award, which is exactly the mission -> blueprints relation the
overlay needs for progress and lookup.

The result is written to ``%APPDATA%/Citizen Assistant/blueprint_sets.json`` by
default, the same file the popup and the BLUEPRINTS tab read. Pass ``--out`` to
write somewhere else.

Data belongs to SCMDB (https://scmdb.net); the generated file records the
source. Re-run this after a patch and the mapping is current again.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _rank_index(data: dict) -> dict[str, tuple[str, float]]:
    """nameKey -> (rank name, minimum reputation) from the scopes table."""
    index: dict[str, tuple[str, float]] = {}
    for scope in (data.get("scopes") or {}).values():
        if not isinstance(scope, dict):
            continue
        for rank in scope.get("ranks") or []:
            if not isinstance(rank, dict):
                continue
            key = str(rank.get("nameKey") or "").strip()
            if not key:
                continue
            try:
                value = float(rank.get("minReputation") or 0)
            except (TypeError, ValueError):
                value = 0.0
            entry = (str(rank.get("name") or "").strip(), value)
            index[key] = entry
            index[key.lstrip("@")] = entry
    return index


def _reputation(contract: dict,
                ranks: dict[str, tuple[str, float]]) -> dict:
    """Faction, rank, reputation value and display text for the gate."""
    subs = contract.get("tokenSubstitutions") or {}
    rank_key = str(subs.get("ReputationRank") or "").strip()
    rank_name, rank_rep = ranks.get(rank_key) or ranks.get(
        rank_key.lstrip("@")) or ("", 0.0)

    faction = ""
    for lock in contract.get("reputationLockout") or []:
        if isinstance(lock, dict):
            faction = str(lock.get("factionName") or "").strip()
            if faction:
                break
    if not faction:
        faction = str(contract.get("contractorName") or "").strip()

    if not rank_name:
        # No explicit rank: fall back to the minimum standing in the lockout.
        for lock in contract.get("reputationLockout") or []:
            if not isinstance(lock, dict):
                continue
            standing = lock.get("minStanding") or {}
            name = str(standing.get("name") or "").strip()
            if name:
                rank_name = name
                try:
                    rank_rep = float(standing.get("minReputation") or 0)
                except (TypeError, ValueError):
                    rank_rep = 0.0
                break

    text = f"{faction} {rank_name}".strip() if faction or rank_name else ""
    if text and rank_rep:
        text = f"{text} ({int(rank_rep)} rep)"
    return {"faction": faction, "rank": rank_name,
            "reputation": rank_rep, "text": text}


def _places(contract: dict, index: dict) -> tuple[list[str], list[str]]:
    """(place names, systems) where the mission can be picked up.

    ``locations`` is where the contract is offered, which is what matters for a
    blueprint drop. ``destinations`` only stands in when a contract has no
    offering location, and unresolved ids or placeholder names are skipped.
    """
    places: list[str] = []
    systems: list[str] = []
    for field in ("locations", "destinations"):
        for identifier in contract.get(field) or []:
            entry = index.get(str(identifier))
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if name and not name.startswith("<=") and name not in places:
                places.append(name)
            system = str(entry.get("system") or "").strip()
            if system and system not in systems:
                systems.append(system)
        if places:
            break
    return places, systems


def _entity_names(data: dict) -> dict[str, str]:
    """entityClass GUID -> blueprint display name, taken from the reward pools."""
    names: dict[str, str] = {}
    for pool in (data.get("blueprintPools") or {}).values():
        if not isinstance(pool, dict):
            continue
        for entry in pool.get("blueprints") or []:
            if not isinstance(entry, dict):
                continue
            entity = str(entry.get("entityClass") or "").strip().lower()
            name = str(entry.get("name") or "").strip()
            if entity and name:
                names.setdefault(entity, name)
    return names


def _pool_names(data: dict) -> set[str]:
    """Every blueprint name that at least one pool can award.

    The crafting dump also defines recipes for variants that were never hooked
    into a pool (P6-LR "Archangel", A03 "Canuto", the ADP colour runs). Nothing
    in the game awards those, so they are not blueprints yet.
    """
    available: set[str] = set()
    for pool in (data.get("blueprintPools") or {}).values():
        if not isinstance(pool, dict):
            continue
        for entry in pool.get("blueprints") or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if name:
                available.add(name.casefold())
    return available


def _number(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _number_or(value, fallback):
    """Like _number, but keeps a genuine 0 instead of treating it as missing."""
    parsed = _number(value)
    return fallback if parsed is None else parsed


def build_crafting(recipes: dict,
                   names: dict[str, str],
                   available: set[str] | None = None,
                   ) -> tuple[dict, int, int]:
    """blueprint name -> tiers/materials.

    Returns (crafting, unmatched, unreleased). ``available`` marks each recipe
    with ``awarded``; recipes outside it are still kept so the data stays
    complete, and the app hides them.
    """
    crafting: dict[str, dict] = {}
    unmatched = 0
    unreleased = 0

    for blueprint in recipes.get("blueprints") or []:
        if not isinstance(blueprint, dict):
            continue
        entity = str(blueprint.get("productEntityClass") or "").strip().lower()
        name = (str(blueprint.get("productName") or "").strip()
                or names.get(entity))
        if not name:
            unmatched += 1
            continue
        awarded = available is None or name.casefold() in available
        if not awarded:
            unreleased += 1

        tiers: list[dict] = []
        for tier in blueprint.get("tiers") or []:
            if not isinstance(tier, dict):
                continue
            materials: list[dict] = []
            for slot in tier.get("slots") or []:
                parsed: list[dict] = []
                for option in (slot or {}).get("options") or []:
                    if not isinstance(option, dict):
                        continue
                    option_name = str(option.get("resourceName")
                                      or option.get("itemName")
                                      or "").strip()
                    if not option_name:
                        continue
                    parsed.append({
                        "type": str(option.get("type") or "").strip(),
                        "name": option_name,
                        "quantity": _number(option.get("quantity")),
                        "minQuality": _number(option.get("minQuality")),
                    })
                if not parsed:
                    continue
                modifiers = []
                for modifier in slot.get("modifiers") or []:
                    if not isinstance(modifier, dict):
                        continue
                    key = str(modifier.get("propertyKey") or "").strip()
                    if not key:
                        continue
                    modifiers.append({
                        "property": key,
                        "name": str(modifier.get("propertyName") or "").strip(),
                        "start": _number_or(modifier.get("startQuality"), 0.0),
                        "end": _number_or(modifier.get("endQuality"), 1000.0),
                        "atStart": _number_or(modifier.get("modifierAtStart"), 1.0),
                        "atEnd": _number_or(modifier.get("modifierAtEnd"), 1.0),
                        "additive": bool(modifier.get("additive")),
                    })
                # Keep the first option at the top level so older readers and the
                # list summary keep working; only store the full set when there
                # is a choice to offer.
                first = parsed[0]
                material = {
                    "slot": str(slot.get("name") or "").strip(),
                    "type": first["type"],
                    "name": first["name"],
                    "quantity": first["quantity"],
                    "minQuality": first["minQuality"],
                    "modifiers": modifiers,
                }
                if len(parsed) > 1:
                    material["options"] = parsed
                materials.append(material)
            tiers.append({
                "craftTime": tier.get("craftTimeSeconds"),
                "materials": materials,
            })

        if tiers:
            crafting[name] = {
                "tag": str(blueprint.get("tag") or "").strip(),
                "awarded": awarded,
                "tiers": tiers,
            }

    return crafting, unmatched, unreleased


def build(data: dict) -> dict[str, dict]:
    """{set name: {source, requires, blueprints[]}} from a merged SCMDB dump."""
    pools = data.get("blueprintPools") or {}
    contracts = data.get("contracts") or []
    places_index = data.get("locationPools") or {}
    ranks = _rank_index(data)
    sets: dict[str, dict] = {}

    for contract in contracts:
        rewards = contract.get("blueprintRewards") or []
        if not rewards:
            continue

        names: list[str] = []
        for reward in rewards:
            pool = pools.get(str(reward.get("blueprintPool") or ""))
            if not pool:
                continue
            for entry in pool.get("blueprints") or []:
                name = str(entry.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
        if not names:
            continue

        title = str(contract.get("title") or contract.get("debugName")
                    or "Contract").strip()
        contractor = str(contract.get("contractorName") or "").strip()
        rep = _reputation(contract, ranks)
        faction = rep["faction"]
        requires = rep["text"]

        # Where the mission can be picked up. The resolved location ids name the
        # actual outposts/stations; fall back to the prerequisite's label (often
        # just the system) when a contract has no ids.
        places, systems = _places(contract, places_index)
        if not places:
            for place in (contract.get("prerequisites") or {}).get("location") or []:
                if not isinstance(place, dict):
                    continue
                label = str(place.get("name") or "").strip()
                if label and label not in places:
                    places.append(label)
        for name in contract.get("availableSystems") or []:
            text = str(name).strip()
            if text and text not in systems:
                systems.append(text)
        system = ", ".join(systems)

        # Keep rank variants apart: the same title at a different standing
        # awards a different pool, so merging them would blur the progress.
        rank_hint = (requires.replace(f"{faction} ", "", 1)
                     if faction else requires)
        key = f"{contractor}: {title}" if contractor else title
        if rank_hint:
            key = f"{key} · {rank_hint}"

        bits = [str(contract.get("missionType") or "").strip(), contractor,
                str(contract.get("debugName") or "").strip()]
        source = "  ·  ".join(bit for bit in bits if bit)

        entry = sets.setdefault(key, {"blueprints": []})
        for field, value in (
                ("source", source),
                ("requires", requires),
                ("faction", faction),
                ("rank", rep["rank"]),
                ("reputation", rep["reputation"]),
                ("system", system),
                ("places", places[:6]),
                ("title", title),
                ("missionType", str(contract.get("missionType") or "").strip())):
            if value and not entry.get(field):
                entry[field] = value
        for name in names:
            if name not in entry["blueprints"]:
                entry["blueprints"].append(name)

    return sets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert an SCMDB merged dump into blueprint sets")
    parser.add_argument("merged", type=Path, help="merged-<version>.json")
    parser.add_argument("--out", type=Path, default=None,
                        help="destination (default: this checkout's app/data)")
    parser.add_argument("--recipes", type=Path, default=None,
                        help="crafting_blueprints-<version>.json, adds materials")
    parser.add_argument("--include-unreleased", action="store_true",
                        help="treat every definition as awarded")
    args = parser.parse_args(argv)

    with open(args.merged, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    sets = build(data)
    if not sets:
        print("No contracts with blueprint rewards found", file=sys.stderr)
        return 1

    out = args.out or (Path(__file__).resolve().parent.parent
                       / "app" / "data" / "blueprint_sets.json")
    payload = {
        "note": ("Generated by tools/scmdb_import.py from SCMDB "
                 f"(https://scmdb.net) game data, file {args.merged.name}. "
                 "Re-run after a patch to refresh."),
        "sets": sets,
        "blueprints": {},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    total = sum(len(entry["blueprints"]) for entry in sets.values())
    print(f"{len(sets)} sets, {total} blueprint assignments -> {out}")

    if args.recipes:
        with open(args.recipes, "r", encoding="utf-8") as handle:
            recipes = json.load(handle)
        available = None if args.include_unreleased else _pool_names(data)
        crafting, unmatched, unreleased = build_crafting(
            recipes, _entity_names(data), available)
        source = recipes.get("blueprints") or []
        craft_out = out.parent / "blueprint_crafting.json"
        craft_out.parent.mkdir(parents=True, exist_ok=True)
        craft_out.write_text(json.dumps({
            "note": ("Generated by tools/scmdb_import.py from SCMDB "
                     f"(https://scmdb.net) crafting data, file "
                     f"{args.recipes.name}."),
            "crafting": crafting,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(crafting)} recipes from {len(source)} blueprints "
              f"({unmatched} unnamed, {unreleased} unreleased but kept) "
              f"-> {craft_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
