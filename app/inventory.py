"""Local, manual inventory notes.

This is deliberately a notebook, not a live game-inventory reader. It records
where the player left loot or supplies so those stashes can be found again.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import config_dir


@dataclass
class InventoryNote:
    id: str
    location: str
    item: str
    quantity: str = ""
    note: str = ""
    updated: float = 0.0


class InventoryStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else config_dir() / "inventory.json"
        self.notes: list[InventoryNote] = []
        self.error = ""
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        self.notes = []
        self.error = ""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            self.error = f"Could not read inventory notes: {exc}"
            return

        rows = raw.get("notes") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            self.error = "Inventory notes file has an unexpected format"
            return

        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            location = str(row.get("location") or "").strip()
            item = str(row.get("item") or "").strip()
            if not location and not item:
                continue
            note_id = str(row.get("id") or uuid.uuid4().hex)
            if note_id in seen:
                note_id = uuid.uuid4().hex
            seen.add(note_id)
            try:
                updated = float(row.get("updated") or 0.0)
            except (TypeError, ValueError):
                updated = 0.0
            self.notes.append(InventoryNote(
                id=note_id,
                location=location,
                item=item,
                quantity=str(row.get("quantity") or "").strip(),
                note=str(row.get("note") or "").strip(),
                updated=updated,
            ))
        self.notes.sort(key=lambda note: -note.updated)

    def save(self) -> bool:
        payload = {
            "version": 1,
            "notes": [
                {
                    "id": note.id,
                    "location": note.location,
                    "item": note.item,
                    "quantity": note.quantity,
                    "note": note.note,
                    "updated": note.updated,
                }
                for note in self.notes
            ],
        }
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            self.error = f"Could not save inventory notes: {exc}"
            return False
        self.error = ""
        return True

    # ------------------------------------------------------------------
    def all(self) -> list[InventoryNote]:
        return list(self.notes)

    def get(self, note_id: str) -> InventoryNote | None:
        return next((note for note in self.notes if note.id == note_id), None)

    def add(self, location: str, item: str, quantity: str = "",
            note: str = "") -> InventoryNote:
        entry = InventoryNote(
            id=uuid.uuid4().hex,
            location=location.strip(),
            item=item.strip(),
            quantity=quantity.strip(),
            note=note.strip(),
            updated=time.time(),
        )
        self.notes.insert(0, entry)
        self.save()
        return entry

    def update(self, note_id: str, location: str, item: str,
               quantity: str = "", note: str = "") -> InventoryNote | None:
        entry = self.get(note_id)
        if entry is None:
            return None
        entry.location = location.strip()
        entry.item = item.strip()
        entry.quantity = quantity.strip()
        entry.note = note.strip()
        entry.updated = time.time()
        self.save()
        return entry

    def delete(self, note_id: str) -> bool:
        before = len(self.notes)
        self.notes = [note for note in self.notes if note.id != note_id]
        if len(self.notes) == before:
            return False
        self.save()
        return True
