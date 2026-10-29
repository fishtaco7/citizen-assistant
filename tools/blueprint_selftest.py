"""Offline checks for the headless blueprint/inventory tracker.

    python -m tools.blueprint_selftest

No game, network, Qt event loop, or real Game.log is required.
"""
from __future__ import annotations

import json
import queue
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.blueprint_log import (  # noqa: E402
    KIND_BLUEPRINT,
    KIND_ITEM,
    KIND_LOOT,
    BlueprintLogEvent,
    BlueprintLogParser,
    BlueprintLogWatcher,
    parse_log_timestamp,
)
from app.blueprints import (  # noqa: E402
    BlueprintStore,
    BlueprintTracker,
    InventoryEvent,
)

STAMP = "2026-03-26T17:15:41.684Z"


def line(body: str, stamp: str = STAMP) -> str:
    return f"<{stamp}> {body}"


BLUEPRINT_LINE = line(
    '[Notice] <SHUDEvent_OnNotification> Added notification '
    '"Received Blueprint: Defiance Helmet Tactical: " [23] to queue. '
    'New queue size: 2, MissionId: [00000000-0000-0000-0000-000000000000], '
    'ObjectiveId: [] [Team_CoreGameplayFeatures][Missions][Comms]'
)

QUOTED_BLUEPRINT_LINE = line(
    '[Notice] <SHUDEvent_OnNotification> Added notification '
    '"Received Blueprint: Demeco "Purgatory Camo" LMG: " [24] to queue.'
)

ECHO_LINE = line(
    '[Notice] <UpdateNotificationItem> Notification '
    '"Received Blueprint: Coda Pistol: " [23], Action: Next'
)

BARE_CONTINUATION = line('   "Received Blueprint: Coda Pistol: " [23]')

ITEM_LINE = line(
    "CInventory::AddItem item_name='FS-9 LMG' quantity=1"
)

LOOT_LINE = line(
    "[Notice] Opened loot box 'Crate_123' and found item 'Coda Pistol'"
)

