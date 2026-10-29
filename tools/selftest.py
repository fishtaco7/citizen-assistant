"""End-to-end check of the OCR + signature lookup without needing the game.

    python -m tools.selftest
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.demo import signature_crop          # noqa: E402
from app.mining import MiningData            # noqa: E402
from app.ocr import SignatureOCR             # noqa: E402


CASES = [
    (7200, "Bexalite", 2),
    (3585, "Gold", 1),
    (10710, "Borase", 3),
    (4225, "Corundum", 1),
    (4285, "Aluminum", 1),
    (6340, "Quantainium", 2),
    (9000, None, None),       # ground only: FPS x3 (not a 2,000 multiple)
]


def main() -> int:
    data = MiningData()
    ocr = SignatureOCR()
    failures = 0

    print(f"Signature data: {data.version}  ({len(data.ores)} ores, "
          f"ship 1-{data.max_cluster}, ground 1-{data.ground_max_cluster}, "
          f"source {data.source})")
    print("-" * 78)
    print(f"{'expected':>9}  {'read':>9}  {'conf':>5}  {'top match':<22} result")
    print("-" * 78)

    for expected, ore, count in CASES:
        crop = signature_crop(expected)
        res = ocr.read(crop)
        matches = data.lookup(res.value) if res.ok else []
        top = matches[0] if matches else None

        ok = res.value == expected
        if ore is not None:
            ok = ok and top is not None and top.name == ore and top.count == count
        else:
            ok = ok and top is not None and top.is_ground
        failures += 0 if ok else 1

        got = f"{top.label} x{top.count}" if top else "-"
        print(f"{expected:>9,}  {str(res.value):>9}  "
              f"{res.confidence:>5.2f}  {got:<22} {'PASS' if ok else 'FAIL'}")

    # Lookup-only sanity checks (no OCR involved). Agricium covers the
    # 1x/2x/4x/10x examples from the SCMINER signature scanner.
    print("-" * 78)
    lookup_cases = (
        (3885, "Agricium", 1),
        (7770, "Agricium", 2),
        (15540, "Agricium", 4),
        (38850, "Agricium", 10),
        (3600 * 4, "Bexalite", 4),
        (3170 * 5, "Quantainium", 5),
        # Salvage panels / wreck scrap: multiples of 2,000 are Debris.
        (2000, "Debris", 1),
        (4000, "Debris", 2),
        (6000, "Debris", 3),
        (8000, "Debris", 4),
        (10000, "Debris", 5),
    )
    for value, ore, count in lookup_cases:
        m = data.lookup(value)
        ok = bool(m) and m[0].name == ore and m[0].count == count
        failures += 0 if ok else 1
        print(f"{value:>9,} -> {m[0].name if m else '-'} "
              f"x{m[0].count if m else 0}  {'PASS' if ok else 'FAIL'}")

    print("-" * 78)
    print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
