"""Headless blueprint and inventory tracking engine.

The module has four deliberately separate concerns:

* :class:`BlueprintStore` persists the permanent ``unlocked_blueprints.json``
  registry and distinguishes new unlocks from duplicates;
* :class:`BlueprintLogWatcher` in :mod:`app.blueprint_log` owns the read-only
  ``Game.log`` tail loop and regex parsing;
* :class:`BlueprintTracker` owns the background worker that loads the registry,
  records events, writes JSON, and updates counters;
* :class:`BlueprintEventBus` dispatches structured payloads to host listeners.

No Qt classes, widgets, or windows are created here.  A host can subscribe to
callbacks, poll :attr:`BlueprintTracker.event_queue`, or forward
:meth:`InventoryEvent.to_blueprint_dict` through a Qt signal.  The overlay uses
``OverlayApp.blueprint_unlocked_signal`` for the toast/check-list hook.

Typical host usage::

    tracker = BlueprintTracker()
    tracker.subscribe(on_event)                 # full InventoryEvent objects
    tracker.subscribe_blueprints(on_unlock)     # three-key toast payloads
    tracker.start()
    ...
    tracker.close()

The worker thread performs the registry load, every string-to-registry lookup,
and every JSON write.  The main thread only starts/stops the engine and reads
immutable snapshots.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .blueprint_log import (
    KIND_BLUEPRINT,
    KIND_ITEM,
    KIND_LOOT,
    BlueprintLogEvent,
    BlueprintLogWatcher,
)
from .config import config_dir

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
REGISTRY_NAME = "unlocked_blueprints.json"

__all__ = [
    "BlueprintEventBus",
    "BlueprintStats",
    "BlueprintStore",
    "BlueprintTracker",
    "InventoryEvent",
    "REGISTRY_NAME",
    "SCHEMA_VERSION",
    "UnlockRecord",
]


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat(
    ).replace("+00:00", "Z")


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {"false", "0", "no", "off", ""}
    return bool(value)


def _clean(value: object) -> str:
    text = str(value or "").strip().strip("\"'").strip()
    return " ".join(text.split())[:200]


def _key(value: object) -> str:
    return _clean(value).casefold()


def _pick(mapping: Mapping[str, object], *keys: str, default=None):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


@dataclass(frozen=True)
class UnlockRecord:
    """One permanent blueprint or inventory-component record."""

    key: str
    item_name: str
    item_code: str
    kind: str
    first_seen: float
    first_seen_iso: str
    last_seen: float
    last_seen_iso: str
    seen_count: int = 1
    checked_off: bool = True
    source: str = "game_log"
    raw: str = ""

    @property
    def is_blueprint(self) -> bool:
        return self.kind == KIND_BLUEPRINT

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "item_name": self.item_name,
            "item_code": self.item_code,
            "kind": self.kind,
            "first_seen": self.first_seen,
            "first_seen_iso": self.first_seen_iso,
            "last_seen": self.last_seen,
            "last_seen_iso": self.last_seen_iso,
            "seen_count": self.seen_count,
            "checked_off": self.checked_off,
            "source": self.source,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object],
                  kind: str = KIND_BLUEPRINT) -> "UnlockRecord | None":
        if not isinstance(value, Mapping):
            return None
        item_name = _clean(_pick(
            value, "item_name", "name", "blueprint_name", "recipe_name",
            default=""))
        item_code = _clean(_pick(
            value, "item_code", "blueprint_code", "layout_code", "code",
            "item_id", "blueprint_id", default=""))
        if not item_name and not item_code:
            return None
        if not item_name:
            item_name = item_code
        if not item_code:
            item_code = item_name

        first_seen = _num(_pick(
            value, "first_seen", "timestamp", "unlocked_at", default=0.0))
        last_seen = _num(_pick(
            value, "last_seen", "updated_at", default=first_seen))
        if first_seen <= 0:
            first_seen = last_seen
        if last_seen <= 0:
            last_seen = first_seen
        if first_seen <= 0:
            first_seen = time.time()
            last_seen = first_seen

        record_kind = str(_pick(value, "kind", default=kind) or kind)
        return cls(
            key=_key(_pick(value, "key", default="") or item_code or item_name),
            item_name=item_name,
            item_code=item_code,
            kind=record_kind,
            first_seen=first_seen,
            first_seen_iso=str(_pick(
                value, "first_seen_iso", default="") or _iso(first_seen)),
            last_seen=last_seen,
            last_seen_iso=str(_pick(
                value, "last_seen_iso", default="") or _iso(last_seen)),
            seen_count=max(1, _int(_pick(
                value, "seen_count", "count", default=1))),
            checked_off=_bool(value.get("checked_off"), True),
            source=str(value.get("source") or "game_log"),
            raw=str(value.get("raw") or ""),
        )


@dataclass(frozen=True)
class InventoryEvent:
    """Structured output for one recorded blueprint or inventory observation."""

    event_type: str
    item_name: str
    item_code: str
    timestamp: float
    timestamp_iso: str
    is_new: bool
    is_new_unlock: bool
    checked_off: bool
    kind: str
    quantity: int = 0
    container: str = ""
    source: str = "game_log"
    raw: str = ""

    @property
    def is_blueprint(self) -> bool:
        return self.kind == KIND_BLUEPRINT

    def to_blueprint_dict(self) -> dict:
        """Exact payload consumed by the host's toast / shopping-list hook."""
        return {
            "item_name": self.item_name,
            "timestamp": self.timestamp_iso,
            "is_new_unlock": self.is_new_unlock,
        }

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "kind": self.kind,
            "item_name": self.item_name,
            "item_code": self.item_code,
            "timestamp": self.timestamp,
            "timestamp_iso": self.timestamp_iso,
            "is_new": self.is_new,
            "is_new_unlock": self.is_new_unlock,
            "checked_off": self.checked_off,
            "quantity": self.quantity,
            "container": self.container,
            "source": self.source,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class BlueprintStats:
    """Immutable analytics snapshot for the blueprint engine."""

    started_at: float
    updated_at: float
    total_events: int = 0
    blueprint_events: int = 0
    new_blueprints: int = 0
    duplicate_blueprints: int = 0
    item_events: int = 0
    loot_events: int = 0
    blueprints_known: int = 0
    items_known: int = 0
    last_event_at: float | None = None
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "total_events": self.total_events,
            "blueprint_events": self.blueprint_events,
            "new_blueprints": self.new_blueprints,
            "duplicate_blueprints": self.duplicate_blueprints,
            "item_events": self.item_events,
            "loot_events": self.loot_events,
            "blueprints_known": self.blueprints_known,
            "items_known": self.items_known,
            "last_event_at": self.last_event_at,
            "error": self.error,
        }


