"""Read-only blueprint and inventory feed parser for ``Game.log``.

Star Citizen writes a small number of notification and inventory-database lines
that are useful to an overlay:

* ``Received Blueprint: <name>: `` notifications (the authoritative unlock line);
* generic crafting/recipe unlock prose;
* inventory ``AddItem`` / acquisition / loot notifications.

The parser is deliberately Qt-free and side-effect-free.  :class:`BlueprintLogWatcher`
owns the read-only tail loop, and every parse/IO operation runs in a background
daemon thread.  A missing, rotated, or truncated log is never fatal.

The canonical blueprint notification currently looks like::

    <2026-03-26T17:15:41.684Z> [Notice] <SHUDEvent_OnNotification> Added
    notification "Received Blueprint: Defiance Helmet Tactical: " [23] to queue.

The ``<UpdateNotificationItem>`` echo lines are ignored so one unlock produces
one event.  Names are returned raw; the registry in :mod:`app.blueprints`
normalizes them for duplicate detection.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

KIND_BLUEPRINT = "blueprint_unlocked"
KIND_ITEM = "item_acquired"
KIND_LOOT = "loot_opened"

DEFAULT_LOG_PATH = Path.home() / "starcitizen" / "LIVE" / "Game.log"

BlueprintLogCallback = Callable[["BlueprintLogEvent"], None]

_LOG_TIMESTAMP = re.compile(
    r"<(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)>"
)

# Exact current-format blueprint notification.  The name is non-greedy so
# display names containing quotes survive intact.
_BLUEPRINT_EVENT = re.compile(
    r"(?i)<SHUDEvent_OnNotification>\s+Added\s+notification\s+"
    r'["\']Received\s+Blueprint:\s*(?P<name>.*?):\s*["\']\s*\[\d+\]'
)

_BLUEPRINT_ACTIONS = (
    r"unlock(?:ed|ing|s)?|learn(?:ed|ing|s)?|acquire(?:d|ing|s)?|"
    r"discover(?:ed|ing|s)?|receiv(?:e|ed|ing|es)|add(?:ed|ing|s)?|"
    r"grant(?:ed|ing|s)?|award(?:ed|ing|s)?|drop(?:ped|ping|s)?|"
    r"found|craft(?:ed|ing|s)?|purchas(?:e|ed|ing|es)|buy|bought|"
    r"reward(?:ed|ing|s)?|obtain(?:ed|ing|s)?"
)

_ITEM_ACTIONS = (
    r"add(?:ed|ing|s)?|acquire(?:d|ing|s)?|receiv(?:e|ed|ing|es)|"
    r"loot(?:ed|ing|s)?|purchas(?:e|ed|ing|es)|buy|bought|"
    r"stow(?:ed|ing|s)?|transfer(?:red|ring|s)?|grant(?:ed|ing|s)?|"
    r"award(?:ed|ing|s)?|found|open(?:ed|ing|s)?|unpack(?:ed|ing|s)?|"
    r"collect(?:ed|ing|s)?|pick(?:ed)?\s+up|obtain(?:ed|ing|s)?"
)

DEFAULT_BLUEPRINT_PATTERNS = (
    _BLUEPRINT_EVENT,
    re.compile(
        rf"(?i)\b(?:blueprint|crafting\s+blueprint|recipe|crafting\s+recipe)\b"
        rf".{{0,140}}\b(?:{_BLUEPRINT_ACTIONS})\b"
    ),
    re.compile(
        rf"(?i)\b(?:{_BLUEPRINT_ACTIONS})\b"
        rf".{{0,140}}\b(?:blueprint|crafting\s+blueprint|recipe|"
        rf"crafting\s+recipe)\b"
    ),
    re.compile(
        r"(?i)\b(?:CraftingBlueprint|BlueprintUnlock|CBlueprint|"
        r"BlueprintAcquired|RecipeUnlock|CraftingRecipe)\w*"
    ),
)

DEFAULT_ITEM_PATTERNS = (
    re.compile(
        rf"(?i)\b(?:item|items|inventory|cargo|stowage|stash|container|crate|"
        rf"box|locker|loot|corpse|body)\b.{{0,140}}\b(?:{_ITEM_ACTIONS})\b"
    ),
    re.compile(
        rf"(?i)\b(?:{_ITEM_ACTIONS})\b.{{0,140}}\b(?:item|items|inventory|"
        rf"cargo|stowage|stash|container|crate|box|locker|loot|corpse|body)\b"
    ),
    re.compile(
        r"(?i)\b(?:CInventory|CItem|CSCItem|Cargo|CSCEntity)\w*.*"
        r"\b(?:Add|Acquire|Receive|Loot|Stow|Transfer|Grant|Award|Open|"
        r"Unpack|Collect)\w*\b"
    ),
    re.compile(
        r"(?i)\b(?:loot(?:ed)?|opened?|open)\b.{0,70}\b"
        r"(?:crate|box|container|locker|corpse|body)\b"
    ),
    re.compile(
        r"(?i)\b(?:crate|box|container|locker|corpse|body)\b.{0,70}\b"
        r"(?:loot(?:ed)?|opened?|open)\b"
    ),
    re.compile(
        r"(?i)<SHUDEvent_OnNotification>\s+Added\s+notification\s+"
        r'["\'](?:You\s+)?(?:found|received|acquired|looted|obtained|'
        r"picked\s+up)\b"
    ),
)

_LOOT_PATTERN = re.compile(
    r"(?i)\b(?:loot(?:ed)?|opened?|open)\b.{0,70}\b"
    r"(?:crate|box|container|locker|corpse|body)\b|"
    r"\b(?:crate|box|container|locker|corpse|body)\b.{0,70}\b"
    r"(?:loot(?:ed)?|opened?|open)\b"
)

# Use explicit field names here.  Generic keys such as ``Blueprint:`` or
# ``Item:`` also appear inside notification prose and UI echo lines, where a
# colon is punctuation rather than a key/value separator.
_NAME_KEYS = (
    "item_name", "entity_name", "class_name", "blueprint_name",
    "recipe_name", "container_name", "loot_name", "crate_name", "box_name",
)
_CODE_KEYS = (
    "blueprint_id", "blueprint_code", "layout_code", "item_id", "entity_id",
    "class_name", "class", "code", "id",
)

_QUOTED_LABELED = re.compile(
    r"(?i)\b(?P<label>blueprint|recipe|item|entity|class|container|crate|box|"
    r"locker|loot|corpse|body)"
    r"\s*(?:name|id|code)?\s*[:=]?\s*[\"'](?P<value>[^\"']+)[\"']"
)
_QUOTED_AFTER_ACTION = re.compile(
    rf"(?i)\b(?:{_BLUEPRINT_ACTIONS}|{_ITEM_ACTIONS})\s+"
    r"(?:the\s+)?(?:blueprint|recipe|item|entity|class|container|crate|box|"
    r"locker|loot|corpse|body)?"
    r"\s*[\"'](?P<value>[^\"']+)[\"']"
)
_CODE_TOKEN = re.compile(
    r"(?i)\b(?:BP|CRAFT|CRAFTING|BLUEPRINT|ITEM|ENTITY|SCITEM|CSCITEM|"
    r"CITEM)_(?!name\b|id\b|code\b)[A-Za-z0-9_\-]+\b"
)
_NOTIFICATION_TEXT = re.compile(
    r"(?i)Added\s+notification\s+[\"'](?P<text>.+?)[\"']\s*\[\d+\]"
)
_TRAILING_PUNCT = re.compile(r"[\s,;:.!?]+$")


@dataclass(frozen=True)
class BlueprintLogEvent:
    """One parsed blueprint or inventory observation from ``Game.log``."""

    kind: str
    item_name: str
    item_code: str = ""
    timestamp: float = 0.0
    timestamp_iso: str = ""
    quantity: int = 0
    container: str = ""
    source: str = "game_log"
    raw: str = ""

    @property
    def is_blueprint(self) -> bool:
        return self.kind == KIND_BLUEPRINT

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "item_name": self.item_name,
            "item_code": self.item_code,
            "timestamp": self.timestamp,
            "timestamp_iso": self.timestamp_iso,
            "quantity": self.quantity,
            "container": self.container,
            "source": self.source,
            "raw": self.raw,
        }


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat(
    ).replace("+00:00", "Z")


def parse_log_timestamp(line: str, now: float | None = None) -> tuple[float, str]:
    """Return ``(epoch, iso_utc)`` from a ``Game.log`` timestamp or *now*."""
    match = _LOG_TIMESTAMP.search(line or "")
    if match is not None:
        try:
            stamp = datetime.fromisoformat(
                match.group("stamp").replace("Z", "+00:00"))
            stamp = stamp.astimezone(timezone.utc)
            return stamp.timestamp(), stamp.isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    epoch = time.time() if now is None else float(now)
    return epoch, _iso_from_epoch(epoch)


def _clean_name(value: object) -> str:
    text = str(value or "").strip().strip("\"'").strip()
    text = _TRAILING_PUNCT.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text[:180]


def _clean_code(value: object) -> str:
    return _clean_name(value)[:180]


def _field(line: str, *keys: str) -> str:
    """Return the first matching ``key=value`` / ``key: value`` field."""
    for key in keys:
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
        value = _clean_name(match.group(1))
        if value:
            return value
    return ""


def _quantity(line: str) -> int:
    value = _field(line, "quantity", "count", "amount", "units", "qty")
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _notification_text(raw: str) -> str:
    match = _NOTIFICATION_TEXT.search(raw)
    return _clean_name(match.group("text")) if match else ""


def _extract_from_notification(text: str) -> str:
    """Pull a display name out of a notification body."""
    text = _clean_name(text)
    if not text:
        return ""
    patterns = (
        re.compile(r"(?i)^Received\s+Blueprint:\s*(?P<name>.*?):\s*$"),
        re.compile(
            r"(?i)^(?:Blueprint|Recipe)\s+"
            r"(?:acquired|unlocked|learned|discovered|received|added|"
            r"granted|found)\s*[:\-]\s*(?P<name>.+)$"
        ),
        re.compile(
            r"(?i)^(?:Blueprint|Recipe|Item|Entity|Class|Container|Crate|Box)"
            r"\s*(?:name|id|code)?\s*[:\-]\s*(?P<name>.+)$"
        ),
        re.compile(
            r"(?i)^(?:You\s+)?(?:found|received|acquired|looted|obtained|"
            r"picked\s+up|unlocked|learned|discovered|added|granted|awarded)"
            r"\s+(?:a|an|the)?\s*(?P<name>.+)$"
        ),
        re.compile(
            r"(?i)^(?P<name>[A-Za-z0-9][\w\-\.' ]{1,100}?)\s+"
            r"(?:added|received|acquired|looted|obtained|granted|awarded)\b"
        ),
    )
    for pattern in patterns:
        match = pattern.match(text)
        if match is not None:
            name = _clean_name(match.group("name"))
            if name:
                return name
    return ""


def _extract_name(raw: str) -> str:
    exact = _BLUEPRINT_EVENT.search(raw)
    if exact is not None:
        name = _clean_name(exact.group("name"))
        if name:
            return name

    notification = _notification_text(raw)
    if notification:
        name = _extract_from_notification(notification)
        if name:
            return name

    name = _field(raw, *_NAME_KEYS)
    if name:
        return name

    # Prefer the last quoted value after an action.  A loot line commonly says
    # ``Opened loot box 'Crate_123' and found item 'Coda Pistol'``; the item,
    # not the container, is the interesting acquisition.
    for match in reversed(list(_QUOTED_AFTER_ACTION.finditer(raw))):
        name = _clean_name(match.group("value"))
        if name:
            return name

    container_labels = {"container", "crate", "box"}
    labeled = list(_QUOTED_LABELED.finditer(raw))
    for match in labeled:
        if match.group("label").casefold() in container_labels:
            continue
        name = _clean_name(match.group("value"))
        if name:
            return name
    for match in labeled:
        name = _clean_name(match.group("value"))
        if name:
            return name

    match = _CODE_TOKEN.search(raw)
    if match is not None:
        return _clean_name(match.group(0))
    return ""


def _extract_code(raw: str, name: str) -> str:
    code = _field(raw, *_CODE_KEYS)
    if code:
        return _clean_code(code)
    match = _CODE_TOKEN.search(raw)
    if match is not None:
        return _clean_code(match.group(0))
    return _clean_code(name)


def _pretty_item_name(name: str) -> str:
    """Turn class/layout codes into a readable label while preserving words."""
    text = _clean_name(name)
    if not text:
        return ""
    original = text
    prefixes = re.compile(
        r"(?i)^(?:BP|CRAFT|CRAFTING|BLUEPRINT|ITEM|ENTITY|CLASS|SCITEM|"
        r"CSCITEM|CITEM|CRAFTINGBLUEPRINT)_"
    )
    while True:
        stripped = prefixes.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"_\d{4,}$", "", text)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or original


def _extract_container(raw: str) -> str:
    container = _field(
        raw, "container_name", "container", "loot_box", "crate", "box",
        "locker", "corpse", "body")
    if container:
        return _clean_name(container)
    match = re.search(
        r"(?i)\b(?:container|crate|box|locker|loot_box|corpse|body)"
        r"\s+['\"]([^'\"]+)['\"]", raw)
    return _clean_name(match.group(1)) if match else ""


class BlueprintLogParser:
    """Pure parser for blueprint and inventory log observations."""

    def __init__(self,
                 blueprint_patterns: Iterable[re.Pattern] | None = None,
                 item_patterns: Iterable[re.Pattern] | None = None):
        self.blueprint_patterns = tuple(
            DEFAULT_BLUEPRINT_PATTERNS if blueprint_patterns is None
            else blueprint_patterns)
        self.item_patterns = tuple(
            DEFAULT_ITEM_PATTERNS if item_patterns is None
            else item_patterns)

    @staticmethod
    def _matches(raw: str, patterns: Iterable[re.Pattern]) -> bool:
        return any(pattern.search(raw) is not None for pattern in patterns)

    def parse_line(self, line: str,
                   now: float | None = None) -> list[BlueprintLogEvent]:
        raw = line.rstrip("\r\n")
        if not raw:
            return []

        is_blueprint = (
            _BLUEPRINT_EVENT.search(raw) is not None
            or self._matches(raw, self.blueprint_patterns)
        )
        is_item = self._matches(raw, self.item_patterns)
        if not is_blueprint and not is_item:
            return []

        name = _extract_name(raw)
        if not name:
            return []
        code = _extract_code(raw, name)
        pretty = _pretty_item_name(name)
        timestamp, timestamp_iso = parse_log_timestamp(raw, now=now)

        if is_blueprint:
            kind = KIND_BLUEPRINT
        elif _LOOT_PATTERN.search(raw) is not None:
            kind = KIND_LOOT
        else:
            kind = KIND_ITEM

        return [BlueprintLogEvent(
            kind=kind,
            item_name=pretty or name,
            item_code=code or name,
            timestamp=timestamp,
            timestamp_iso=timestamp_iso,
            quantity=_quantity(raw),
            container=_extract_container(raw),
            source="game_log",
            raw=raw[:2000],
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


def discover_blueprint_log() -> Path | None:
    """Return the first existing known ``Game.log`` path, or ``None``."""
    for path in _candidate_logs():
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    try:
        from .game_log import discover_game_log as _discover
        return _discover()
    except Exception:
        return None


class BlueprintLogWatcher:
    """Daemon thread that tails one read-only log and forwards parsed events."""

    def __init__(self, path: str | Path | None = None,
                 callback: BlueprintLogCallback | None = None,
                 poll_interval: float = 0.5,
                 discovery_interval: float = 8.0,
                 from_start: bool = False,
                 parser: BlueprintLogParser | None = None):
        self._path = Path(path).expanduser() if path else None
        self._callback = callback
        self._poll_interval = max(0.1, float(poll_interval))
        self._discovery_interval = max(1.0, float(discovery_interval))
        self._from_start = bool(from_start)
        self._parser = parser or BlueprintLogParser()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop.is_set())

    def start(self) -> "BlueprintLogWatcher":
        if self.running:
            return self
        previous = self._thread
        if (previous is not None and previous.is_alive()
                and previous is not threading.current_thread()):
            previous.join(2.0)
            if previous.is_alive():
                return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="sc-blueprint-log", daemon=True)
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
        self._path = discover_blueprint_log()
        return self._path

    @staticmethod
    def _file_identity(handle) -> tuple[int, int] | None:
        try:
            stat = os.fstat(handle.fileno())
            return (int(stat.st_dev), int(stat.st_ino))
        except (OSError, ValueError, AttributeError):
            return None

    def _open(self, path: Path, *, at_end: bool = True):
        handle = open(path, "r", encoding="utf-8", errors="replace")
        if at_end and not self._from_start:
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
        reopen_at_start = False
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
                        handle = self._open(
                            current, at_end=not reopen_at_start)
                        reopen_at_start = False
                        handle_id = self._file_identity(handle)
                    except OSError:
                        current = None
                        handle_id = None
                        self._stop.wait(self._discovery_interval)
                        continue

                try:
                    position = handle.tell()
                    line = handle.readline()
                except (OSError, ValueError):
                    position = None
                    line = ""

                # Do not parse a line the game is still writing.  Text-mode
                # ``tell()`` returns a seekable cookie, so rewind to it and
                # wait for the terminating newline on the next poll.
                if line and not line.endswith(("\n", "\r")):
                    if position is not None:
                        try:
                            handle.seek(position)
                        except OSError:
                            pass
                    line = ""

                if line:
                    try:
                        events = self._parser.parse_line(line)
                    except Exception:
                        events = ()
                    for event in events:
                        if self._stop.is_set():
                            break
                        if self._callback is not None:
                            try:
                                self._callback(event)
                            except Exception:
                                # A consumer must never kill the tailer.
                                pass
                    continue

                # At EOF, detect truncation/rotation before sleeping.  A
                # replaced log keeps the old handle alive on POSIX, so compare
                # the path identity as well as the size.
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
                        reopen_at_start = True
                        last_discovery = (
                            time.monotonic() - self._discovery_interval)
                        continue
                except OSError:
                    handle.close()
                    handle = None
                    handle_id = None
                    current = None
                    last_discovery = (
                        time.monotonic() - self._discovery_interval)
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
    "DEFAULT_BLUEPRINT_PATTERNS",
    "DEFAULT_ITEM_PATTERNS",
    "DEFAULT_LOG_PATH",
    "KIND_BLUEPRINT",
    "KIND_ITEM",
    "KIND_LOOT",
    "BlueprintLogEvent",
    "BlueprintLogParser",
    "BlueprintLogWatcher",
    "discover_blueprint_log",
    "parse_log_timestamp",
]
