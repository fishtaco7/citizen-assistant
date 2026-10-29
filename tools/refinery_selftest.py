"""Offline checks for the headless refinery work-order tracker.

    python -m tools.refinery_selftest

No game, network, Qt event loop, real Game.log, or audible output is required.
"""
from __future__ import annotations

import json
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.refinery import (  # noqa: E402
    RefineryAudio, RefineryJob, RefineryJobStore, RefineryTracker,
    format_countdown,
)
from app.refinery_audio import DEFAULT_PHRASE  # noqa: E402
from app.refinery_log import (  # noqa: E402
    DEFAULT_LOG_PATH, RefineryLogEvent, RefineryLogParser, RefineryLogWatcher,
    parse_duration,
)


class FakeWatcher:
    instances: list["FakeWatcher"] = []

    def __init__(self, callback):
        self.callback = callback
        self.started = False
        self.stopped = False
        FakeWatcher.instances.append(self)

    def start(self):
        self.started = True
        return self

    def stop(self, wait=True, timeout=2.0):
        self.stopped = True


class FakeAudio:
    def __init__(self):
        self.notifications = []
        self.closed = False

    def notify(self, job=None):
        self.notifications.append(job)
        return True

    def close(self):
        self.closed = True


class Recorder:
    def __init__(self):
        self.events = []
        self.condition = threading.Condition()

    def __call__(self, event):
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def wait(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for event in self.events:
                    if predicate(event):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError("refinery event timeout")
                self.condition.wait(remaining)


def make_job(*, job_id: str = "job-1", start: float = 1000.0,
             duration: int = 60) -> RefineryJob:
    return RefineryJob(
        job_id=job_id,
        system="Stanton",
        station="HUR-L1 Green Glade Station",
        processing_method="Cormack",
        start_timestamp=start,
        duration_seconds=duration,
        ends_at=start + duration,
    )


def check_duration_and_countdown() -> None:
    assert parse_duration("1h 30m") == 5400
    assert parse_duration("01:30:00") == 5400
    assert parse_duration("2d 4h") == 187200
    assert parse_duration("3600") == 3600
    assert parse_duration("00:05") == 5
    assert parse_duration("") == 0
    assert format_countdown(0) == "00:00:00"
    assert format_countdown(90061) == "1d 01:01:01"


def check_parser() -> None:
    parser = RefineryLogParser()
    line = (
        '[Notice] Refinery work order submitted: system=Stanton '
        'station="HUR-L1 Green Glade Station" method=Cormack '
        'duration=1h 30m commodity=Gold quantity=32 job_id=abc'
    )
    events = parser.parse_line(line, now=1000.0)
    assert len(events) == 1
    event = events[0]
    assert event.kind == "job_started"
    assert event.system == "Stanton"
    assert event.station == "HUR-L1 Green Glade Station"
    assert event.processing_method == "Cormack"
    assert event.duration_seconds == 5400
    assert event.started_at == 1000.0
    assert event.job_id == "abc"
    assert event.commodity == "Gold"
    assert event.quantity_scu == 32

    events = parser.parse_line(
        "[Notice] CRefineryWorkOrder::SubmitWorkOrder "
        "location=Area18 duration=3600 method=Dinyx", now=2000.0)
    assert len(events) == 1
    assert events[0].station == "Area 18"
    assert events[0].system == "Stanton"
    assert events[0].processing_method == "Dinyx"
    assert events[0].duration_seconds == 3600

    events = parser.parse_line(
        "[Notice] Refinery terminal submit Station Name=Area18 "
        "Processing Method=Dinyx Time Remaining=1h", now=2500.0)
    assert len(events) == 1
    assert events[0].station == "Area 18"
    assert events[0].processing_method == "Dinyx"
    assert events[0].duration_seconds == 3600

    events = parser.parse_line(
        "[Notice] refinery transaction started at Port Tressler "
        'duration="02:00:00"', now=2600.0)
    assert len(events) == 1
    assert events[0].station == "Port Tressler"
    assert events[0].duration_seconds == 7200

    # Completion notices must not start a second countdown.
    assert parser.parse_line(
        "[Notice] refinery work order complete station=Area18",
        now=3000.0) == []
    # A trigger without a usable duration cannot create a timer.
    assert parser.parse_line(
        "[Notice] refinery work order submitted station=Area18",
        now=3000.0) == []


def check_store(tmp: Path) -> None:
    path = tmp / "refinery_jobs.json"
    store = RefineryJobStore(path)
    assert not path.exists()
    assert store.all() == ()

    job = make_job()
    stored, created = store.upsert(job)
    assert created and stored.job_id == job.job_id
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["jobs"][0]["processing_method"] == "Cormack"
    assert payload["jobs"][0]["start_timestamp"] == 1000.0
    assert payload["jobs"][0]["duration_seconds"] == 60
    assert isinstance(payload["jobs"][0]["remaining_seconds"], int)

    # Repeated log observations do not create a second job.
    duplicate, created = store.upsert(make_job(job_id="job-2"))
    assert not created and duplicate.job_id == job.job_id

    active = store.active(now=1010.0)
    assert len(active) == 1
    assert active[0].remaining_seconds(1010.0) == 50
    assert active[0].countdown_text(1010.0) == "00:00:50"
    assert store.has_active(now=1010.0)

    completed = store.complete_due(now=1070.0)
    assert len(completed) == 1
    assert completed[0].status == "complete"
    assert completed[0].alerted is False
    assert not store.has_active(now=1070.0)

    store.mark_alerted((job.job_id,))
    reloaded = RefineryJobStore(path)
    assert len(reloaded.all()) == 1
    assert reloaded.all()[0].alerted is True
    assert reloaded.all()[0].status == "complete"

    assert reloaded.clear_completed() == 1
    assert reloaded.all() == ()


def check_watcher(tmp: Path) -> None:
    log_path = tmp / "Game.log"
    log_path.write_text(
        "[Notice] Refinery work order submitted: "
        "station=HUR-L1 Green Glade Station method=Cormack "
        "duration=01:00:00\n",
        encoding="utf-8",
    )
    received = []
    ready = threading.Event()

    def callback(event):
        received.append(event)
        ready.set()

    watcher = RefineryLogWatcher(
        path=log_path, callback=callback, poll_interval=0.02,
        from_start=True)
    watcher.start()
    try:
        assert ready.wait(3.0), "refinery log watcher did not emit"
        assert received[0].station == "HUR-L1 Green Glade Station"
        assert received[0].duration_seconds == 3600
    finally:
        watcher.stop()


def check_tracker(tmp: Path) -> None:
    now = [1000.0]
    FakeWatcher.instances.clear()
    audio = FakeAudio()
    recorder = Recorder()
    tracker = RefineryTracker(
        state_path=tmp / "tracker_jobs.json",
        audio=audio,
        log_watcher_factory=FakeWatcher,
        tick_seconds=60.0,
        clock=lambda: now[0],
    )
    tracker.subscribe(recorder)
    tracker.start()
    try:
        initial = recorder.wait(lambda event: event.event_type == "state_changed")
        assert initial.is_refinery_order_active is False
        watcher = FakeWatcher.instances[-1]
        assert watcher.started

        event = RefineryLogEvent(
            kind="job_started",
            system="Stanton",
            station="HUR-L1 Green Glade Station",
            processing_method="Cormack",
            duration_seconds=10,
            started_at=now[0],
            timestamp=now[0],
            job_id="job-1",
        )
        watcher.callback(event)
        watcher.callback(event)       # deduplicate the repeated log line
        assert len(tracker.jobs) == 1
        assert tracker.is_refinery_order_active

        start_event = recorder.wait(
            lambda item: item.event_type == "job_started")
        assert start_event.job is not None
        assert start_event.job.job_id == "job-1"
        state_true = recorder.wait(
            lambda item: item.event_type == "state_changed"
            and item.is_refinery_order_active is True)
        assert state_true.is_refinery_order_active is True
        assert tracker.countdowns == {"job-1": 10}

        now[0] = 1005.0
        assert tracker.tick() == ()
        recorder.wait(lambda item: item.event_type == "countdown")
        assert tracker.countdowns == {"job-1": 5}

        now[0] = 1011.0
        completed = tracker.tick()
        assert len(completed) == 1
        assert completed[0].status == "complete"
        recorder.wait(lambda item: item.event_type == "job_complete")
        recorder.wait(
            lambda item: item.event_type == "state_changed"
            and item.is_refinery_order_active is False)
        assert tracker.is_refinery_order_active is False
        assert len(audio.notifications) == 1
        assert tracker.jobs[0].status == "complete"
        assert tracker.jobs[0].alerted is True

        values = []
        while True:
            try:
                values.append(tracker.state_queue.get_nowait())
                tracker.state_queue.task_done()
            except queue.Empty:
                break
        assert values == [False, True, False]

        # The same completed observation stays deduplicated.
        watcher.callback(event)
        assert len(tracker.jobs) == 1

        manual = tracker.add_job(
            station="Area 18", processing_method="Dinyx",
            duration_seconds=20, started_at=now[0])
        assert manual.system == "Stanton"
        assert tracker.is_refinery_order_active
        assert tracker.remove_job(manual.job_id)
        assert not tracker.is_refinery_order_active
    finally:
        tracker.close()
    assert watcher.stopped
    assert audio.closed


def check_restart(tmp: Path) -> None:
    path = tmp / "restart_jobs.json"
    store = RefineryJobStore(path)
    store.upsert(make_job(job_id="restored", start=1000.0, duration=1000))
    assert path.exists()

    FakeWatcher.instances.clear()
    recorder = Recorder()
    tracker = RefineryTracker(
        state_path=path,
        audio=FakeAudio(),
        log_watcher_factory=FakeWatcher,
        tick_seconds=60.0,
        clock=lambda: 1100.0,
    )
    tracker.subscribe(recorder)
    tracker.start()
    try:
        initial = recorder.wait(lambda event: event.event_type == "state_changed")
        assert initial.is_refinery_order_active is True
        assert tracker.is_refinery_order_active
        assert tracker.active_jobs[0].job_id == "restored"
    finally:
        tracker.close()

    # A job that expired while the app was closed is completed on startup.
    expired = tmp / "expired_jobs.json"
    expired_store = RefineryJobStore(expired)
    expired_store.upsert(
        make_job(job_id="expired", start=1000.0, duration=10))
    audio = FakeAudio()
    recorder = Recorder()
    tracker = RefineryTracker(
        state_path=expired,
        audio=audio,
        log_watcher_factory=FakeWatcher,
        tick_seconds=60.0,
        clock=lambda: 1100.0,
    )
    tracker.subscribe(recorder)
    tracker.start()
    try:
        recorder.wait(lambda event: event.event_type == "job_complete")
        assert audio.notifications
        assert not tracker.is_refinery_order_active
        assert RefineryJobStore(expired).all()[0].alerted is True
    finally:
        tracker.close()


def check_audio_guard() -> None:
    audio = RefineryAudio(enabled=False)
    try:
        assert audio.notify() is False
        assert audio.phrase == DEFAULT_PHRASE
    finally:
        audio.close()


def main() -> int:
    assert DEFAULT_LOG_PATH.name == "Game.log"
    assert "starcitizen" in str(DEFAULT_LOG_PATH)
    check_duration_and_countdown()
    check_parser()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        check_store(root)
        check_watcher(root)
        check_tracker(root)
        check_restart(root)
    check_audio_guard()
    print("ALL REFINERY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