class BlueprintStore:
    """Atomic JSON registry of permanent blueprint and item unlocks.

    The store is thread-safe, but :class:`BlueprintTracker` is the intended
    owner.  The tracker's worker performs load/save calls so the host window
    never waits on disk.
    """

    def __init__(self, path: str | Path | None = None, *, autoload: bool = True):
        self.path = (Path(path).expanduser() if path is not None
                     else config_dir() / REGISTRY_NAME)
        self._lock = threading.RLock()
        self._blueprints: dict[str, UnlockRecord] = {}
        self._items: dict[str, UnlockRecord] = {}
        self.loaded = False
        self.error = ""
        if autoload:
            self.load()

    # -- persistence ----------------------------------------------------
    def load(self) -> bool:
        with self._lock:
            self._blueprints = {}
            self._items = {}
            self.error = ""
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
            except FileNotFoundError:
                self.loaded = True
                return True
            except (OSError, json.JSONDecodeError) as exc:
                self.error = f"Could not read blueprint registry: {exc}"
                self.loaded = True
                return False

            if isinstance(raw, list):
                blueprint_rows = raw
                item_rows = []
            elif isinstance(raw, dict):
                blueprint_rows = raw.get("blueprints", [])
                item_rows = raw.get("items", [])
            else:
                self.error = "Blueprint registry has an unexpected format"
                self.loaded = True
                return False

            self._blueprints = self._load_rows(blueprint_rows, KIND_BLUEPRINT)
            self._items = self._load_rows(item_rows, KIND_ITEM)
            self.loaded = True
            return True

    @staticmethod
    def _load_rows(rows: object, kind: str) -> dict[str, UnlockRecord]:
        result: dict[str, UnlockRecord] = {}
        if isinstance(rows, Mapping):
            iterable = list(rows.items())
        elif isinstance(rows, list):
            iterable = [(None, row) for row in rows]
        else:
            return result
        for map_key, row in iterable:
            if isinstance(row, str):
                row = {"item_name": row}
            if not isinstance(row, Mapping):
                continue
            record = UnlockRecord.from_dict(row, kind)
            if record is None:
                continue
            key = _key(map_key) or record.key or _key(
                record.item_code or record.item_name)
            if not key:
                continue
            result[key] = replace(record, key=key)
        return result

    def save(self) -> bool:
        with self._lock:
            payload = {
                "version": SCHEMA_VERSION,
                "updated_at": _iso(time.time()),
                "blueprints": {
                    key: record.to_dict()
                    for key, record in sorted(self._blueprints.items())
                },
                "items": {
                    key: record.to_dict()
                    for key, record in sorted(self._items.items())
                },
            }
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2, ensure_ascii=False)
                os.replace(tmp, self.path)
            except OSError as exc:
                self.error = f"Could not save blueprint registry: {exc}"
                return False
            self.error = ""
            return True

    # -- recording ------------------------------------------------------
    def record(self, event: BlueprintLogEvent) -> tuple[UnlockRecord | None, bool]:
        """Record *event*; return ``(record, is_new)``.

        The caller decides when to persist.  This method never performs disk
        I/O, which keeps the worker's save step explicit and testable.
        """
        with self._lock:
            target = (self._blueprints if event.kind == KIND_BLUEPRINT
                      else self._items)
            existing_key = self._find_key(target, event)
            if existing_key is not None:
                old = target.pop(existing_key)
                code_key = _key(event.item_code)
                new_key = code_key or existing_key
                if new_key != existing_key and new_key in target:
                    new_key = existing_key
                record = replace(
                    old,
                    key=new_key,
                    item_name=event.item_name or old.item_name,
                    item_code=event.item_code or old.item_code,
                    last_seen=event.timestamp or old.last_seen,
                    last_seen_iso=(event.timestamp_iso or old.last_seen_iso
                                   or _iso(event.timestamp)),
                    seen_count=old.seen_count + 1,
                    checked_off=True,
                    source=event.source or old.source,
                    raw=event.raw or old.raw,
                )
                target[new_key] = record
                return record, False

            code_key = _key(event.item_code)
            name_key = _key(event.item_name)
            key = code_key or name_key
            if not key:
                return None, False
            record = UnlockRecord(
                key=key,
                item_name=event.item_name or event.item_code,
                item_code=event.item_code or event.item_name,
                kind=event.kind,
                first_seen=event.timestamp,
                first_seen_iso=event.timestamp_iso or _iso(event.timestamp),
                last_seen=event.timestamp,
                last_seen_iso=event.timestamp_iso or _iso(event.timestamp),
                seen_count=1,
                checked_off=True,
                source=event.source or "game_log",
                raw=event.raw,
            )
            target[key] = record
            return record, True

    @staticmethod
    def _find_key(target: Mapping[str, UnlockRecord],
                  event: BlueprintLogEvent) -> str | None:
        candidates = []
        if event.item_code:
            candidates.append(_key(event.item_code))
        if event.item_name:
            candidates.append(_key(event.item_name))
        for key in candidates:
            if key and key in target:
                return key

        code = _key(event.item_code)
        name = _key(event.item_name)
        for key, record in target.items():
            if code and _key(record.item_code) == code:
                return key
            if name and _key(record.item_name) == name:
                return key
        return None

    # -- snapshots ------------------------------------------------------
    def all_blueprints(self) -> tuple[UnlockRecord, ...]:
        with self._lock:
            return tuple(sorted(
                self._blueprints.values(), key=lambda item: -item.first_seen))

    def all_items(self) -> tuple[UnlockRecord, ...]:
        with self._lock:
            return tuple(sorted(
                self._items.values(), key=lambda item: -item.first_seen))

    @property
    def blueprints(self) -> tuple[UnlockRecord, ...]:
        return self.all_blueprints()

    @property
    def items(self) -> tuple[UnlockRecord, ...]:
        return self.all_items()

    def counts(self) -> tuple[int, int]:
        with self._lock:
            return len(self._blueprints), len(self._items)

    def has(self, value: str, kind: str = KIND_BLUEPRINT) -> bool:
        key = _key(value)
        if not key:
            return False
        with self._lock:
            target = (self._blueprints if kind == KIND_BLUEPRINT
                      else self._items)
            if key in target:
                return True
            return any(
                _key(record.item_code) == key or _key(record.item_name) == key
                for record in target.values()
            )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": SCHEMA_VERSION,
                "updated_at": _iso(time.time()),
                "blueprints": [
                    record.to_dict()
                    for record in sorted(
                        self._blueprints.values(),
                        key=lambda item: -item.first_seen)
                ],
                "items": [
                    record.to_dict()
                    for record in sorted(
                        self._items.values(),
                        key=lambda item: -item.first_seen)
                ],
            }


