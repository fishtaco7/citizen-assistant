"""Audit every blueprint for missing recipe, stat or unlock information.

    python -m tools.audit_blueprints [data_dir]

Reads app/data/blueprint_sets.json and blueprint_crafting.json and reports:
recipes missing for a claimed blueprint, blueprints missing recipes,
recipes with no materials or no stat modifiers, and mission pools missing
faction, rank, reputation or location. Structural gaps exit non-zero; cosmetic
ones are listed as warnings.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "app" / "data"


def _load(path: Path, key: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle).get(key) or {}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    folder = Path(argv[0]) if argv else DEFAULT
    crafting = _load(folder / "blueprint_crafting.json", "crafting")
    sets = _load(folder / "blueprint_sets.json", "sets")

    claims: dict[str, list[tuple[str, dict]]] = {}
    for set_name, entry in sets.items():
        members = entry.get("blueprints") if isinstance(entry, dict) else entry
        for member in members or []:
            name = str(member).strip()
            if name:
                claims.setdefault(name.casefold(), []).append((set_name, entry))

    recipes = {name.casefold(): name for name in crafting}

    # -- recipes a mission pool claims but the crafting file does not have
    missing_recipe = []
    for key, entries in claims.items():
        if key not in recipes:
            missing_recipe.append((entries[0][0], key))

    # -- recipes that no mission pool claims (should be empty after the filter)
    unsourced = [name for key, name in recipes.items() if key not in claims]

    # -- recipe structure
    empty_recipe = []
    no_stats = []
    empty_material = []
    zero_quantity = []
    for name, entry in crafting.items():
        tiers = entry.get("tiers") or []
        materials = [material for tier in tiers
                     for material in (tier.get("materials") or [])]
        if not materials:
            empty_recipe.append(name)
            continue
        for material in materials:
            if not str(material.get("name") or "").strip():
                empty_material.append(name)
            if not material.get("quantity"):
                zero_quantity.append(name)
        if not any(material.get("modifiers") for material in materials):
            no_stats.append(name)

    # -- unlock details
    no_faction = []
    no_rank = []
    no_places = []
    for set_name, entry in sets.items():
        if not isinstance(entry, dict):
            entry = {}
        members = entry.get("blueprints") or []
        if not members:
            continue
        if not str(entry.get("faction") or "").strip():
            no_faction.append(set_name)
        if not str(entry.get("rank") or "").strip():
            no_rank.append(set_name)
        if not (entry.get("places") or entry.get("location")
                or entry.get("system")):
            no_places.append(set_name)

    multi_tier = sum(1 for entry in crafting.values()
                     if len(entry.get("tiers") or []) > 1)
    multi_option = sum(1 for entry in crafting.values()
                       for tier in entry.get("tiers") or []
                       for material in tier.get("materials") or []
                       if len(material.get("options") or []) > 1)

    print(f"{len(crafting):,} recipes, {len(sets):,} mission pools, "
          f"{len(claims):,} distinct blueprint names\n")
    print(f"Recipes with several tiers   {multi_tier}")
    print(f"Slots offering a material choice {multi_option}")
    print(f"Recipes with no stat modifiers   {len(no_stats)}")

    def _show(title: str, items: list, limit: int = 8) -> None:
        print(f"\n{title}: {len(items)}")
        for item in items[:limit]:
            print(f"  {item}")

    print("\nStructural gaps")
    _show("Mission pools claiming a blueprint with no recipe",
          [f"{set_name} -> {key}" for set_name, key in missing_recipe])
    _show("Recipes with no materials", empty_recipe)
    _show("Materials with no name", sorted(set(empty_material)))
    _show("Materials with zero quantity", sorted(set(zero_quantity)))
    _show("Recipes no mission pool claims", unsourced)

    print("\nUnlock details (missing but not fatal)")
    _show("Pools without a faction", no_faction)
    _show("Pools without a rank", no_rank)
    _show("Pools without a location", no_places)

    problems = (missing_recipe or empty_recipe or empty_material
                or zero_quantity or unsourced)
    print("\nRESULT:", "gaps found" if problems else "every blueprint has "
          "a recipe, materials and a source")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
