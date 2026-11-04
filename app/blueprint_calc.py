"""Crafting maths, matching the game's own quality curves.

A slot's modifier is a straight line between two quality points:

    factor = atStart + (atEnd - atStart) * clamp((q - start) / (end - start))

Per ingredient, the factors for one property are multiplied together. Across
ingredients the default is **additive** (sum of the deviations), while damage
mitigation uses **stacked** (product). Verified against sccraftlab's bundle,
whose `Jp` function does exactly this.
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


def changes(materials: list[dict], qualities: list[float]) -> dict[str, dict]:
    """{property key: {name, factor, percent}} for the whole recipe."""
    additive: dict[str, float] = {}
    stacked: dict[str, float] = {}
    names: dict[str, str] = {}

    for index, material in enumerate(materials):
        if index >= len(qualities):
            break
        quality = float(qualities[index])
        per_slot: dict[str, float] = {}
        for modifier in material.get("modifiers") or []:
            key = str(modifier.get("property") or "").strip()
            if not key:
                continue
            names.setdefault(key, str(modifier.get("name") or key))
            per_slot[key] = per_slot.get(key, 1.0) * factor(modifier, quality)

        for key, value in per_slot.items():
            additive[key] = additive.get(key, 0.0) + (value - 1.0)
            stacked[key] = stacked.get(key, 1.0) * value

    result: dict[str, dict] = {}
    for key, deviation in additive.items():
        if "mitigation" in key:
            value = stacked.get(key, 1.0)
        else:
            value = 1.0 + deviation
        result[key] = {
            "name": names.get(key, key),
            "factor": value,
            "percent": (value - 1.0) * 100.0,
        }
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
