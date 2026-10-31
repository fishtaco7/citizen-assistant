"""Build identity for this copy of the overlay.

A downloaded ZIP has no ``.git`` directory, so the build cannot be read from
git and has to be committed instead.  Bump both values in the same commit as a
fix: Settings shows the string, and ``python -m app --version`` prints it.

That matters because Windows happily keeps an old shortcut alive: searching
for "Citizen Assistant" can launch a previous folder for months.  The stamp is
how you tell, at a glance, which copy is actually running.
"""
from __future__ import annotations

VERSION = "1.2.15"
BUILD_DATE = "2026-10-30"


def label() -> str:
    """Short build string, e.g. ``v1.1.0 · 2026-10-30``."""
    return f"v{VERSION} · {BUILD_DATE}"