NOTIFICATION_ITEM_LINE = line(
    '[Notice] <SHUDEvent_OnNotification> Added notification '
    '"You found a Coda Pistol" [25] to queue.'
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


class Recorder:
    def __init__(self):
        self.events: list[InventoryEvent] = []
        self.condition = threading.Condition()

    def __call__(self, event: InventoryEvent) -> None:
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def wait(self, predicate, timeout=4.0) -> InventoryEvent:
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for event in self.events:
                    if predicate(event):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError("blueprint event timeout")
                self.condition.wait(remaining)


def event(
        kind: str,
        name: str,
        timestamp: float,
        *,
        code: str = "",
        quantity: int = 0,
        container: str = "") -> BlueprintLogEvent:
    # tz-aware conversion: plain fromtimestamp() uses localtime(), which raises
    # OSError on Windows for epochs that fall before 1970 in a negative UTC
    # offset (the test uses small synthetic timestamps).
    iso = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat()
    return BlueprintLogEvent(
        kind=kind,
        item_name=name,
        item_code=code or name,
        timestamp=timestamp,
        timestamp_iso=iso,
        quantity=quantity,
        container=container,
    )


def check_timestamp() -> None:
    expected = datetime.fromisoformat(
        STAMP.replace("Z", "+00:00")).timestamp()
    epoch, iso = parse_log_timestamp(f"<{STAMP}> hello")
    assert abs(epoch - expected) < 0.001
    assert iso.endswith("Z")
    expected_iso = datetime.fromtimestamp(
        123.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    assert parse_log_timestamp("no timestamp", now=123.0) == (
        123.0, expected_iso)


def check_parser() -> None:
    parser = BlueprintLogParser()

    events = parser.parse_line(BLUEPRINT_LINE, now=1.0)
    assert len(events) == 1
    blueprint = events[0]
    assert blueprint.kind == KIND_BLUEPRINT
    assert blueprint.item_name == "Defiance Helmet Tactical"
    assert blueprint.item_code == "Defiance Helmet Tactical"
    assert blueprint.timestamp_iso.endswith("Z")
    assert blueprint.timestamp > 1.0

    events = parser.parse_line(QUOTED_BLUEPRINT_LINE, now=2.0)
    assert len(events) == 1
    assert events[0].kind == KIND_BLUEPRINT
    assert events[0].item_name == 'Demeco "Purgatory Camo" LMG'

    # UI echo and bare continuation lines are not authoritative unlocks.
    assert parser.parse_line(ECHO_LINE, now=3.0) == []
    assert parser.parse_line(BARE_CONTINUATION, now=3.0) == []
    assert parser.parse_line(
        line("[Notice] <ReuseChannel> Reusing channel for "
             "'sc.external.services.blueprint_library.v1."
             "BlueprintLibraryService' to endpoint example.com:443"),
        now=3.0,
    ) == []

    events = parser.parse_line(
        line("CraftingBlueprint::Unlock blueprint_name='BP_CRAFT_AMRS_LaserRepeater_S2'"),
        now=4.0)
    assert len(events) == 1
    assert events[0].kind == KIND_BLUEPRINT
    assert events[0].item_name == "AMRS LaserRepeater S2"
    assert events[0].item_code == "BP_CRAFT_AMRS_LaserRepeater_S2"

    events = parser.parse_line(ITEM_LINE, now=5.0)
    assert len(events) == 1
    assert events[0].kind == KIND_ITEM
    assert events[0].item_name == "FS-9 LMG"
    assert events[0].quantity == 1

    events = parser.parse_line(LOOT_LINE, now=6.0)
    assert len(events) == 1
    assert events[0].kind == KIND_LOOT
    assert events[0].item_name == "Coda Pistol"
    assert events[0].container == "Crate_123"

    events = parser.parse_line(NOTIFICATION_ITEM_LINE, now=7.0)
    assert len(events) == 1
    assert events[0].kind == KIND_ITEM
    assert events[0].item_name == "Coda Pistol"

    assert parser.parse_line(
        line("CInventory::AddItem"), now=8.0) == []
    assert parser.parse_line(
        line("Inventory updated: 3 items"), now=8.0) == []

    # The canonical blueprint format remains available even when custom broad
    # patterns are disabled.
    custom = BlueprintLogParser(blueprint_patterns=(), item_patterns=())
    assert custom.parse_line(BLUEPRINT_LINE, now=9.0)
    assert custom.parse_line(ECHO_LINE, now=9.0) == []


def check_store(tmp: Path) -> None:
    path = tmp / "unlocked_blueprints.json"
    store = BlueprintStore(path)
    first = event(KIND_BLUEPRINT, "Coda Pistol", 100.0)
    record, is_new = store.record(first)
    assert record is not None and is_new
    assert record.checked_off
    assert store.save()
    assert path.is_file()

    loaded = BlueprintStore(path)
    assert loaded.loaded
    assert loaded.has("Coda Pistol")
    assert loaded.has("coda pistol")
    duplicate = event(KIND_BLUEPRINT, "Coda Pistol", 101.0)
    record, is_new = loaded.record(duplicate)
    assert record is not None and not is_new
    assert record.seen_count == 2
    assert loaded.save()

    # A later id-bearing line upgrades the name key without duplicating it.
    coded = event(KIND_BLUEPRINT, "Coda Pistol", 102.0,
                  code="BP_CRAFT_CODA_PISTOL")
    record, is_new = loaded.record(coded)
    assert record is not None and not is_new
    assert record.key == "bp_craft_coda_pistol"
    assert len(loaded.blueprints) == 1
    assert loaded.save()

    item = event(KIND_ITEM, "FS-9 LMG", 103.0, quantity=1)
    record, is_new = loaded.record(item)
    assert record is not None and is_new
    assert len(loaded.items) == 1
    assert loaded.save()

    snapshot = loaded.snapshot()
    assert snapshot["blueprints"][0]["item_name"] == "Coda Pistol"
    assert snapshot["items"][0]["item_name"] == "FS-9 LMG"

    # Malformed files are tolerated and leave an empty registry.
    bad = tmp / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    broken = BlueprintStore(bad)
    assert broken.loaded
    assert broken.error
    assert broken.blueprints == ()


def check_watcher(tmp: Path) -> None:
    log_path = tmp / "Game.log"
    log_path.write_text(
        BLUEPRINT_LINE + "\n" + ITEM_LINE + "\n" + ECHO_LINE + "\n",
        encoding="utf-8",
    )
    received: list[BlueprintLogEvent] = []
    ready = threading.Event()

    def callback(item: BlueprintLogEvent) -> None:
        received.append(item)
        if len(received) >= 2:
            ready.set()

    watcher = BlueprintLogWatcher(
        path=log_path, callback=callback, poll_interval=0.02,
        from_start=True)
    watcher.start()
    try:
        assert ready.wait(4.0), "blueprint log watcher did not emit"
        assert received[0].kind == KIND_BLUEPRINT
        assert received[0].item_name == "Defiance Helmet Tactical"
        assert received[1].kind == KIND_ITEM
        assert received[1].item_name == "FS-9 LMG"
        assert len(received) == 2
    finally:
        watcher.stop()


def check_partial_line(tmp: Path) -> None:
    log_path = tmp / "partial.log"
    log_path.write_text(BLUEPRINT_LINE[:60], encoding="utf-8")
    received: list[BlueprintLogEvent] = []
    ready = threading.Event()

    def callback(item: BlueprintLogEvent) -> None:
        received.append(item)
        ready.set()

    watcher = BlueprintLogWatcher(
        path=log_path, callback=callback, poll_interval=0.02,
        from_start=True)
    watcher.start()
    try:
        time.sleep(0.2)
        assert received == [], "partial log line must not be parsed"
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(BLUEPRINT_LINE[60:] + "\n")
        assert ready.wait(4.0), "completed log line was not parsed"
        assert received[0].item_name == "Defiance Helmet Tactical"
    finally:
        watcher.stop()


def check_tracker(tmp: Path) -> None:
    registry = tmp / "unlocked_blueprints.json"
    now = [1000.0]
    FakeWatcher.instances.clear()
    recorder = Recorder()
    blueprint_payloads: list[dict] = []

    def on_blueprint(payload: dict) -> None:
        blueprint_payloads.append(payload)

    def wait_payloads(count: int, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while len(blueprint_payloads) < count:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    tracker = BlueprintTracker(
        registry_path=registry,
        log_watcher_factory=FakeWatcher,
        clock=lambda: now[0],
    )
    tracker.subscribe(recorder)
    tracker.subscribe_blueprints(on_blueprint)
    tracker.start()
    try:
        assert tracker.wait_until_ready(4.0), "registry worker did not become ready"
        watcher = FakeWatcher.instances[-1]
        assert watcher.started

        parser = BlueprintLogParser()
        blueprint = parser.parse_line(BLUEPRINT_LINE, now=now[0])[0]
        watcher.callback(blueprint)
        emitted = recorder.wait(lambda item: item.is_blueprint)
        payload = emitted.to_blueprint_dict()
        assert set(payload) == {"item_name", "timestamp", "is_new_unlock"}
        assert payload["item_name"] == "Defiance Helmet Tactical"
        assert payload["is_new_unlock"] is True
        assert payload["timestamp"].endswith("Z")
        assert wait_payloads(1)
        assert blueprint_payloads[0] == payload
        assert tracker.is_unlocked("Defiance Helmet Tactical")
        assert tracker.blueprints[0].checked_off

        # The registry is written by the worker before the event is dispatched.
        raw = json.loads(registry.read_text(encoding="utf-8"))
        assert raw["version"] == 1
        assert len(raw["blueprints"]) == 1

        now[0] = 1005.0
        duplicate = parser.parse_line(
            BLUEPRINT_LINE.replace(STAMP, "2026-03-26T17:16:00.000Z"),
            now=now[0])[0]
        watcher.callback(duplicate)
        repeated = recorder.wait(
            lambda item: item.is_blueprint and not item.is_new_unlock)
        assert repeated.to_blueprint_dict()["is_new_unlock"] is False
        assert repeated.checked_off
        assert wait_payloads(2)
        assert blueprint_payloads[-1]["is_new_unlock"] is False
        assert tracker.stats.duplicate_blueprints == 1
        assert tracker.stats.new_blueprints == 1

        now[0] = 1010.0
        item = parser.parse_line(ITEM_LINE, now=now[0])[0]
        watcher.callback(item)
        item_event = recorder.wait(lambda item: item.kind == KIND_ITEM)
        assert item_event.event_type == KIND_ITEM
        assert item_event.is_new
        assert not item_event.is_new_unlock
        assert item_event.quantity == 1

        now[0] = 1015.0
        loot = parser.parse_line(LOOT_LINE, now=now[0])[0]
        watcher.callback(loot)
        loot_event = recorder.wait(lambda item: item.kind == KIND_LOOT)
        assert loot_event.container == "Crate_123"

        stats = tracker.stats
        assert stats.total_events == 4
        assert stats.blueprint_events == 2
        assert stats.new_blueprints == 1
        assert stats.duplicate_blueprints == 1
        assert stats.item_events == 1
        assert stats.loot_events == 1
        assert stats.blueprints_known == 1
        assert stats.items_known == 2

        queued = []
        while True:
            try:
                queued.append(tracker.event_queue.get_nowait())
                tracker.event_queue.task_done()
            except queue.Empty:
                break
        assert len(queued) == 4
        assert all(isinstance(item, InventoryEvent) for item in queued)
    finally:
        tracker.close()
    assert watcher.stopped

    # A fresh tracker cross-references the persisted registry on startup.
    FakeWatcher.instances.clear()
    recorder2 = Recorder()
    tracker2 = BlueprintTracker(
        registry_path=registry,
        log_watcher_factory=FakeWatcher,
        clock=lambda: 2000.0,
    )
    tracker2.subscribe(recorder2)
    tracker2.start()
    try:
        assert tracker2.wait_until_ready(4.0)
        watcher2 = FakeWatcher.instances[-1]
        duplicate = BlueprintLogParser().parse_line(BLUEPRINT_LINE, now=2000.0)[0]
        watcher2.callback(duplicate)
        emitted = recorder2.wait(lambda item: item.is_blueprint)
        assert emitted.is_new_unlock is False
        assert tracker2.stats.new_blueprints == 0
        assert tracker2.stats.duplicate_blueprints == 1
    finally:
        tracker2.close()
    assert watcher2.stopped


def main() -> int:
    check_timestamp()
    check_parser()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        for name, check in (
                ("store", check_store),
                ("watcher", check_watcher),
                ("partial", check_partial_line),
                ("tracker", check_tracker)):
            directory = root / name
            directory.mkdir()
            check(directory)
    print("ALL BLUEPRINT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
