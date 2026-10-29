"""Read-only, best-effort Star Citizen ``Game.log`` tailer.

The overlay never touches the game process. This module only reads a text log
from disk and reports two things the workflow engine needs:

* whether the player entered or left a pilot/command seat;
* location changes reported in the log.

Exact log lines vary by game patch and by whether the player runs the LIVE or
PTU build. The default patterns below are intentionally broad; callers can pass
their own compiled patterns. A missing log is not an error: the monitor stays
alive and keeps looking for it, which makes the workflow engine safe to use
offline or before the game has started.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .stations import clean_terminal_name

PilotCallback = Callable[["GameLogEvent"], None]

DEFAULT_PILOT_PATTERNS = (
    re.compile(
        r"(?i)\b(?:enter(?:ed|ing)?|exit(?:ed|ing)?|leav(?:e|ing)|left|"
        r"sit(?:ting)?|sat|stand(?:ing)?|stood|occupied|vacated)\b"
        r".{0,45}\b(?:pilot|command(?:er)?|captain)(?:'s)?\s+seat\b"
    ),
    re.compile(
        r"(?i)\b(?:pilot|command(?:er)?|captain)(?:'s)?\s+seat\b"
        r".{0,45}\b(?:enter(?:ed|ing)?|exit(?:ed|ing)?|leave|left|"
        r"occupied|vacated|stood)\b"
    ),
)

DEFAULT_LOCATION_PATTERNS = (
    re.compile(
        r"(?i)\b(?:current\s+)?location(?:\s+(?:changed|change|updated|update|"
        r"set|is))?\s*[:=]\s*[\"']?([^\"'\r\n,;]{2,100})"
    ),
    re.compile(
        r"(?i)\b(?:arriv(?:ed|ing)|enter(?:ed|ing)?)\s+(?:at|in|to)\s+"
        r"[\"']?([A-Za-z0-9][^\"'\r\n,;]{2,80})"
    ),
    re.compile(
        r"(?i)added notification\s+[\"'](?:entering|entered|"
        r"arriving at|arrived at)\s+([^\"']{2,80})[\"']"
    ),
)

_PILOT_ENTER = re.compile(
    r"(?i)\b(?:enter(?:ed|ing)?|sit(?:ting)?|sat|seated|occupied)\b")
_PILOT_EXIT = re.compile(
    r"(?i)\b(?:exit(?:ed|ing)?|leav(?:e|ing)|left|stand(?:ing)?|stood|"
    r"vacated|unseat(?:ed|ing)?)\b")
_TRAILING_NOISE = re.compile(
    r"(?i)\s+(?:added|notification|notice|log|entry|event)\b.*$")


@dataclass(frozen=True)
class GameLogEvent:
    """One parsed, read-only observation from ``Game.log``."""

    kind: str                       # "pilot_seat" or "location"
    value: bool | str
    raw: str
    timestamp: float

    @property
    def seated(self) -> bool | None:
        return self.value if self.kind == "pilot_seat" else None

    @property
    def location(self) -> str:
        return str(self.value) if self.kind == "location" else ""


def _windows_install_dirs() -> Iterable[Path]:
    """Likely Star Citizen install folders on Windows.

    The RSI launcher lets players install anywhere, so this covers the default
    Program Files location plus the usual custom spots on every fixed drive.
    Only cheap ``stat`` calls are made, which is fine for the 8 s discovery
    retry when the game is not installed; the drive list itself comes from
    :func:`_windows_fixed_drives`, which never touches the media.
    """
    seen: set[str] = set()

    def add(path: Path) -> Iterable[Path]:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            return (path,)
        return ()

    roots: list[Path] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                "SystemDrive"):
        value = os.environ.get(var)
        if value:
            roots.append(Path(value) if var != "SystemDrive" else Path(value + "\\"))
    roots.extend([Path(r"C:\Program Files"), Path(r"C:\Games")])

    for root in roots:
        for path in add(root / "Roberts Space Industries" / "StarCitizen"):
            yield path
        for path in add(root / "StarCitizen"):
            yield path
    for drive in _windows_fixed_drives():
        for path in add(Path(drive) / "Roberts Space Industries" / "StarCitizen"):
            yield path
        for path in add(Path(drive) / "StarCitizen"):
            yield path
        for path in add(Path(drive) / "Games" / "StarCitizen"):
            yield path


_FIXED_DRIVES: tuple[str, ...] | None = None


def _windows_fixed_drives() -> tuple[str, ...]:
    """Root paths of the local fixed disks, e.g. ``('C:\\', 'D:\\')``.

    ``Path("Z:\\").exists()`` blocks for seconds (or minutes) when a letter is
    mapped to an unreachable network share or an empty card reader, and the log
    watcher calls discovery every 8 s, so the drive list is taken from
    ``GetLogicalDrives`` + ``GetDriveTypeW`` instead: those never touch the
    media.  The result is cached for the life of the process.
    """
    global _FIXED_DRIVES
    if _FIXED_DRIVES is not None:
        return _FIXED_DRIVES
    drives: list[str] = []
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetDriveTypeW.restype = wintypes.UINT
        mask = kernel32.GetLogicalDrives()
        for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            if not mask & (1 << index):
                continue
            root = f"{letter}:\\"
            # DRIVE_FIXED == 3.  Removable media is skipped on purpose: an
            # empty reader reports DRIVE_REMOVABLE and stat() on it stalls.
            if kernel32.GetDriveTypeW(root) == 3:
                drives.append(root)
    except Exception:
        drives = []
    _FIXED_DRIVES = tuple(drives)
    return _FIXED_DRIVES


def _candidate_logs() -> Iterable[Path]:
    override = os.environ.get("SC_GAME_LOG")
    if override:
        yield Path(override).expanduser()

    if sys.platform == "win32":
        for install in _windows_install_dirs():
            for branch in ("LIVE", "PTU", "EPTU", "HOTFIX"):
                yield install / branch / "Game.log"
    else:
        home = Path.home()
        for root in (
            home / ".steam" / "steam" / "steamapps" / "common",
            home / ".local" / "share" / "Steam" / "steamapps" / "common",
            home / "Games",
        ):
            for branch in ("LIVE", "PTU", "EPTU"):
                yield root / "StarCitizen" / branch / "Game.log"

    # CrossOver / Whisky installations on macOS.
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        for bottle_root in (base / "CrossOver" / "Bottles",
                            base / "Whisky" / "Bottles"):
            try:
                for path in bottle_root.glob(
                        "*/drive_c/Program Files/Roberts Space Industries/"
                        "StarCitizen/*/Game.log"):
                    yield path
            except OSError:
                continue


def discover_game_log() -> Path | None:
    """Return the first known login log path, or ``None`` if none exists."""
    for path in _candidate_logs():
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _clean_location(value: str) -> str:
    text = (value or "").strip().strip("\"'")
    text = _TRAILING_NOISE.sub("", text).strip()
    text = re.sub(r"^[\s\-–—:]+|[\s\-–—:]+$", "", text)
    if not text:
        return ""
    # Known station aliases are useful, but do not let a generic log phrase
    # turn into a misleading location.
    return clean_terminal_name(text) or text


def _pilot_seated(line: str) -> bool | None:
    text = line.lower()
    exit_match = _PILOT_EXIT.search(text)
    enter_match = _PILOT_ENTER.search(text)
    if exit_match is None and enter_match is None:
        return None
    if exit_match is None:
        return True
    if enter_match is None:
        return False
    return enter_match.start() >= exit_match.start()


class GameLogParser:
    """Small, deterministic parser that can be reused and tested offline."""

    def __init__(self, pilot_patterns: Iterable[re.Pattern] | None = None,
                 location_patterns: Iterable[re.Pattern] | None = None):
        self.pilot_patterns = tuple(pilot_patterns or DEFAULT_PILOT_PATTERNS)
        self.location_patterns = tuple(
            location_patterns or DEFAULT_LOCATION_PATTERNS)

    def parse_line(self, line: str, now: float | None = None) -> list[GameLogEvent]:
        stamp = time.time() if now is None else float(now)
        raw = line.rstrip("\r\n")
        if not raw:
            return []
        events: list[GameLogEvent] = []
        for pattern in self.pilot_patterns:
            match = pattern.search(raw)
            if match is None:
                continue
            seated = _pilot_seated(match.group(0))
            if seated is None:
                seated = _pilot_seated(raw)
            if seated is not None:
                events.append(GameLogEvent("pilot_seat", seated, raw, stamp))
            break
        for pattern in self.location_patterns:
            match = pattern.search(raw)
            if match is None:
                continue
            location = _clean_location(match.group(1))
            if location:
                events.append(GameLogEvent("location", location, raw, stamp))
            break
        return events


class GameLogMonitor:
    """Daemon thread that tails one log file and forwards parsed events."""

    def __init__(self, path: str | Path | None = None,
                 callback: PilotCallback | None = None,
                 poll_interval: float = 0.5,
                 discovery_interval: float = 8.0,
                 from_start: bool = False,
                 parser: GameLogParser | None = None):
        self._path = Path(path).expanduser() if path else None
        self._callback = callback
        self._poll_interval = max(0.1, float(poll_interval))
        self._discovery_interval = max(1.0, float(discovery_interval))
        self._from_start = bool(from_start)
        self._parser = parser or GameLogParser()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop.is_set())

    def start(self) -> "GameLogMonitor":
        if self.running:
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="sc-game-log", daemon=True)
        self._thread.start()
        return self

    def stop(self, wait: bool = True, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if (wait and thread is not None
                and thread is not threading.current_thread()):
            thread.join(timeout)
        if thread is None or not thread.is_alive():
            self._thread = None

    def _resolve_path(self) -> Path | None:
        if self._path is not None:
            return self._path
        self._path = discover_game_log()
        return self._path

    @staticmethod
    def _file_identity(handle) -> tuple[int, int] | None:
        try:
            stat = os.fstat(handle.fileno())
            return (int(stat.st_dev), int(stat.st_ino))
        except (OSError, ValueError, AttributeError):
            return None

    def _open(self, path: Path):
        handle = open(path, "r", encoding="utf-8", errors="replace")
        if not self._from_start:
            try:
                handle.seek(0, os.SEEK_END)
            except OSError:
                pass
        return handle

    def _run(self) -> None:
        handle = None
        current: Path | None = None
        handle_id: tuple[int, int] | None = None
        # Resolve immediately on the first pass.  Some platforms start
        # monotonic time near zero, so 0.0 would otherwise delay discovery.
        last_discovery = time.monotonic() - self._discovery_interval
        try:
            while not self._stop.is_set():
                if current is None:
                    now = time.monotonic()
                    if now - last_discovery >= self._discovery_interval:
                        last_discovery = now
                        current = self._resolve_path()
                    if current is None:
                        self._stop.wait(self._discovery_interval)
                        continue
                    try:
                        handle = self._open(current)
                        handle_id = self._file_identity(handle)
                    except OSError:
                        current = None
                        handle_id = None
                        if self._path is not None and not self._from_start:
                            # An explicit but temporarily missing path should
                            # be retried instead of forgotten.
                            pass
                        self._stop.wait(self._discovery_interval)
                        continue

                try:
                    line = handle.readline()
                except (OSError, ValueError):
                    line = ""
                if line:
                    for event in self._parser.parse_line(line):
                        if self._stop.is_set():
                            break
                        if self._callback is not None:
                            try:
                                self._callback(event)
                            except Exception:
                                # A consumer must never kill the tailer.
                                pass
                    continue

                # At EOF, detect truncation/rotation before sleeping. A
                # replaced log keeps the old open handle alive on POSIX, so
                # compare the path identity as well as the size.
                try:
                    stat = current.stat()
                    path_id = (int(stat.st_dev), int(stat.st_ino))
                    rotated = (handle_id is not None
                               and path_id != handle_id
                               and stat.st_size > 0)
                    if rotated or stat.st_size < handle.tell():
                        handle.close()
                        handle = None
                        handle_id = None
                        current = None
                        continue
                except OSError:
                    handle.close()
                    handle = None
                    handle_id = None
                    current = None
                    continue
                self._stop.wait(self._poll_interval)
        finally:
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            self._thread = None
