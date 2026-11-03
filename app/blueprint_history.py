"""Recover blueprint unlocks from Game.log backups.

Star Citizen starts a fresh ``Game.log`` each launch and moves the previous one
into a ``logbackups`` folder beside the LIVE build, so a machine that has played
for months still has its unlock notifications on disk. The live watcher only
sees new lines; this module fills the registry for blueprints earned before the
overlay was installed.

Everything here is read-only. Files are streamed line by line so a large log
never has to be held in memory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from .blueprint_log import KIND_BLUEPRINT, BlueprintLogEvent, BlueprintLogParser
from .game_log import discover_game_log

# Guards against a pathological directory: the game rotates logs, so a normal
# install has tens of files, not thousands.
MAX_FILES = 400


def history_logs(game_log: Path | str | None = None) -> list[Path]:
    """Backups plus the current log, oldest first, or an empty list."""
    current = (Path(game_log).expanduser() if game_log is not None
               else discover_game_log())
    if current is None:
        return []

    logs: list[Path] = []
    backups = current.parent / "logbackups"
    try:
        if backups.is_dir():
            logs.extend(sorted(backups.glob("*.log")))
    except OSError:
        pass

    try:
        if current.is_file():
            logs.append(current)
    except OSError:
        pass
    return logs[-MAX_FILES:]


def scan(logs: Iterable[Path],
         progress: Callable[[str], None] | None = None) -> list[BlueprintLogEvent]:
    """Every blueprint event in ``logs``, in the order the files were written.

    Duplicates are deliberately not removed: the registry counts ``seen_count``
    and keeps the earliest ``first_seen``, which is more useful than a
    ready-made set.
    """
    paths = list(logs)
    parser = BlueprintLogParser()
    events: list[BlueprintLogEvent] = []

    for index, path in enumerate(paths, start=1):
        if progress is not None:
            try:
                progress(f"Reading {path.name} ({index}/{len(paths)})")
            except Exception:
                pass
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    for event in parser.parse_line(line):
                        if event.kind == KIND_BLUEPRINT:
                            events.append(event)
        except OSError:
            continue
    return events
