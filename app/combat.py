"""Headless combat tracking engine for the Star Citizen overlay.

The module separates four concerns:

* :class:`CombatLogWatcher` in :mod:`app.combat_log` owns the read-only
  ``Game.log`` tail loop and the combat regexes;
* :class:`CombatTracker` decides whether a target changed operational state;
* :class:`CombatSessionStats` snapshots the current gameplay loop;
* :class:`CombatEventBus` delivers events on a dedicated listener thread.

No Qt classes, widgets, or windows are created here.  A host can subscribe to
callbacks, poll :attr:`CombatTracker.event_queue`, or forward
:meth:`CombatEvent.to_dict` through a Qt signal.  The tracker also exposes
:meth:`CombatTracker.reset_session` and :meth:`CombatTracker.flush_stats` for
the end of a gameplay loop.

Typical host usage::

    tracker = CombatTracker()
    tracker.subscribe(on_combat_event)
    tracker.start()
    ...
    tracker.reset_session()
    tracker.close()
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .combat_log import (
    KIND_PLAYER_KILL,
    KIND_VEHICLE_DAMAGE,
    KIND_VEHICLE_DESTRUCTION,
    KIND_WEAPON_IMPACT,
    STATUS_ACTIVE,
    STATUS_DESTROYED,
    STATUS_ORDER,
    STATUS_SOFT_DEAD,
    CombatLogEvent,
    CombatLogWatcher,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CombatEvent",
    "CombatEventBus",
    "CombatSessionStats",
    "CombatState",
    "CombatTargetState",
    "CombatTracker",
    "normalize_status",
]

_DAMAGE_KINDS = frozenset({
    KIND_VEHICLE_DAMAGE,
    KIND_WEAPON_IMPACT,
    KIND_VEHICLE_DESTRUCTION,
    KIND_PLAYER_KILL,
})
_TERMINAL_STATUSES = frozenset({STATUS_SOFT_DEAD, STATUS_DESTROYED})


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _pick(mapping: Mapping[str, object], *keys: str, default=None):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


def _clean_name(value: object) -> str:
    text = str(value or "").strip().strip("\"'").strip()
    if not text:
        return ""
    return " ".join(text.split())[:160]


def normalize_status(value: object) -> str:
    """Map common status spellings onto the three public operational states."""
    text = str(value or "").strip().casefold()
    text = text.replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    if text in {"soft dead", "softdead", "soft death", "disabled", "crippled",
                "immobilized", "immobilised", "incapacitated", "inoperable"}:
        return STATUS_SOFT_DEAD
    if text in {"destroyed", "destroy", "dead", "killed", "wrecked",
                "exploded", "explosion"}:
        return STATUS_DESTROYED
    return STATUS_ACTIVE


@dataclass(frozen=True)
class CombatSessionStats:
    """Immutable analytics snapshot for the active gameplay loop."""

    session_started_at: float
    updated_at: float
    targets_seen: int = 0
    active_targets: int = 0
    targets_neutralized: int = 0
    soft_dead_targets: int = 0
    destroyed_targets: int = 0
    total_events: int = 0
    damage_events: int = 0
    weapon_impacts: int = 0
    kills: int = 0
    player_kills: int = 0
    deaths: int = 0
    engagement_count: int = 0
    last_engagement_at: float | None = None
    last_engagement_delta: float | None = None
    average_engagement_delta: float | None = None
    uptime_seconds: float = 0.0
    current_survival_seconds: float = 0.0
    longest_survival_seconds: float = 0.0
    last_event_at: float | None = None
    time_since_last_event: float | None = None
    last_damage_at: float | None = None
    time_since_last_damage: float | None = None
    error: str = ""

    @property
    def total_targets_neutralized(self) -> int:
        return self.targets_neutralized

    @property
    def survival_uptime_seconds(self) -> float:
        return self.uptime_seconds

    def to_dict(self) -> dict:
        return {
            "session_started_at": self.session_started_at,
            "updated_at": self.updated_at,
            "targets_seen": self.targets_seen,
            "active_targets": self.active_targets,
            "targets_neutralized": self.targets_neutralized,
            "total_targets_neutralized": self.targets_neutralized,
            "soft_dead_targets": self.soft_dead_targets,
            "destroyed_targets": self.destroyed_targets,
            "total_events": self.total_events,
            "damage_events": self.damage_events,
            "weapon_impacts": self.weapon_impacts,
            "kills": self.kills,
            "player_kills": self.player_kills,
            "deaths": self.deaths,
            "engagement_count": self.engagement_count,
            "last_engagement_at": self.last_engagement_at,
            "last_engagement_delta": self.last_engagement_delta,
            "last_engagement_delta_seconds": self.last_engagement_delta,
            "average_engagement_delta": self.average_engagement_delta,
            "average_engagement_interval_seconds":
                self.average_engagement_delta,
            "uptime_seconds": self.uptime_seconds,
            "survival_uptime_seconds": self.uptime_seconds,
            "survival_uptime": self.uptime_seconds,
            "current_survival_seconds": self.current_survival_seconds,
            "longest_survival_seconds": self.longest_survival_seconds,
            "last_event_at": self.last_event_at,
            "time_since_last_event_seconds": self.time_since_last_event,
            "last_damage_at": self.last_damage_at,
            "time_since_last_damage_seconds": self.time_since_last_damage,
            "error": self.error,
        }


@dataclass(frozen=True)
class CombatTargetState:
    """Last known operational state for one target."""

    key: str
    target_name: str
    target_type: str
    status: str
    first_seen: float
    last_seen: float
    kind: str
    attacker: str = ""
    weapon: str = ""
    raw: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "target_name": self.target_name,
            "target_type": self.target_type,
            "operational_status": self.status,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "kind": self.kind,
            "attacker": self.attacker,
            "weapon": self.weapon,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class CombatEvent:
    """Structured payload delivered to a host UI or queue consumer.

    ``to_dict()`` is the serialisable form.  The operational status is exposed
    as both ``operational_status`` (the public API name) and ``status`` (a
    shorter alias used by compact HUD code).
    """

    event_type: str
    kind: str
    timestamp: float
    operational_status: str
    target_name: str
    target_type: str
    previous_status: str = ""
    target_id: str = ""
    attacker: str = ""
    weapon: str = ""
    weapon_class: str = ""
    damage_type: str = ""
    zone: str = ""
    from_level: int | None = None
    to_level: int | None = None
    raw: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.operational_status

    @property
    def terminal(self) -> bool:
        return self.operational_status in _TERMINAL_STATUSES

    @property
    def status_changed(self) -> bool:
        return self.event_type == "target_state"

    def to_dict(self) -> dict:
        try:
            iso = datetime.fromtimestamp(
                self.timestamp, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            iso = ""
        return {
            "event_type": self.event_type,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "timestamp_iso": iso,
            "target_name": self.target_name,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "operational_status": self.operational_status,
            "status": self.operational_status,
            "previous_status": self.previous_status,
            "status_changed": self.status_changed,
            "terminal": self.terminal,
            "attacker": self.attacker,
            "weapon": self.weapon,
            "weapon_class": self.weapon_class,
            "damage_type": self.damage_type,
            "zone": self.zone,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "raw": self.raw,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class CombatState:
    """Full immutable snapshot exposed to the host."""

    stats: CombatSessionStats
    targets: tuple[CombatTargetState, ...]
    last_event: CombatEvent | None
    updated_at: float
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "stats": self.stats.to_dict(),
            "targets": [target.to_dict() for target in self.targets],
            "last_event": (self.last_event.to_dict()
                           if self.last_event else None),
            "updated_at": self.updated_at,
            "error": self.error,
        }


class CombatEventBus:
    """Queue-backed listener dispatcher; listeners never block the log loop."""

    _SENTINEL = object()

    def __init__(self):
        self._listeners: list[Callable[[CombatEvent], None]] = []
        self._lock = threading.RLock()
        self._queue: queue.Queue = queue.Queue()
        self._history: deque[CombatEvent] = deque(maxlen=512)
        self._closed = False
        self._thread = threading.Thread(
            target=self._dispatch, name="sc-combat-events", daemon=True)
        self._thread.start()

    def subscribe(self, listener: Callable[[CombatEvent], None]):
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            self._listeners.append(listener)
        return lambda: self.unsubscribe(listener)

    def unsubscribe(self, listener: Callable[[CombatEvent], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def emit(self, event: CombatEvent) -> None:
        with self._lock:
            if self._closed:
                return
            self._history.append(event)
        self._queue.put(event)

    @property
    def history(self) -> tuple[CombatEvent, ...]:
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
                        logger.exception("combat event listener failed")
            finally:
                self._queue.task_done()


class CombatTracker:
    """Facade joining the log watcher, target state, and session analytics."""

    def __init__(
            self,
            *,
            game_log_path: str | Path | None = None,
            log_watcher_factory: (
                Callable[[Callable[[CombatLogEvent], None]],
                         CombatLogWatcher] | None) = None,
            event_bus: CombatEventBus | None = None,
            clock: Callable[[], float] | None = None):
        self._game_log_path = (Path(game_log_path).expanduser()
                               if game_log_path else None)
        self._bus = event_bus or CombatEventBus()
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._started = False
        self._closed = False
        self._watcher: CombatLogWatcher | None = None
        self._last_event: CombatEvent | None = None
        self._error = ""
        self._event_queue: queue.Queue = queue.Queue(maxsize=1024)
        if log_watcher_factory is not None:
            self._watcher_factory = log_watcher_factory
        else:
            self._watcher_factory = lambda callback: CombatLogWatcher(
                path=self._game_log_path, callback=callback)

        self._reset_state_locked(self._clock())

    # -- lifecycle ------------------------------------------------------
    def __enter__(self) -> "CombatTracker":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> "CombatTracker":
        """Start tailing the log and emit a session-started event."""
        with self._lock:
            if self._closed:
                raise RuntimeError("combat tracker is closed")
            if self._started:
                return self
            now = self._clock()
            # A tracker may be constructed long before the host starts it.
            # Start the survival clock at start() unless manual events already
            # arrived before the log watcher was attached.
            if not self._targets and self._total_events == 0:
                self._reset_state_locked(now)
            self._started = True
            watcher = self._watcher_factory(self._on_log_event)
            self._watcher = watcher
            self._emit_locked(self._make_event(
                event_type="session_started",
                kind="session_started",
                status=STATUS_ACTIVE,
                target_name="",
                target_type="Session",
                timestamp=now,
                detail={"session_started_at": self._session_started_at},
            ))
        try:
            watcher.start()
        except Exception as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
                self._started = False
                self._watcher = None
            raise
        return self

    def stop(self, wait: bool = True) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            watcher = self._watcher
            self._watcher = None
        if watcher is not None:
            watcher.stop(wait=wait)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.stop(wait=True)
        self._bus.close()

    # -- subscriptions and snapshots ------------------------------------
    def subscribe(self, listener: Callable[[CombatEvent], None]):
        return self._bus.subscribe(listener)

    def unsubscribe(self, listener: Callable[[CombatEvent], None]) -> None:
        self._bus.unsubscribe(listener)

    @property
    def event_bus(self) -> CombatEventBus:
        return self._bus

    @property
    def event_queue(self) -> queue.Queue:
        """Queue receiving ``CombatEvent`` objects for polling hosts."""
        return self._event_queue

    @property
    def last_event(self) -> CombatEvent | None:
        with self._lock:
            return self._last_event

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    @property
    def game_log_path(self) -> Path | None:
        if self._game_log_path is not None:
            return self._game_log_path
        with self._lock:
            watcher = self._watcher
        return watcher.path if watcher is not None else None

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._started and not self._closed)

    @property
    def stats(self) -> CombatSessionStats:
        with self._lock:
            return self._snapshot_locked(self._clock())

    @property
    def targets(self) -> tuple[CombatTargetState, ...]:
        with self._lock:
            return tuple(sorted(
                self._targets.values(),
                key=lambda target: (target.last_seen, target.target_name),
                reverse=True))

    @property
    def state(self) -> CombatState:
        with self._lock:
            now = self._clock()
            return CombatState(
                stats=self._snapshot_locked(now),
                targets=self.targets,
                last_event=self._last_event,
                updated_at=now,
                error=self._error,
            )

    def target_status(self, target: str) -> str | None:
        """Return the last known status for a name or target key."""
        wanted = str(target or "").strip()
        if not wanted:
            return None
        key = wanted.casefold()
        with self._lock:
            for target_key, state in self._targets.items():
                if (target_key.casefold() == key
                        or state.target_name.casefold() == key):
                    return state.status
        return None

    # -- manual and parsed events --------------------------------------
    def emit_combat_event(
            self,
            event: CombatLogEvent | CombatEvent | Mapping[str, object],
    ) -> CombatEvent | None:
        """Update target state and emit a structured combat payload.

        Returns the emitted :class:`CombatEvent`, or ``None`` when the update
        was a duplicate terminal observation, a status downgrade, targetless,
        or the tracker is closed.  Callers can also construct a
        :class:`CombatLogEvent` and pass it directly.
        """
        parsed = self._coerce_event(event)
        if parsed is None:
            return None

        with self._lock:
            if self._closed:
                return None
            stamp = parsed.timestamp or self._clock()
            status = normalize_status(parsed.status)
            target_name = _clean_name(parsed.target_name)
            target_type = _clean_name(parsed.target_type) or "Vehicle"
            key = self._resolve_target_key(
                parsed.target_id, target_name, target_type)
            previous = self._targets[key].status if key in self._targets else ""

            # A late soft-death line must never overwrite a destroyed state.
            if previous and (STATUS_ORDER[status]
                             < STATUS_ORDER[previous]):
                return None
            if key and status in _TERMINAL_STATUSES and status == previous:
                # Repeated destruction lines are common during cleanup; keep
                # the target state but do not flash the HUD or stats twice.
                return None

            transition = bool(key and status != previous)
            self._last_event_at = stamp
            self._total_events += 1
            if parsed.kind in _DAMAGE_KINDS:
                self._damage_events += 1
                self._last_damage_at = stamp
            if parsed.kind == KIND_WEAPON_IMPACT:
                self._weapon_impacts += 1
            if parsed.kind == KIND_PLAYER_KILL:
                self._kills += 1
                if not parsed.is_npc_attacker:
                    self._player_kills += 1
                if not parsed.is_npc_target:
                    self._deaths += 1

            if key:
                old = self._targets.get(key)
                self._targets[key] = CombatTargetState(
                    key=key,
                    target_name=target_name,
                    target_type=target_type,
                    status=status,
                    first_seen=(old.first_seen if old else stamp),
                    last_seen=stamp,
                    kind=parsed.kind,
                    attacker=parsed.attacker,
                    weapon=parsed.weapon,
                    raw=parsed.raw,
                )
                if status == STATUS_SOFT_DEAD:
                    self._soft_dead.add(key)
                elif status == STATUS_DESTROYED:
                    self._destroyed.add(key)
                if status in _TERMINAL_STATUSES and key not in self._neutralized:
                    self._neutralized.add(key)
                    self._record_engagement_locked(stamp)

            if not key:
                # A damage line with no identifiable target still contributes
                # to counters, but it cannot produce a useful HUD alert.
                return None
            if not transition and parsed.kind == KIND_VEHICLE_DESTRUCTION:
                return None

            event_type = "target_state" if transition else parsed.kind
            emitted = self._make_event(
                event_type=event_type,
                kind=parsed.kind,
                status=status,
                target_name=target_name,
                target_type=target_type,
                timestamp=stamp,
                previous_status=previous,
                target_id=parsed.target_id,
                attacker=parsed.attacker,
                weapon=parsed.weapon,
                weapon_class=parsed.weapon_class,
                damage_type=parsed.damage_type,
                zone=parsed.zone,
                from_level=parsed.from_level,
                to_level=parsed.to_level,
                raw=parsed.raw,
            )
            self._emit_locked(emitted)
            return emitted

    # -- session analytics ---------------------------------------------
    def reset_session(self) -> CombatSessionStats:
        """Clear all targets, counters, and engagement timing for a new loop."""
        with self._lock:
            now = self._clock()
            if self._closed:
                return self._snapshot_locked(now)
            self._reset_state_locked(now)
            event = self._make_event(
                event_type="session_reset",
                kind="session_reset",
                status=STATUS_ACTIVE,
                target_name="",
                target_type="Session",
                timestamp=now,
                detail={"session_started_at": now},
            )
            self._emit_locked(event)
            return self._snapshot_locked(now)

    def reset_analytics(self) -> CombatSessionStats:
        """Alias for :meth:`reset_session`."""
        return self.reset_session()

    def flush_stats(self, *, reset: bool = False) -> CombatSessionStats:
        """Return a snapshot, optionally resetting the session afterwards.

        With ``reset=True`` the returned snapshot describes the loop that just
        ended; the tracker immediately starts a clean session.
        """
        snapshot = self.stats
        if reset:
            self.reset_session()
        return snapshot

    # -- internals ------------------------------------------------------
    def _on_log_event(self, event: CombatLogEvent) -> None:
        self.emit_combat_event(event)

    def _coerce_event(
            self,
            value: CombatLogEvent | CombatEvent | Mapping[str, object],
    ) -> CombatLogEvent | None:
        if isinstance(value, CombatLogEvent):
            if value.timestamp > 0:
                return value
            return replace(value, timestamp=self._clock())

        if isinstance(value, CombatEvent):
            return CombatLogEvent(
                kind=value.kind or value.event_type,
                status=value.operational_status,
                target_name=value.target_name,
                target_type=value.target_type,
                timestamp=value.timestamp or self._clock(),
                target_id=value.target_id,
                attacker=value.attacker,
                weapon=value.weapon,
                weapon_class=value.weapon_class,
                damage_type=value.damage_type,
                zone=value.zone,
                from_level=value.from_level,
                to_level=value.to_level,
                raw=value.raw,
            )

        if not isinstance(value, Mapping):
            raise TypeError(
                "combat event must be CombatLogEvent, CombatEvent, or mapping")

        stamp = _num(_pick(value, "timestamp", "time"), 0.0)
        if stamp <= 0:
            iso = _pick(value, "timestamp_iso", "iso_timestamp")
            if iso:
                try:
                    stamp = datetime.fromisoformat(
                        str(iso).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    stamp = 0.0
        if stamp <= 0:
            stamp = self._clock()

        return CombatLogEvent(
            kind=str(_pick(value, "kind", "event_type", default="manual")),
            status=str(_pick(
                value, "operational_status", "status", default=STATUS_ACTIVE)),
            target_name=_clean_name(_pick(
                value, "target_name", "target", "name", default="")),
            target_type=_clean_name(_pick(
                value, "target_type", "entity_type", default="Vehicle")),
            timestamp=stamp,
            target_id=str(_pick(value, "target_id", "id", default="") or ""),
            attacker=_clean_name(value.get("attacker")),
            weapon=_clean_name(value.get("weapon")),
            weapon_class=_clean_name(value.get("weapon_class")),
            damage_type=_clean_name(value.get("damage_type")),
            zone=_clean_name(value.get("zone")),
            from_level=_optional_int(value.get("from_level")),
            to_level=_optional_int(value.get("to_level")),
            is_npc_target=bool(value.get("is_npc_target", False)),
            is_npc_attacker=bool(value.get("is_npc_attacker", False)),
            raw=str(value.get("raw") or ""),
        )

    def _resolve_target_key(self, target_id: str, target_name: str,
                            target_type: str) -> str:
        """Stable target identity across id-bearing and name-only log lines.

        Destruction lines usually carry a numeric vehicle/actor id while a
        generic damage or impact line only carries a name.  If exactly one
        existing target matches the name, reuse its key so the same ship does
        not appear twice in the session totals.
        """
        identifier = _clean_name(target_id)
        name = _clean_name(target_name)
        type_key = (target_type or "Vehicle").casefold()
        name_key = (f"{type_key}:{name.casefold()}" if name else "")

        if identifier:
            id_key = f"id:{identifier.casefold()}"
            if id_key in self._targets:
                return id_key
            if name_key and name_key in self._targets:
                old = self._targets.pop(name_key)
                self._targets[id_key] = replace(old, key=id_key)
                for bucket in (self._neutralized, self._soft_dead,
                               self._destroyed):
                    if name_key in bucket:
                        bucket.remove(name_key)
                        bucket.add(id_key)
                return id_key
            return id_key

        if name_key and name_key in self._targets:
            return name_key
        if name:
            matches = [
                key for key, state in self._targets.items()
                if (state.target_type.casefold() == type_key
                    and state.target_name.casefold() == name.casefold())
            ]
            if len(matches) == 1:
                return matches[0]
        return name_key

    def _record_engagement_locked(self, stamp: float) -> None:
        if self._last_engagement_at is not None:
            delta = max(0.0, stamp - self._last_engagement_at)
            self._last_engagement_delta = delta
            self._engagement_deltas.append(delta)
        self._last_engagement_at = stamp
        gap = max(0.0, stamp - self._survival_mark)
        self._longest_survival_seconds = max(
            self._longest_survival_seconds, gap)
        self._survival_mark = stamp

    def _reset_state_locked(self, now: float) -> None:
        self._session_started_at = float(now)
        self._targets: dict[str, CombatTargetState] = {}
        self._neutralized: set[str] = set()
        self._soft_dead: set[str] = set()
        self._destroyed: set[str] = set()
        self._total_events = 0
        self._damage_events = 0
        self._weapon_impacts = 0
        self._kills = 0
        self._player_kills = 0
        self._deaths = 0
        self._engagement_deltas: list[float] = []
        self._last_engagement_at: float | None = None
        self._last_engagement_delta: float | None = None
        self._last_event_at: float | None = None
        self._last_damage_at: float | None = None
        self._longest_survival_seconds = 0.0
        self._survival_mark = float(now)

    def _snapshot_locked(self, now: float) -> CombatSessionStats:
        uptime = max(0.0, now - self._session_started_at)
        current_survival = max(0.0, now - self._survival_mark)
        longest = max(self._longest_survival_seconds, current_survival)
        deltas = self._engagement_deltas
        average = (sum(deltas) / len(deltas)) if deltas else None
        return CombatSessionStats(
            session_started_at=self._session_started_at,
            updated_at=now,
            targets_seen=len(self._targets),
            active_targets=sum(
                1 for target in self._targets.values()
                if target.status == STATUS_ACTIVE),
            targets_neutralized=len(self._neutralized),
            soft_dead_targets=len(self._soft_dead),
            destroyed_targets=len(self._destroyed),
            total_events=self._total_events,
            damage_events=self._damage_events,
            weapon_impacts=self._weapon_impacts,
            kills=self._kills,
            player_kills=self._player_kills,
            deaths=self._deaths,
            engagement_count=len(self._neutralized),
            last_engagement_at=self._last_engagement_at,
            last_engagement_delta=self._last_engagement_delta,
            average_engagement_delta=average,
            uptime_seconds=uptime,
            current_survival_seconds=current_survival,
            longest_survival_seconds=longest,
            last_event_at=self._last_event_at,
            time_since_last_event=(
                max(0.0, now - self._last_event_at)
                if self._last_event_at is not None else None),
            last_damage_at=self._last_damage_at,
            time_since_last_damage=(
                max(0.0, now - self._last_damage_at)
                if self._last_damage_at is not None else None),
            error=self._error,
        )

    @staticmethod
    def _make_event(
            *,
            event_type: str,
            kind: str,
            status: str,
            target_name: str,
            target_type: str,
            timestamp: float,
            previous_status: str = "",
            target_id: str = "",
            attacker: str = "",
            weapon: str = "",
            weapon_class: str = "",
            damage_type: str = "",
            zone: str = "",
            from_level: int | None = None,
            to_level: int | None = None,
            raw: str = "",
            detail: Mapping[str, object] | None = None) -> CombatEvent:
        return CombatEvent(
            event_type=event_type,
            kind=kind,
            timestamp=timestamp,
            operational_status=status,
            target_name=target_name,
            target_type=target_type,
            previous_status=previous_status,
            target_id=target_id,
            attacker=attacker,
            weapon=weapon,
            weapon_class=weapon_class,
            damage_type=damage_type,
            zone=zone,
            from_level=from_level,
            to_level=to_level,
            raw=raw,
            detail=dict(detail or {}),
        )

    def _emit_locked(self, event: CombatEvent) -> None:
        self._last_event = event
        self._bus.emit(event)
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            try:
                self._event_queue.get_nowait()
                self._event_queue.task_done()
            except (queue.Empty, ValueError):
                pass
            try:
                self._event_queue.put_nowait(event)
            except queue.Full:
                pass
