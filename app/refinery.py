"""Headless refinery work-order tracking engine.

The module has three deliberately separate concerns:

* :class:`RefineryJobStore` persists immutable jobs to ``refinery_jobs.json``;
* :class:`RefineryTimerEngine` owns the one-second countdown thread;
* :class:`RefineryLogWatcher` (in :mod:`app.refinery_log`) owns the read-only
  ``Game.log`` tail loop.

:class:`RefineryTracker` is the small facade a host application talks to.  It
exposes callbacks, a thread queue, a boolean active flag and immutable job
snapshots.  It contains no Qt imports and creates no windows.

Typical host usage::

    tracker = RefineryTracker()
    tracker.subscribe(on_event)
    tracker.start()
    ...
    tracker.close()

The host should subscribe before calling ``start`` so the initial state event
for jobs restored from disk is observed.
"""
from __future__ import annotations

import json
import logging
import math
import os
import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .config import config_dir
from .refinery_audio import RefineryAudio
from .refinery_log import (RefineryLogEvent, RefineryLogWatcher,
                           parse_duration)
from .stations import clean_terminal_name, system_for_station

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
CACHE_NAME = "refinery_jobs.json"

__all__ = [
    "CACHE_NAME",
    "RefineryAudio",
    "RefineryEvent",
    "RefineryEventBus",
    "RefineryJob",
    "RefineryJobStore",
    "RefineryState",
    "RefineryTimerEngine",
    "RefineryTracker",
    "format_countdown",
    "parse_duration",
]


