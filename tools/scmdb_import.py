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

from app.config import config_dir  # noqa: E402


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
                ranks: dict[str, tuple[str, float]]) -> tuple[str, str]:
    """(faction, display text) for the contract's reputation gate."""
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
    return faction, text


def build(data: dict) -> dict[str, dict]:
    """{set name: {source, requires, blueprints[]}} from a merged SCMDB dump."""
    pools = data.get("blueprintPools") or {}
    contracts = data.get("contracts") or []
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
        faction, requires = _reputation(contract, ranks)

        # Keep rank variants apart: the same title at a different standing
        # awards a different pool, so merging them would blur the progress.
        rank_hint = requires.replace(f"{faction} ", "", 1) if faction else requires
        key = f"{contractor}: {title}" if contractor else title
        if rank_hint:
            key = f"{key} · {rank_hint}"

        bits = [str(contract.get("missionType") or "").strip(), contractor,
                str(contract.get("debugName") or "").strip()]
        source = "  ·  ".join(bit for bit in bits if bit)

        entry = sets.setdefault(key, {"source": source,
                                      "requires": requires,
                                      "blueprints": []})
        if requires and not entry.get("requires"):
            entry["requires"] = requires
        for name in names:
            if name not in entry["blueprints"]:
                entry["blueprints"].append(name)

    return sets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert an SCMDB merged dump into blueprint sets")
    parser.add_argument("merged", type=Path, help="merged-<version>.json")
    parser.add_argument("--out", type=Path, default=None,
                        help="destination (default: the app config folder)")
    args = parser.parse_args(argv)

    with open(args.merged, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    sets = build(data)
    if not sets:
        print("No contracts with blueprint rewards found", file=sys.stderr)
        return 1

    out = args.out or (config_dir() / "blueprint_sets.json")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
