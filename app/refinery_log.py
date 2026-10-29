"""Read-only refinery work-order parser for Star Citizen's ``Game.log``.

The game does not publish a stable refinery API.  This module therefore uses a
small number of deliberately broad trigger patterns and extracts optional
``key=value`` fields from the matching line.  It never reads game memory,
injects input, or writes to the game directory.

The default log location requested by the overlay is::

    ~/starcitizen/LIVE/Game.log

A missing log is normal (the game may not have been launched yet).  The watcher
keeps looking for the file without raising.
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

from .stations import clean_terminal_name, station_names, system_for_station

RefineryLogCallback = Callable[["RefineryLogEvent"], None]

DEFAULT_LOG_PATH = Path.home() / "starcitizen" / "LIVE" / "Game.log"

# These are intentionally generous.  A real Game.log line can contain class
# names such as ``CRefineryWorkOrder`` and method names such as
# ``SubmitWorkOrder`` rather than readable prose.
DEFAULT_START_PATTERNS = (
    re.compile(
        r"(?i)\b(?:refiner(?:y|ies)|refining|refine)\b"
        r".{0,180}\b(?:work[\s_-]*order|workorder|job|order)\b"
        r".{0,180}\b(?:start(?:ed|ing)?|submit(?:ted|ting)?|"
        r"accept(?:ed|ing)?|creat(?:e|ed|ing)|queue(?:d|ing)?|"
        r"begin(?:ning)?|began|confirm(?:ed|ing)?|transaction|"
        r"request(?:ed|ing)?|purchas(?:e|ed|ing))\b"
    ),
    re.compile(
        r"(?i)\b(?:work[\s_-]*order|workorder)\b"
        r".{0,180}\b(?:start(?:ed|ing)?|submit(?:ted|ting)?|"
        r"accept(?:ed|ing)?|creat(?:e|ed|ing)|queue(?:d|ing)?|"
        r"begin(?:ning)?|began|confirm(?:ed|ing)?|transaction|"
        r"request(?:ed|ing)?|purchas(?:e|ed|ing))\b"
    ),
    re.compile(
        r"(?i)\b(?:start(?:ed|ing)?|submit(?:ted|ting)?|"
        r"accept(?:ed|ing)?|creat(?:e|ed|ing)|queue(?:d|ing)?|"
        r"begin(?:ning)?|began|confirm(?:ed|ing)?|transaction|"
        r"request(?:ed|ing)?|purchas(?:e|ed|ing))\b"
        r".{0,180}\b(?:refiner(?:y|ies)|refining|refine)\b"
        r".{0,180}\b(?:work[\s_-]*order|workorder|job|order)\b"
    ),
)

_START_ACTIONS = (
    "start", "submit", "accept", "create", "queue", "begin", "began",
    "confirm", "transaction", "request", "purchase",
)

_KNOWN_REFINERY_METHODS = (
    "Cormack", "Dinyx", "Ferron", "Pyrometric", "Electrostar",
    "Photometric", "Thermal", "Gaslight", "Catalytic",
)

_KNOWN_SYSTEMS = (
    "Stanton", "Pyro", "Nyx", "Terra", "Magnus", "Odin", "Ellis",
    "Kilian", "Sol", "Castra",
)

# Field names seen in diagnostic logs and useful optional data.
_DURATION_KEYS = (
    "duration", "time_remaining", "remaining_time", "time_left",
    "remaining", "refine_time", "refining_time", "processing_time", "eta",
)
_STATION_KEYS = (
    "station", "station_name", "location", "location_name", "terminal",
    "refinery", "refinery_station", "refinery_terminal",
    "work_order_location",
)
_SYSTEM_KEYS = (
    "system", "star_system", "starsystem", "solar_system", "solarsystem",
)
_METHOD_KEYS = (
    "method", "processing_method", "refining_method", "refinery_method",
    "refining_process", "process",
)
_JOB_ID_KEYS = (
    "job_id", "work_order_id", "workorder_id", "order_id", "refinery_job_id",
)
_COMMODITY_KEYS = ("commodity", "commodity_name", "material", "ore",
                   "resource")
_QUANTITY_KEYS = ("quantity_scu", "quantity", "scu", "amount", "units")

_DURATION_TOKEN = re.compile(
    r"(?i)(\d+(?:\.\d+)?)\s*"
    r"(d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes|"
    r"s|sec|secs|second|seconds)\b"
)
_QUOTED_VALUE = re.compile(r"^[\"'](.*?)[\"']$")
_TRAILING_PAREN = re.compile(r"\s+\([^)]*\)\s*$")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")

# Longest first so "HUR-L1 Green Glade Station" wins over a shorter name.
_KNOWN_STATIONS = tuple(sorted(station_names(), key=len, reverse=True))


@dataclass(frozen=True)
class RefineryLogEvent:
    """One refinery start observation parsed from a log line."""

    kind: str
    system: str
    station: str
    processing_method: str
    duration_seconds: int
    started_at: float
    timestamp: float
    job_id: str = ""
    commodity: str = ""
    quantity_scu: int = 0
    raw: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "system": self.system,
            "station": self.station,
            "processing_method": self.processing_method,
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at,
            "timestamp": self.timestamp,
            "job_id": self.job_id,
            "commodity": self.commodity,
            "quantity_scu": self.quantity_scu,
            "raw": self.raw,
        }


def _clean_value(value: str) -> str:
    text = (value or "").strip()
    match = _QUOTED_VALUE.match(text)
    if match:
        text = match.group(1).strip()
    text = text.strip().strip("\"'").strip()
    text = re.sub(r"[\s,;]+$", "", text)
    return text


def _field(line: str, *keys: str) -> str:
    """Return the first matching ``key=value`` / ``key: value`` field."""
    for key in keys:
        # Accept both ``time_remaining`` and ``Time Remaining`` spellings.
        key_pattern = re.escape(key).replace("_", r"[\s_-]*")
        pattern = re.compile(
            rf"(?i)(?<![\w])[\"']?{key_pattern}[\"']?\s*[:=]\s*[\"']?"
            r"([^\"'\r\n,;\]\}]*?)"
            r"(?=\s+[A-Za-z_][\w]*"
            r"(?:[\s_-]+[A-Za-z_][\w]*){0,4}\s*[:=]"
            r"|[\"'\r\n,;\]\}]|$)"
        )
        match = pattern.search(line)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return value
    return ""


def _parse_clock(text: str) -> int | None:
    value = (text or "").strip()
    if ":" not in value:
        return None
    parts = value.split(":")
    if not 2 <= len(parts) <= 4 or not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if len(numbers) == 2:          # MM:SS
        minutes, seconds = numbers
        return minutes * 60 + seconds
    if len(numbers) == 3:          # HH:MM:SS
        hours, minutes, seconds = numbers
        return hours * 3600 + minutes * 60 + seconds
    days, hours, minutes, seconds = numbers   # D:HH:MM:SS
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_duration(text: str) -> int:
    """Parse ``1d 2h``, ``01:30:00``, or a raw second count.

    A bare integer is treated as seconds because that is the least surprising
    representation for a diagnostic log field.  Callers that know a game patch
    uses minutes can pass an explicit suffix in the log.
    """
    value = _clean_value(text)
    if not value:
        return 0
    clock = _parse_clock(value)
    if clock is not None:
        return max(0, int(clock))

    tokens = list(_DURATION_TOKEN.finditer(value))
    if tokens:
        total = 0.0
        for match in tokens:
            amount = float(match.group(1))
            unit = match.group(2).casefold()
            if unit.startswith("d"):
                total += amount * 86400
            elif unit.startswith("h"):
                total += amount * 3600
            elif unit.startswith("m"):
                total += amount * 60
            else:
                total += amount
        return max(0, int(round(total)))

    number = _NUMBER.search(value)
    if number is not None:
        return max(0, int(round(float(number.group(0)))))
    return 0


def _quantity(text: str) -> int:
    match = _NUMBER.search(text or "")
    if match is None:
        return 0
    try:
        return max(0, int(round(float(match.group(0)))))
    except (TypeError, ValueError):
        return 0


def _station_from_line(line: str) -> str:
    value = _field(line, *_STATION_KEYS)
    if value:
        value = _TRAILING_PAREN.sub("", value).strip()
        return clean_terminal_name(value) or value

    lower = line.casefold()
    for name in _KNOWN_STATIONS:
        if name.casefold() in lower:
            return name
    # Also recognise compact spellings such as ``Area18`` or ``CRU-L4``.
    compact = re.sub(r"[^a-z0-9]+", "", lower)
    for name in _KNOWN_STATIONS:
        key = re.sub(r"[^a-z0-9]+", "", name.casefold())
        if len(key) >= 5 and key in compact:
            return name
    return ""


def _system_from_line(line: str, station: str) -> str:
    value = _field(line, *_SYSTEM_KEYS)
    if value:
        return value
    inferred = system_for_station(station)
    if inferred:
        return inferred
    lower = line.casefold()
    for system in _KNOWN_SYSTEMS:
        if re.search(rf"\b{re.escape(system.casefold())}\b", lower):
            return system
    return ""


def _method_from_line(line: str) -> str:
    value = _field(line, *_METHOD_KEYS)
    if value:
        return value
    lower = line.casefold()
    for method in _KNOWN_REFINERY_METHODS:
        if re.search(rf"\b{re.escape(method.casefold())}\b", lower):
            return method
    return ""


class RefineryLogParser:
    """Deterministic, offline parser for refinery start lines."""

    def __init__(self, start_patterns: Iterable[re.Pattern] | None = None):
        self.start_patterns = tuple(start_patterns or DEFAULT_START_PATTERNS)

    # ------------------------------------------------------------------
    @staticmethod
    def _triggered(raw: str, patterns: tuple[re.Pattern, ...]) -> bool:
        if any(pattern.search(raw) for pattern in patterns):
            return True
        # Compact fallback for camel-case diagnostics such as
        # ``CRefineryWorkOrder::SubmitWorkOrder``.
        compact = re.sub(r"[^a-z0-9]+", "", raw.casefold())
        refinery = any(word in compact for word in
                       ("refinery", "refining", "refine"))
        order = ("workorder" in compact or "order" in compact
                 or "terminal" in compact or "transaction" in compact
                 or "job" in compact)
        action = any(word in compact for word in _START_ACTIONS)
        return bool(refinery and order and action)

    def parse_line(self, line: str,
                   now: float | None = None) -> list[RefineryLogEvent]:
        raw = line.rstrip("\r\n")
        if not raw or not self._triggered(raw, self.start_patterns):
            return []

        duration_text = _field(raw, *_DURATION_KEYS)
        duration = parse_duration(duration_text)
        if duration <= 0:
            # A trigger without a duration cannot create a useful countdown.
            # The host can still add the order manually after inspecting it.
            return []

        station = _station_from_line(raw)
        system = _system_from_line(raw, station)
        method = _method_from_line(raw)
        stamp = time.time() if now is None else float(now)
        return [RefineryLogEvent(
            kind="job_started",
            system=system or "Unknown",
            station=station or "Unknown",
            processing_method=method or "Unknown",
            duration_seconds=max(1, int(duration)),
            started_at=stamp,
            timestamp=stamp,
            job_id=_field(raw, *_JOB_ID_KEYS),
            commodity=_field(raw, *_COMMODITY_KEYS),
            quantity_scu=_quantity(_field(raw, *_QUANTITY_KEYS)),
            raw=raw[:1000],
        )]


# ---------------------------------------------------------------------------
def _candidate_logs() -> Iterable[Path]:
    override = os.environ.get("SC_GAME_LOG")
    if override:
        yield Path(override).expanduser()

    home = Path.home()
    yield home / "starcitizen" / "LIVE" / "Game.log"
    yield home / "StarCitizen" / "LIVE" / "Game.log"
    for branch in ("PTU", "EPTU"):
        yield home / "starcitizen" / branch / "Game.log"
        yield home / "StarCitizen" / branch / "Game.log"

    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
        for bottle_root in (base / "CrossOver" / "Bottles",
                            base / "Whisky" / "Bottles"):
            try:
                for path in bottle_root.glob(
                        "*/drive_c/Program Files/Roberts Space Industries/"
                        "StarCitizen/*/Game.log"):
                    yield path
            except OSError:
                continue


def discover_refinery_log() -> Path | None:
    """Return the first existing known log, including the requested default."""
    for path in _candidate_logs():
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    # Reuse the overlay's wider Windows/Linux discovery as a final fallback.
    try:
        from .game_log import discover_game_log as _discover
        return _discover()
    except Exception:
        return None


class RefineryLogWatcher:
    """Daemon thread that tails one read-only log and forwards start events."""

    def __init__(self, path: str | Path | None = None,
                 callback: RefineryLogCallback | None = None,
                 poll_interval: float = 0.5,
                 discovery_interval: float = 8.0,
                 from_start: bool = False,
                 parser: RefineryLogParser | None = None):
        self._path = Path(path).expanduser() if path else None
        self._callback = callback
        self._poll_interval = max(0.1, float(poll_interval))
        self._discovery_interval = max(1.0, float(discovery_interval))
        self._from_start = bool(from_start)
        self._parser = parser or RefineryLogParser()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop.is_set())

    def start(self) -> "RefineryLogWatcher":
        if self.running:
            return self
        previous = self._thread
        if (previous is not None and previous.is_alive()
                and previous is not threading.current_thread()):
            previous.join(1.0)
        if self.running:
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="sc-refinery-log", daemon=True)
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
        self._path = discover_refinery_log()
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
        thread = threading.current_thread()
        handle = None
        current: Path | None = None
        handle_id: tuple[int, int] | None = None
        # Resolve immediately on the first pass instead of waiting for one
        # discovery interval (some platforms start monotonic time near zero).
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

                # EOF: detect truncation and replacement, including the POSIX
                # case where the old file remains open after a log rotation.
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
            if self._thread is thread:
                self._thread = None


__all__ = [
    "DEFAULT_LOG_PATH",
    "DEFAULT_START_PATTERNS",
    "RefineryLogEvent",
    "RefineryLogParser",
    "RefineryLogWatcher",
    "discover_refinery_log",
    "parse_duration",
]
