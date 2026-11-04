"""Crafting maths, matching the game's own quality curves.

A slot's modifier is a straight line between two quality points:

    factor = atStart + (atEnd - atStart) * clamp((q - start) / (end - start))

One property may be described by several segments (0-500, then 501-1000); only
the segment covering the quality applies, because they are pieces of a single
curve rather than independent modifiers. Factors for one property multiply
within an ingredient. Across ingredients the default is **additive** (sum of the
deviations), damage mitigation is **stacked** (product), and properties flagged
``additive`` in the data (power pips) are summed as plain numbers. Verified
against sccraftlab's bundle, whose `Jp` function does exactly this.
"""
from __future__ import annotations


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def factor(modifier: dict, quality: float) -> float:
    """Factor for one modifier at a material quality of 0-1000."""
    start = float(modifier.get("start") or 0.0)
    end = float(modifier.get("end") or 1000.0)
    at_start = float(modifier.get("atStart", 1.0))
    at_end = float(modifier.get("atEnd", 1.0))
    if end == start:
        return at_start
    position = _clamp01((float(quality) - start) / (end - start))
    return at_start + (at_end - at_start) * position


def _segment(modifiers: list[dict], quality: float) -> dict | None:
    """The segment that covers this quality, else the nearest one.

    A property can be described by several segments (0-500 rising, 501-1000
    rising again). Only one applies at a given quality; treating them as
    separate modifiers multiplies curves that were meant to be alternatives.
    """
    if not modifiers:
        return None
    for modifier in modifiers:
        start = float(modifier.get("start") or 0.0)
        end = float(modifier.get("end") or 1000.0)
        if start <= quality <= end:
            return modifier
    return min(modifiers,
               key=lambda modifier: abs(
                   quality - (float(modifier.get("start") or 0.0)
                              + float(modifier.get("end") or 1000.0)) / 2.0))


def changes(materials: list[dict], qualities: list[float]) -> dict[str, dict]:
    """{property key: {name, percent, delta}} for the whole recipe.

    Multiplicative properties report a factor and a percentage; additive ones
    (power pips and similar) report an absolute delta instead.
    """
    deviation: dict[str, float] = {}
    stacked: dict[str, float] = {}
    added: dict[str, float] = {}
    additive_keys: set[str] = set()
    names: dict[str, str] = {}

    for index, material in enumerate(materials):
        if index >= len(qualities):
            break
        quality = float(qualities[index])

        grouped: dict[str, list[dict]] = {}
        for modifier in material.get("modifiers") or []:
            key = str(modifier.get("property") or "").strip()
            if key:
                grouped.setdefault(key, []).append(modifier)

        for key, group in grouped.items():
            names.setdefault(key, str(group[0].get("name") or key))
            segment = _segment(group, quality)
            if segment is None:
                continue
            value = factor(segment, quality)
            if segment.get("additive"):
                additive_keys.add(key)
                added[key] = added.get(key, 0.0) + value
            else:
                deviation[key] = deviation.get(key, 0.0) + (value - 1.0)
                stacked[key] = stacked.get(key, 1.0) * value

    result: dict[str, dict] = {}
    for key in set(deviation) | set(added):
        if key in additive_keys:
            result[key] = {"name": names.get(key, key), "percent": None,
                           "delta": added.get(key, 0.0)}
        else:
            value = (stacked.get(key, 1.0) if "mitigation" in key
                     else 1.0 + deviation.get(key, 0.0))
            result[key] = {"name": names.get(key, key), "percent":
                           (value - 1.0) * 100.0, "delta": None}
    return result


def average_quality(qualities: list[float],
                    materials: list[dict]) -> float:
    """Quantity-weighted mean quality; the game's output quality estimate."""
    total = 0.0
    weighted = 0.0
    for index, material in enumerate(materials):
        if index >= len(qualities):
            break
        quantity = float(material.get("quantity") or 0.0)
        if quantity <= 0:
            continue
        total += quantity
        weighted += quantity * float(qualities[index])
    return weighted / total if total else 0.0
