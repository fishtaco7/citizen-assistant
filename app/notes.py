"""Personal notes, stored as plain text beside the other user data.

Nothing clever on purpose: one UTF-8 file the player can open, back up or edit
in any editor.  Writes go through a temporary file and a rename, so a crash
mid-save cannot leave a half-written note behind.
"""
from __future__ import annotations

from pathlib import Path

from .config import config_dir

FILE_NAME = "notes.txt"


class NotesStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else config_dir() / FILE_NAME

    def load(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def save(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)