def _num(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _pick(mapping: Mapping[str, object], *keys: str, default=None):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


def _status(value: object) -> str:
    text = str(value or "active").strip().casefold()
    if text in {"complete", "completed", "done", "finished", "collected"}:
        return "complete"
    if text in {"cancelled", "canceled", "failed", "aborted"}:
        return "cancelled"
    return "active"


def format_countdown(seconds: float | int) -> str:
    """Format seconds as ``Dd HH:MM:SS`` or ``HH:MM:SS``."""
    total = max(0, int(seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RefineryJob:
    """One immutable refinery work order.

    ``ends_at`` is stored as a UTC epoch timestamp so multi-day timers survive
    restarts without relying on a monotonic clock.
    """

    job_id: str
    system: str
    station: str
    processing_method: str
    start_timestamp: float
    duration_seconds: int
    ends_at: float
    commodity: str = ""
    quantity_scu: int = 0
    status: str = "active"
    completed_at: float = 0.0
    alerted: bool = False
    source: str = "game_log"
    raw: str = ""

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.status == "active"

    def is_active(self, now: float | None = None) -> bool:
        stamp = time.time() if now is None else float(now)
        return self.status == "active" and self.ends_at > stamp

    def remaining_seconds(self, now: float | None = None) -> int:
        if self.status != "active":
            return 0
        stamp = time.time() if now is None else float(now)
        return max(0, int(math.ceil(self.ends_at - stamp)))

    def countdown_text(self, now: float | None = None) -> str:
        return format_countdown(self.remaining_seconds(now))

    def to_dict(self, now: float | None = None) -> dict:
        data = {
            "job_id": self.job_id,
            "system": self.system,
            "station": self.station,
            "processing_method": self.processing_method,
            "start_timestamp": self.start_timestamp,
            "duration_seconds": self.duration_seconds,
            "ends_at": self.ends_at,
            "commodity": self.commodity,
            "quantity_scu": self.quantity_scu,
            "status": self.status,
            "completed_at": self.completed_at,
            "alerted": self.alerted,
            "source": self.source,
            "raw": self.raw,
        }
        if now is not None:
            data["remaining_seconds"] = self.remaining_seconds(now)
            data["countdown"] = self.countdown_text(now)
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RefineryJob | None":
        start = _num(_pick(
            value, "start_timestamp", "started_at", "start", default=0))
        duration = _int(_pick(
            value, "duration_seconds", "duration", "countdown_seconds",
            default=0))
        ends = _num(_pick(
            value, "ends_at", "end_timestamp", "end", default=0))
        if ends <= 0 and start > 0 and duration > 0:
            ends = start + duration
        if start <= 0 and ends > 0 and duration > 0:
            start = ends - duration
        if duration <= 0 and start > 0 and ends > start:
            duration = int(round(ends - start))
        if start <= 0 or duration <= 0 or ends <= 0:
            return None

        station = clean_terminal_name(str(_pick(
            value, "station", "location", default="") or ""))
        station = station or str(_pick(
            value, "station", "location", default="Unknown") or "Unknown")
        system = str(_pick(value, "system", "star_system", default="") or "")
        if not system or system.casefold() == "unknown":
            system = system_for_station(station) or "Unknown"
        status = _status(value.get("status"))
        completed = _num(value.get("completed_at"))
        if status == "complete" and completed <= 0:
            completed = ends
        return cls(
            job_id=str(_pick(value, "job_id", "id", default="")
                       or uuid.uuid4().hex),
            system=system,
            station=station,
            processing_method=str(_pick(
                value, "processing_method", "method", default="Unknown")
                or "Unknown"),
            start_timestamp=start,
            duration_seconds=max(1, duration),
            ends_at=max(start + 1, ends),
            commodity=str(value.get("commodity") or ""),
            quantity_scu=max(0, _int(value.get("quantity_scu"))),
            status=status,
            completed_at=max(0.0, completed),
            alerted=bool(value.get("alerted", False)),
            source=str(value.get("source") or "game_log"),
            raw=str(value.get("raw") or ""),
        )


@dataclass(frozen=True)
class RefineryState:
    """Immutable snapshot exposed to the host UI."""

    is_refinery_order_active: bool
    jobs: tuple[RefineryJob, ...]
    active_jobs: tuple[RefineryJob, ...]
    updated_at: float
    error: str = ""

    @property
    def active_count(self) -> int:
        return len(self.active_jobs)

    def countdowns(self) -> dict[str, int]:
        return {job.job_id: job.remaining_seconds(self.updated_at)
                for job in self.active_jobs}

    def to_dict(self) -> dict:
        return {
            "is_refinery_order_active": self.is_refinery_order_active,
            "active_count": self.active_count,
            "jobs": [job.to_dict(now=self.updated_at) for job in self.jobs],
            "countdowns": self.countdowns(),
            "updated_at": self.updated_at,
            "error": self.error,
        }


@dataclass(frozen=True)
class RefineryEvent:
    """Asynchronous payload delivered by :class:`RefineryEventBus`."""

    event_type: str
    is_refinery_order_active: bool
    jobs: tuple[RefineryJob, ...]
    timestamp: float
    job: RefineryJob | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "is_refinery_order_active": self.is_refinery_order_active,
            "jobs": [job.to_dict(now=self.timestamp) for job in self.jobs],
            "job": (self.job.to_dict(now=self.timestamp)
                    if self.job else None),
            "timestamp": self.timestamp,
            "detail": dict(self.detail),
        }


class RefineryEventBus:
    """Queue-backed listener dispatcher; listeners never block the timer."""

    _SENTINEL = object()

    def __init__(self):
        self._listeners: list[Callable[[RefineryEvent], None]] = []
        self._lock = threading.RLock()
        self._queue: queue.Queue = queue.Queue()
        self._history: deque[RefineryEvent] = deque(maxlen=256)
        self._closed = False
        self._thread = threading.Thread(
            target=self._dispatch, name="sc-refinery-events", daemon=True)
        self._thread.start()

    def subscribe(self, listener: Callable[[RefineryEvent], None]):
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            self._listeners.append(listener)
        return lambda: self.unsubscribe(listener)

    def unsubscribe(self, listener: Callable[[RefineryEvent], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def emit(self, event: RefineryEvent) -> None:
        with self._lock:
            if self._closed:
                return
            self._history.append(event)
        self._queue.put(event)

    @property
    def history(self) -> tuple[RefineryEvent, ...]:
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
                        logger.exception("refinery event listener failed")
            finally:
                self._queue.task_done()


# ---------------------------------------------------------------------------
class RefineryJobStore:
    """Thread-safe JSON registry with atomic replacement writes."""

    def __init__(self, path: str | Path | None = None):
        self.path = (Path(path).expanduser() if path is not None
                     else config_dir() / CACHE_NAME)
        self._lock = threading.RLock()
        self._jobs: list[RefineryJob] = []
        self.error = ""
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            self._jobs = []
            self.error = ""
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except FileNotFoundError:
                return
            except (OSError, ValueError) as exc:
                self.error = f"Could not read refinery jobs: {exc}"
                return

            rows = payload.get("jobs") if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                self.error = "refinery_jobs.json has no jobs list"
                return

            seen: set[str] = set()
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                job = RefineryJob.from_dict(row)
                if job is None or job.job_id in seen:
                    continue
                seen.add(job.job_id)
                self._jobs.append(job)
            self._sort_locked()

    def save(self) -> bool:
        with self._lock:
            stamp = time.time()
            payload = {
                "schema": SCHEMA_VERSION,
                "updated_at": stamp,
                "jobs": [
                    {
                        **job.to_dict(),
                        "remaining_seconds": job.remaining_seconds(stamp),
                        "countdown": job.countdown_text(stamp),
                    }
                    for job in self._jobs
                ],
            }
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False,
                              separators=(",", ":"))
                os.replace(temporary, self.path)
                self.error = ""
                return True
            except OSError as exc:
                self.error = f"Could not save refinery jobs: {exc}"
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                return False

    # ------------------------------------------------------------------
    def all(self) -> tuple[RefineryJob, ...]:
        with self._lock:
            return tuple(self._jobs)

    def active(self, now: float | None = None) -> tuple[RefineryJob, ...]:
        stamp = time.time() if now is None else float(now)
        with self._lock:
            return tuple(job for job in self._jobs
                         if job.status == "active" and job.ends_at > stamp)

    def has_active(self, now: float | None = None) -> bool:
        return bool(self.active(now))

    def get(self, job_id: str) -> RefineryJob | None:
        with self._lock:
            return next((job for job in self._jobs
                         if job.job_id == job_id), None)

    # ------------------------------------------------------------------
    def upsert(self, job: RefineryJob) -> tuple[RefineryJob, bool]:
        """Insert a job unless the same log observation is already present."""
        with self._lock:
            existing = next((item for item in self._jobs
                             if item.job_id == job.job_id), None)
            if existing is not None:
                return existing, False

            # Repeated log lines can create fresh UUIDs.  The start time,
            # station, method and duration form a stable enough fingerprint.
            for item in self._jobs:
                if (item.system.casefold() == job.system.casefold()
                        and item.station.casefold() == job.station.casefold()
                        and (item.processing_method.casefold()
                             == job.processing_method.casefold())
                        and item.duration_seconds == job.duration_seconds
                        and abs(item.start_timestamp
                                - job.start_timestamp) <= 2.0):
                    return item, False

            self._jobs.append(job)
            self._sort_locked()
            self.save()
            return job, True

    def remove(self, job_id: str) -> bool:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [job for job in self._jobs
                          if job.job_id != job_id]
            changed = len(self._jobs) != before
            if changed:
                self.save()
            return changed

    def complete_due(self, now: float | None = None) -> tuple[RefineryJob, ...]:
        """Mark timers that reached zero and return their new snapshots."""
        stamp = time.time() if now is None else float(now)
        completed: list[RefineryJob] = []
        with self._lock:
            updated: list[RefineryJob] = []
            for job in self._jobs:
                if job.status == "active" and job.ends_at <= stamp:
                    job = replace(
                        job,
                        status="complete",
                        completed_at=job.ends_at,
                    )
                    completed.append(job)
                updated.append(job)
            if completed:
                self._jobs = updated
                self.save()
        return tuple(completed)

    def complete(self, job_id: str,
                 now: float | None = None) -> RefineryJob | None:
        stamp = time.time() if now is None else float(now)
        with self._lock:
            for index, job in enumerate(self._jobs):
                if job.job_id != job_id or job.status != "active":
                    continue
                job = replace(
                    job,
                    status="complete",
                    completed_at=stamp,
                    alerted=False,
                )
                self._jobs[index] = job
                self.save()
                return job
        return None

    def mark_alerted(self, job_ids: Iterable[str]) -> None:
        wanted = set(job_ids)
        if not wanted:
            return
        with self._lock:
            changed = False
            updated: list[RefineryJob] = []
            for job in self._jobs:
                if job.job_id in wanted and not job.alerted:
                    job = replace(job, alerted=True)
                    changed = True
                updated.append(job)
            if changed:
                self._jobs = updated
                self.save()

    def clear_completed(self) -> int:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [job for job in self._jobs
                          if job.status != "complete"]
            removed = before - len(self._jobs)
            if removed:
                self.save()
            return removed

    def prune_completed(self, older_than_seconds: float = 30 * 86400,
                        now: float | None = None) -> int:
        stamp = time.time() if now is None else float(now)
        cutoff = stamp - max(0.0, float(older_than_seconds))
        with self._lock:
            before = len(self._jobs)
            self._jobs = [
                job for job in self._jobs
                if job.status != "complete"
                or job.completed_at <= 0
                or job.completed_at >= cutoff
            ]
            removed = before - len(self._jobs)
            if removed:
                self.save()
            return removed

    def snapshot(self, now: float | None = None) -> RefineryState:
        stamp = time.time() if now is None else float(now)
        with self._lock:
            jobs = tuple(self._jobs)
            active = tuple(job for job in jobs
                           if job.status == "active" and job.ends_at > stamp)
            return RefineryState(
                is_refinery_order_active=bool(active),
                jobs=jobs,
                active_jobs=active,
                updated_at=stamp,
                error=self.error,
            )

    def _sort_locked(self) -> None:
        self._jobs.sort(key=lambda job: (job.start_timestamp, job.job_id))


# ---------------------------------------------------------------------------
class RefineryTimerEngine:
    """One-second countdown loop, separate from disk and log concerns."""

    def __init__(
            self,
            store: RefineryJobStore,
            callback: Callable[[tuple[RefineryJob, ...],
                                tuple[RefineryJob, ...], float], None],
            tick_seconds: float = 1.0,
            clock: Callable[[], float] | None = None):
        self._store = store
        self._callback = callback
        self._tick_seconds = max(0.05, float(tick_seconds))
        self._clock = clock or time.time
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self.last_tick = 0.0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop.is_set())

    def start(self) -> "RefineryTimerEngine":
        with self._lock:
            if self.running:
                return self
            previous = self._thread
        if (previous is not None and previous.is_alive()
                and previous is not threading.current_thread()):
            previous.join(1.0)
        with self._lock:
            if self.running:
                return self
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="sc-refinery-timer", daemon=True)
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

    def run_once(
            self,
            now: float | None = None) -> tuple[tuple[RefineryJob, ...],
                                               tuple[RefineryJob, ...]]:
        stamp = self._clock() if now is None else float(now)
        completed = self._store.complete_due(stamp)
        active = self._store.active(stamp)
        self.last_tick = stamp
        try:
            self._callback(tuple(completed), tuple(active), stamp)
        except Exception:
            logger.exception("refinery timer callback failed")
        return tuple(completed), tuple(active)

    def _run(self) -> None:
        current = threading.current_thread()
        try:
            while not self._stop.is_set():
                try:
                    self.run_once()
                except Exception:
                    logger.exception("refinery timer tick failed")
                if self._stop.wait(self._tick_seconds):
                    break
        finally:
            with self._lock:
                if self._thread is current:
                    self._thread = None


# ---------------------------------------------------------------------------
class RefineryTracker:
    """Facade joining the log watcher, JSON registry, timer and audio alert."""

    def __init__(
            self,
            *,
            state_path: str | Path | None = None,
            state_dir: str | Path | None = None,
            game_log_path: str | Path | None = None,
            audio: RefineryAudio | None = None,
            log_watcher_factory: (
                Callable[[Callable[[RefineryLogEvent], None]],
                         RefineryLogWatcher] | None) = None,
            event_bus: RefineryEventBus | None = None,
            tick_seconds: float = 1.0,
            clock: Callable[[], float] | None = None):
        if state_path is None:
            directory = (Path(state_dir).expanduser() if state_dir is not None
                         else config_dir())
            state_path = directory / CACHE_NAME
        self._store = RefineryJobStore(state_path)
        self._game_log_path = (Path(game_log_path).expanduser()
                               if game_log_path else None)
        self._audio = audio if audio is not None else RefineryAudio()
        self._bus = event_bus or RefineryEventBus()
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._active = self._store.has_active(self._clock())
        self._started = False
        self._closed = False
        self._watcher: RefineryLogWatcher | None = None
        self._timer = RefineryTimerEngine(
            self._store, self._on_timer_tick,
            tick_seconds=tick_seconds, clock=self._clock)
        self._state_queue: queue.Queue = queue.Queue(maxsize=64)
        self._last_event: RefineryEvent | None = None
        if log_watcher_factory is not None:
            self._watcher_factory = log_watcher_factory
        else:
            self._watcher_factory = lambda callback: RefineryLogWatcher(
                path=self._game_log_path, callback=callback)

    # -- lifecycle ------------------------------------------------------
    def __enter__(self) -> "RefineryTracker":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> "RefineryTracker":
        """Start watching and counting; emits the initial restored state."""
        with self._lock:
            if self._closed:
                raise RuntimeError("refinery tracker is closed")
            if self._started:
                return self
            self._started = True
            # Emit True immediately when persisted active work exists.
            self._refresh_active_locked(
                force=True, now=self._clock(), active_jobs=None)
            watcher = self._watcher_factory(self._on_log_event)
            self._watcher = watcher
        watcher.start()
        self._timer.start()
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
        self._timer.stop(wait=wait)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.stop(wait=True)
        self._audio.close()
        self._bus.close()

    # -- subscriptions and snapshots ------------------------------------
    def subscribe(self, listener: Callable[[RefineryEvent], None]):
        return self._bus.subscribe(listener)

    def unsubscribe(self, listener: Callable[[RefineryEvent], None]) -> None:
        self._bus.unsubscribe(listener)

    @property
    def event_bus(self) -> RefineryEventBus:
        return self._bus

    @property
    def state_queue(self) -> queue.Queue:
        """Queue receiving only ``is_refinery_order_active`` booleans."""
        return self._state_queue

    @property
    def is_refinery_order_active(self) -> bool:
        with self._lock:
            return self._store.has_active(self._clock())

    @property
    def state(self) -> RefineryState:
        return self._store.snapshot(self._clock())

    @property
    def jobs(self) -> tuple[RefineryJob, ...]:
        return self._store.all()

    @property
    def active_jobs(self) -> tuple[RefineryJob, ...]:
        return self._store.active(self._clock())

    @property
    def countdowns(self) -> dict[str, int]:
        now = self._clock()
        return {job.job_id: job.remaining_seconds(now)
                for job in self._store.active(now)}

    @property
    def last_event(self) -> RefineryEvent | None:
        with self._lock:
            return self._last_event

    @property
    def error(self) -> str:
        return self._store.error

    @property
    def state_path(self) -> Path:
        return self._store.path

    @property
    def game_log_path(self) -> Path | None:
        return self._game_log_path

    @property
    def audio(self) -> RefineryAudio:
        return self._audio

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._started and not self._closed)

    def tick(self, now: float | None = None) -> tuple[RefineryJob, ...]:
        """Run one deterministic countdown pass (also useful in tests)."""
        completed, _ = self._timer.run_once(now=now)
        return tuple(completed)

    # -- manual operations ----------------------------------------------
    def add_job(
            self,
            *,
            station: str,
            processing_method: str = "Unknown",
            duration_seconds: int,
            system: str = "",
            started_at: float | None = None,
            job_id: str | None = None,
            commodity: str = "",
            quantity_scu: int = 0,
            source: str = "manual") -> RefineryJob:
        """Add a work order manually when a log patch omits useful fields."""
        stamp = self._clock() if started_at is None else float(started_at)
        duration = max(1, int(duration_seconds))
        clean_station = clean_terminal_name(station) or station or "Unknown"
        clean_system = (system or "").strip()
        if not clean_system or clean_system.casefold() == "unknown":
            clean_system = system_for_station(clean_station) or "Unknown"
        job = RefineryJob(
            job_id=job_id or uuid.uuid4().hex,
            system=clean_system,
            station=clean_station,
            processing_method=processing_method or "Unknown",
            start_timestamp=stamp,
            duration_seconds=duration,
            ends_at=stamp + duration,
            commodity=commodity,
            quantity_scu=max(0, int(quantity_scu)),
            status="active",
            source=source,
        )
        with self._lock:
            stored, created = self._store.upsert(job)
            if created:
                self._refresh_active_locked(now=self._clock())
                self._emit_locked("job_started", job=stored)
            return stored

    def remove_job(self, job_id: str) -> bool:
        with self._lock:
            removed = self._store.remove(job_id)
            if removed:
                self._refresh_active_locked(now=self._clock())
                self._emit_locked("jobs_changed")
            return removed

    def complete_job(self, job_id: str,
                     now: float | None = None) -> RefineryJob | None:
        stamp = self._clock() if now is None else float(now)
        with self._lock:
            job = self._store.complete(job_id, stamp)
            if job is None:
                return None
            notify = not job.alerted
            if notify:
                self._store.mark_alerted((job.job_id,))
            self._refresh_active_locked(now=stamp)
            self._emit_locked("job_complete", job=job, now=stamp)
            if notify:
                try:
                    self._audio.notify(job)
                except Exception:
                    logger.exception("refinery audio notification failed")
            return job

    def clear_completed(self) -> int:
        with self._lock:
            removed = self._store.clear_completed()
            if removed:
                self._emit_locked("jobs_changed")
            return removed

    # -- internals ------------------------------------------------------
    def _on_log_event(self, event: RefineryLogEvent) -> None:
        now = self._clock()
        station = clean_terminal_name(event.station) or event.station or "Unknown"
        system = (event.system or "").strip()
        if not system or system.casefold() == "unknown":
            system = system_for_station(station) or "Unknown"
        duration = max(1, int(event.duration_seconds))
        stamp = event.started_at or event.timestamp or now
        job = RefineryJob(
            job_id=event.job_id or uuid.uuid4().hex,
            system=system,
            station=station,
            processing_method=event.processing_method or "Unknown",
            start_timestamp=stamp,
            duration_seconds=duration,
            ends_at=stamp + duration,
            commodity=event.commodity,
            quantity_scu=max(0, int(event.quantity_scu)),
            status="active",
            source="game_log",
            raw=event.raw,
        )

        with self._lock:
            if self._closed:
                return
            existing = self._store.get(job.job_id)
            if existing is not None and existing.status != "active":
                # A patch reused an order id for a new job.
                job = replace(job, job_id=uuid.uuid4().hex)
            stored, created = self._store.upsert(job)
            if not created:
                return
            self._refresh_active_locked(now=now)
            self._emit_locked("job_started", job=stored, now=now)

    def _on_timer_tick(
            self,
            completed: tuple[RefineryJob, ...],
            active_jobs: tuple[RefineryJob, ...],
            now: float) -> None:
        with self._lock:
            if self._closed:
                return

            # Mark before notifying so a crash cannot produce a second spoken
            # alert for the same order on the next application start.
            alert_ids = [job.job_id for job in completed
                         if not job.alerted]
            if alert_ids:
                self._store.mark_alerted(alert_ids)
            for job in completed:
                if not job.alerted:
                    try:
                        self._audio.notify(job)
                    except Exception:
                        logger.exception("refinery audio notification failed")

            self._refresh_active_locked(
                now=now, active_jobs=active_jobs)

            for job in completed:
                self._emit_locked("job_complete", job=job, now=now)

            if active_jobs:
                self._emit_locked(
                    "countdown",
                    jobs=active_jobs,
                    now=now,
                    detail={
                        "countdowns": {
                            job.job_id: job.remaining_seconds(now)
                            for job in active_jobs
                        },
                    },
                )

    def _refresh_active_locked(
            self,
            *,
            force: bool = False,
            now: float | None = None,
            active_jobs: tuple[RefineryJob, ...] | None = None) -> bool:
        stamp = self._clock() if now is None else float(now)
        if active_jobs is None:
            active_jobs = self._store.active(stamp)
        new_active = bool(active_jobs)
        if force or new_active != self._active:
            self._active = new_active
            self._emit_locked(
                "state_changed",
                jobs=active_jobs,
                now=stamp,
                detail={"is_refinery_order_active": new_active},
            )
            try:
                self._state_queue.put_nowait(new_active)
            except queue.Full:
                try:
                    self._state_queue.get_nowait()
                    self._state_queue.task_done()
                except (queue.Empty, ValueError):
                    pass
                try:
                    self._state_queue.put_nowait(new_active)
                except queue.Full:
                    pass
        return self._active

    def _emit_locked(
            self,
            event_type: str,
            *,
            job: RefineryJob | None = None,
            jobs: Iterable[RefineryJob] | None = None,
            now: float | None = None,
            detail: Mapping[str, object] | None = None) -> RefineryEvent:
        stamp = self._clock() if now is None else float(now)
        if jobs is None:
            jobs = self._store.active(stamp)
        event = RefineryEvent(
            event_type=event_type,
            is_refinery_order_active=self._active,
            jobs=tuple(jobs),
            timestamp=stamp,
            job=job,
            detail=dict(detail or {}),
        )
        self._last_event = event
        self._bus.emit(event)
        return event