class BlueprintEventBus:
    """Queue-backed listener dispatcher; listeners never block the worker."""

    _SENTINEL = object()

    def __init__(self):
        self._listeners: list[Callable[[InventoryEvent], None]] = []
        self._lock = threading.RLock()
        self._queue: queue.Queue = queue.Queue()
        self._history: list[InventoryEvent] = []
        self._closed = False
        self._thread = threading.Thread(
            target=self._dispatch, name="sc-blueprint-events", daemon=True)
        self._thread.start()

    def subscribe(self, listener: Callable[[InventoryEvent], None]):
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            self._listeners.append(listener)
        return lambda: self.unsubscribe(listener)

    def unsubscribe(self, listener: Callable[[InventoryEvent], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def emit(self, event: InventoryEvent) -> None:
        with self._lock:
            if self._closed:
                return
            self._history.append(event)
            if len(self._history) > 512:
                del self._history[:-512]
        self._queue.put(event)

    @property
    def history(self) -> tuple[InventoryEvent, ...]:
        with self._lock:
            return tuple(self._history)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(self._SENTINEL)
        if self._thread is not threading.current_thread():
            self._thread.join(2.0)

    def _dispatch(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._SENTINEL:
                    return
                with self._lock:
                    listeners = tuple(self._listeners)
                for listener in listeners:
                    try:
                        listener(item)
                    except Exception:
                        logger.exception("blueprint event listener failed")
            finally:
                self._queue.task_done()


class BlueprintTracker:
    """Facade joining the log watcher, registry worker, and event bus."""

    _SENTINEL = object()

    def __init__(
            self,
            *,
            game_log_path: str | Path | None = None,
            registry_path: str | Path | None = None,
            store: BlueprintStore | None = None,
            log_watcher_factory: (
                Callable[[Callable[[BlueprintLogEvent], None]],
                         BlueprintLogWatcher] | None) = None,
            event_bus: BlueprintEventBus | None = None,
            clock: Callable[[], float] | None = None):
        self._game_log_path = (Path(game_log_path).expanduser()
                               if game_log_path else None)
        self._registry_path = (Path(registry_path).expanduser()
                               if registry_path else None)
        self._store = store or BlueprintStore(
            self._registry_path, autoload=False)
        self._store_needs_load = not self._store.loaded
        self._bus = event_bus or BlueprintEventBus()
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._started = False
        self._closed = False
        self._watcher: BlueprintLogWatcher | None = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._event_queue: queue.Queue = queue.Queue()
        self._last_event: InventoryEvent | None = None
        self._error = ""
        self._started_at = 0.0
        self._updated_at = 0.0
        self._total_events = 0
        self._blueprint_events = 0
        self._new_blueprints = 0
        self._duplicate_blueprints = 0
        self._item_events = 0
        self._loot_events = 0
        self._last_event_at: float | None = None

        if log_watcher_factory is not None:
            self._watcher_factory = log_watcher_factory
        else:
            self._watcher_factory = lambda callback: BlueprintLogWatcher(
                path=self._game_log_path, callback=callback)

    # -- lifecycle ------------------------------------------------------
    def __enter__(self) -> "BlueprintTracker":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> "BlueprintTracker":
        with self._lock:
            if self._closed:
                raise RuntimeError("blueprint tracker is closed")
            if self._started:
                return self
            self._started = True
            self._started_at = self._clock()
            self._updated_at = self._started_at
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._run, name="sc-blueprint-worker", daemon=True)
            watcher = self._watcher_factory(self._on_log_event)
            self._watcher = watcher
            self._worker.start()

        try:
            watcher.start()
        except Exception as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
                self._started = False
                self._watcher = None
            self._stop.set()
            self._queue.put(self._SENTINEL)
            raise
        return self

    def stop(self, wait: bool = True) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            watcher = self._watcher
            worker = self._worker
            self._watcher = None
        self._stop.set()
        if watcher is not None:
            watcher.stop(wait=wait)
        self._queue.put(self._SENTINEL)
        if (wait and worker is not None
                and worker is not threading.current_thread()):
            worker.join(2.0)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.stop()
        self._bus.close()

    # -- worker ---------------------------------------------------------
    def _on_log_event(self, event: BlueprintLogEvent) -> None:
        if self._stop.is_set() or self._closed:
            return
        self._queue.put(event)

    def _run(self) -> None:
        try:
            if self._store_needs_load:
                self._store.load()
                self._store_needs_load = False
            self._ready.set()
            while True:
                item = self._queue.get()
                try:
                    if item is self._SENTINEL:
                        return
                    try:
                        self._process(item)
                    except Exception as exc:
                        with self._lock:
                            self._error = f"{type(exc).__name__}: {exc}"
                        logger.exception("blueprint event processing failed")
                finally:
                    self._queue.task_done()
        except Exception as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
            logger.exception("blueprint worker failed")
            self._ready.set()
        finally:
            with self._lock:
                if self._worker is threading.current_thread():
                    self._worker = None

    def _process(self, event: BlueprintLogEvent) -> None:
        record, is_new = self._store.record(event)
        if record is None:
            return
        if not self._store.save():
            with self._lock:
                self._error = self._store.error

        is_blueprint = event.kind == KIND_BLUEPRINT
        payload = InventoryEvent(
            event_type=event.kind,
            item_name=record.item_name,
            item_code=record.item_code,
            timestamp=event.timestamp,
            timestamp_iso=event.timestamp_iso or _iso(event.timestamp),
            is_new=is_new,
            is_new_unlock=bool(is_new and is_blueprint),
            checked_off=record.checked_off,
            kind=event.kind,
            quantity=event.quantity,
            container=event.container,
            source=event.source,
            raw=event.raw,
        )

        with self._lock:
            self._total_events += 1
            if is_blueprint:
                self._blueprint_events += 1
                if is_new:
                    self._new_blueprints += 1
                else:
                    self._duplicate_blueprints += 1
            elif event.kind == KIND_LOOT:
                self._loot_events += 1
            else:
                self._item_events += 1
            self._last_event = payload
            self._last_event_at = event.timestamp
            self._updated_at = self._clock()

        self._event_queue.put(payload)
        self._bus.emit(payload)

    # -- public snapshots ----------------------------------------------
    @property
    def store(self) -> BlueprintStore:
        return self._store

    @property
    def registry_path(self) -> Path:
        return self._store.path

    @property
    def event_queue(self) -> queue.Queue:
        return self._event_queue

    @property
    def event_bus(self) -> BlueprintEventBus:
        return self._bus

    @property
    def last_event(self) -> InventoryEvent | None:
        with self._lock:
            return self._last_event

    @property
    def error(self) -> str:
        with self._lock:
            error = self._error
        return error or self._store.error

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._started and not self._closed)

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Wait for the worker's registry load; call from a non-GUI thread."""
        return self._ready.wait(max(0.0, float(timeout)))

    @property
    def blueprints(self) -> tuple[UnlockRecord, ...]:
        return self._store.all_blueprints()

    @property
    def items(self) -> tuple[UnlockRecord, ...]:
        return self._store.all_items()

    def is_unlocked(self, value: str, kind: str = KIND_BLUEPRINT) -> bool:
        return self._store.has(value, kind)

    @property
    def stats(self) -> BlueprintStats:
        with self._lock:
            started_at = self._started_at
            updated_at = self._updated_at or self._clock()
            total = self._total_events
            blueprint_events = self._blueprint_events
            new_blueprints = self._new_blueprints
            duplicate_blueprints = self._duplicate_blueprints
            item_events = self._item_events
            loot_events = self._loot_events
            last_event_at = self._last_event_at
            error = self._error
        blueprints_known, items_known = self._store.counts()
        return BlueprintStats(
            started_at=started_at,
            updated_at=updated_at,
            total_events=total,
            blueprint_events=blueprint_events,
            new_blueprints=new_blueprints,
            duplicate_blueprints=duplicate_blueprints,
            item_events=item_events,
            loot_events=loot_events,
            blueprints_known=blueprints_known,
            items_known=items_known,
            last_event_at=last_event_at,
            error=error or self._store.error,
        )

    # -- callbacks ------------------------------------------------------
    def subscribe(self, listener: Callable[[InventoryEvent], None]):
        return self._bus.subscribe(listener)

    def subscribe_blueprints(self, listener: Callable[[dict], None]):
        """Subscribe with the exact toast/check-list payload only."""
        if not callable(listener):
            raise TypeError("listener must be callable")

        def forward(event: InventoryEvent) -> None:
            if event.is_blueprint:
                listener(event.to_blueprint_dict())

        return self._bus.subscribe(forward)

    def unsubscribe(self, listener: Callable[[InventoryEvent], None]) -> None:
        self._bus.unsubscribe(listener)
