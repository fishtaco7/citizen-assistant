"""Offline checks for the headless combat tracker.

    python -m tools.combat_selftest

No game, network, Qt event loop, or real Game.log is required.
"""
from __future__ import annotations

import queue
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.combat import (  # noqa: E402
    CombatEvent, CombatSessionStats, CombatTracker, normalize_status,
)
from app.combat_log import (  # noqa: E402
    KIND_PLAYER_KILL,
    KIND_VEHICLE_DAMAGE,
    KIND_VEHICLE_DESTRUCTION,
    KIND_WEAPON_IMPACT,
    STATUS_ACTIVE,
    STATUS_DESTROYED,
    STATUS_SOFT_DEAD,
    CombatLogEvent,
    CombatLogParser,
    CombatLogWatcher,
    parse_log_timestamp,
    pretty_vehicle_name,
)

STAMP = "2025-10-25T12:00:00.000Z"


def line(body: str, stamp: str = STAMP) -> str:
    return f"<{stamp}> {body}"


VEHICLE_SOFT = line(
    "<Vehicle Destruction> CVehicle::OnAdvanceDestroyLevel: "
    "Vehicle 'ANVL_Paladin_6763231335005' [123456] in zone 'Crusader_789' "
    "[pos x: 1.0, y: 2.0, z: 3.0 vel x: 0.0, y: 0.0, z: 0.0] "
    "driven by 'PilotOne' [789012] advanced from destroy level 0 to 1 "
    "caused by 'AttackerOne' [345678] with 'Combat'"
)

VEHICLE_FULL = line(
    "<Vehicle Destruction> CVehicle::OnAdvanceDestroyLevel: "
    "Vehicle 'AEGS_Gladius_1234567890' [654321] in zone 'Stanton_1' "
    "[pos x: 0.0, y: 0.0, z: 0.0 vel x: 0.0, y: 0.0, z: 0.0] "
    "driven by 'PilotTwo' [111111] advanced from destroy level 1 to 2 "
    "caused by 'AttackerTwo' [222222] with 'Combat'"
)

ACTOR_KILL = line(
    "<Actor Death> CActor::Kill: 'NPC_Archetypes-Male-Human-FrontierFighters_"
    "soldier_6897206313090' [333333] in zone 'Crusader_789' killed by "
    "'PlayerOne' [444444] using 'FS-9' [Class Bullet] with damage type "
    "'Bullet' from direction x: 1.0, y: 0.0, z: 0.0"
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
                    raise AssertionError("combat event timeout")
                self.condition.wait(remaining)


def event(
        kind: str,
        status: str,
        target: str,
        timestamp: float,
        *,
        target_id: str = "",
        attacker: str = "",
        is_npc_target: bool = False,
        is_npc_attacker: bool = False) -> CombatLogEvent:
    return CombatLogEvent(
        kind=kind,
        status=status,
        target_name=target,
        target_type="Vehicle",
        timestamp=timestamp,
        target_id=target_id,
        attacker=attacker,
        is_npc_target=is_npc_target,
        is_npc_attacker=is_npc_attacker,
    )


def check_timestamp_and_names() -> None:
    expected = datetime.fromisoformat(
        STAMP.replace("Z", "+00:00")).timestamp()
    assert abs(parse_log_timestamp(f"<{STAMP}> hello") - expected) < 0.001
    assert parse_log_timestamp("no timestamp", now=123.0) == 123.0
    assert pretty_vehicle_name("ANVL_Paladin_6763231335005") == "Paladin"
    assert pretty_vehicle_name("ORIG_890Jump_6166775878721") == "890 Jump"
    assert pretty_vehicle_name("Gladius") == "Gladius"
    assert normalize_status("soft-dead") == STATUS_SOFT_DEAD
    assert normalize_status("disabled") == STATUS_SOFT_DEAD
    assert normalize_status("DESTROYED") == STATUS_DESTROYED
    assert normalize_status("active") == STATUS_ACTIVE


def check_parser() -> None:
    parser = CombatLogParser()

    events = parser.parse_line(VEHICLE_SOFT, now=1.0)
    assert len(events) == 1
    soft = events[0]
    assert soft.kind == KIND_VEHICLE_DESTRUCTION
    assert soft.status == STATUS_SOFT_DEAD
    assert soft.target_name == "Paladin"
    assert soft.target_id == "123456"
    assert soft.attacker == "AttackerOne"
    assert soft.from_level == 0
    assert soft.to_level == 1
    assert soft.target_type == "Vehicle"
    assert soft.timestamp > 1.0

    events = parser.parse_line(VEHICLE_FULL, now=1.0)
    assert len(events) == 1
    full = events[0]
    assert full.status == STATUS_DESTROYED
    assert full.target_name == "Gladius"
    assert full.from_level == 1
    assert full.to_level == 2

    events = parser.parse_line(
        "[Notice] Vehicle 'Gladius' reported soft death: hull disabled",
        now=2.0)
    assert len(events) == 1
    assert events[0].status == STATUS_SOFT_DEAD
    assert events[0].target_name == "Gladius"
    assert events[0].timestamp == 2.0

    events = parser.parse_line(
        "[Notice] Ship 'Gladius' was destroyed in a catastrophic explosion",
        now=3.0)
    assert len(events) == 1
    assert events[0].status == STATUS_DESTROYED
    assert events[0].target_name == "Gladius"

    assert parser.parse_line(
        "[Notice] Refinery work order destroyed at Area18", now=4.0) == []

    events = parser.parse_line(ACTOR_KILL, now=5.0)
    assert len(events) == 1
    kill = events[0]
    assert kill.kind == KIND_PLAYER_KILL
    assert kill.status == STATUS_DESTROYED
    assert kill.target_name.startswith("NPC_Archetypes")
    assert kill.target_type == "Actor"
    assert kill.attacker == "PlayerOne"
    assert kill.weapon == "FS-9"
    assert kill.weapon_class == "Bullet"
    assert kill.damage_type == "Bullet"
    assert kill.is_npc_target
    assert not kill.is_npc_attacker

    loose_kill = parser.parse_line(
        line("<Actor Death> CActor::Kill: 'Victim' [555] in zone 'Z' "
             "killed by 'Killer' [666] using 'P4-AR' with damage type 'Bullet'"),
        now=5.5)
    assert len(loose_kill) == 1
    assert loose_kill[0].kind == KIND_PLAYER_KILL
    assert loose_kill[0].target_name == "Victim"
    assert loose_kill[0].attacker == "Killer"
    assert loose_kill[0].weapon == "P4-AR"
    assert loose_kill[0].damage_type == "Bullet"

    events = parser.parse_line(
        "[Notice] Weapon impact on 'Gladius' from a laser cannon", now=6.0)
    assert len(events) == 1
    assert events[0].kind == KIND_WEAPON_IMPACT
    assert events[0].status == STATUS_ACTIVE
    assert events[0].target_name == "Gladius"

    events = parser.parse_line(
        "[Notice] Vehicle 'Gladius' took hull damage from a projectile",
        now=7.0)
    assert len(events) == 1
    assert events[0].kind == KIND_VEHICLE_DAMAGE
    assert events[0].status == STATUS_ACTIVE
    assert events[0].target_name == "Gladius"

    assert parser.parse_line(
        "[Notice] PlayerOne joined the server", now=8.0) == []

    # Generic patch-specific prose can be disabled; the stable destroy-level
    # format still parses because it is the canonical source of truth.
    custom = CombatLogParser(
        soft_death_patterns=(),
        destruction_patterns=(),
        vehicle_damage_patterns=(),
        weapon_impact_patterns=())
    assert custom.parse_line(VEHICLE_SOFT, now=9.0)
    assert custom.parse_line(
        "[Notice] Vehicle 'Gladius' reported soft death", now=9.0) == []


def check_watcher(tmp: Path) -> None:
    log_path = tmp / "Game.log"
    log_path.write_text(
        VEHICLE_SOFT + "\n"
        "[Notice] Ship 'Gladius' was destroyed in a catastrophic explosion\n",
        encoding="utf-8",
    )
    received = []
    ready = threading.Event()

    def callback(item):
        received.append(item)
        if len(received) >= 2:
            ready.set()

    watcher = CombatLogWatcher(
        path=log_path, callback=callback, poll_interval=0.02,
        from_start=True)
    watcher.start()
    try:
        assert ready.wait(3.0), "combat log watcher did not emit"
        assert received[0].status == STATUS_SOFT_DEAD
        assert received[0].target_name == "Paladin"
        assert received[1].status == STATUS_DESTROYED
        assert received[1].target_name == "Gladius"
    finally:
        watcher.stop()


def check_tracker() -> None:
    now = [1000.0]
    FakeWatcher.instances.clear()
    recorder = Recorder()
    tracker = CombatTracker(
        log_watcher_factory=FakeWatcher,
        clock=lambda: now[0],
    )
    tracker.subscribe(recorder)
    tracker.start()
    try:
        started = recorder.wait(
            lambda item: item.event_type == "session_started")
        assert started.operational_status == STATUS_ACTIVE
        watcher = FakeWatcher.instances[-1]
        assert watcher.started

        active = tracker.emit_combat_event(
            event(KIND_VEHICLE_DAMAGE, STATUS_ACTIVE, "Gladius", now[0]))
        assert active is not None
        assert active.event_type == "target_state"
        assert active.status_changed
        assert active.to_dict()["operational_status"] == STATUS_ACTIVE
        assert active.to_dict()["target_name"] == "Gladius"
        assert tracker.stats.targets_seen == 1
        assert tracker.stats.damage_events == 1

        impact = tracker.emit_combat_event(
            event(KIND_WEAPON_IMPACT, STATUS_ACTIVE, "Gladius", 1005.0))
        assert impact is not None
        assert impact.event_type == KIND_WEAPON_IMPACT
        assert tracker.stats.weapon_impacts == 1
        assert not tracker.stats.last_event_at is None

        now[0] = 1010.0
        soft = tracker.emit_combat_event(
            event(KIND_VEHICLE_DESTRUCTION, STATUS_SOFT_DEAD,
                  "Gladius", now[0], target_id="ship-1"))
        assert soft is not None
        assert soft.event_type == "target_state"
        assert soft.status == STATUS_SOFT_DEAD
        assert tracker.target_status("Gladius") == STATUS_SOFT_DEAD
        assert tracker.stats.soft_dead_targets == 1
        assert tracker.stats.targets_seen == 1
        assert tracker.stats.targets_neutralized == 1
        assert tracker.stats.engagement_count == 1
        assert tracker.stats.last_engagement_at == 1010.0

        now[0] = 1020.0
        destroyed = tracker.emit_combat_event(
            event(KIND_VEHICLE_DESTRUCTION, STATUS_DESTROYED,
                  "Cutlass", now[0], target_id="ship-2"))
        assert destroyed is not None
        assert destroyed.status == STATUS_DESTROYED
        assert tracker.stats.destroyed_targets == 1
        assert tracker.stats.targets_neutralized == 2
        assert abs(tracker.stats.last_engagement_delta - 10.0) < 0.01
        assert abs(tracker.stats.average_engagement_delta - 10.0) < 0.01

        # A late soft-death line must not downgrade a destroyed target.
        now[0] = 1025.0
        late = tracker.emit_combat_event(
            event(KIND_VEHICLE_DESTRUCTION, STATUS_SOFT_DEAD,
                  "Cutlass", now[0], target_id="ship-2"))
        assert late is None
        assert tracker.target_status("Cutlass") == STATUS_DESTROYED

        # A soft-dead target can later be fully destroyed without inflating
        # the unique neutralized-target count.
        now[0] = 1030.0
        final = tracker.emit_combat_event(
            event(KIND_VEHICLE_DESTRUCTION, STATUS_DESTROYED,
                  "Gladius", now[0]))
        assert final is not None
        assert tracker.stats.targets_neutralized == 2
        assert tracker.stats.soft_dead_targets == 1
        assert tracker.stats.destroyed_targets == 2
        assert tracker.stats.last_engagement_delta == 10.0

        # Repeated terminal lines keep the state but do not flash again.
        now[0] = 1040.0
        assert tracker.emit_combat_event(
            event(KIND_VEHICLE_DESTRUCTION, STATUS_DESTROYED,
                  "Gladius", now[0])) is None

        stats = tracker.stats
        assert stats.uptime_seconds == 40.0
        assert stats.current_survival_seconds == 20.0
        assert stats.longest_survival_seconds == 20.0
        assert stats.time_since_last_damage == 10.0
        assert stats.targets_seen == 2
        assert stats.active_targets == 0

        # The queue receives structured event objects.
        queued = []
        while True:
            try:
                queued.append(tracker.event_queue.get_nowait())
                tracker.event_queue.task_done()
            except queue.Empty:
                break
        assert any(item.event_type == "target_state" for item in queued)
        assert any(item.status == STATUS_SOFT_DEAD for item in queued)
        assert all(isinstance(item, CombatEvent) for item in queued)

        # A targetless impact still counts, but cannot produce a HUD alert.
        assert tracker.emit_combat_event(
            event(KIND_WEAPON_IMPACT, STATUS_ACTIVE, "", 1041.0)) is None
        assert tracker.stats.weapon_impacts == 2

        reset = tracker.reset_session()
        assert reset.targets_neutralized == 0
        assert reset.uptime_seconds == 0.0
        assert reset.session_started_at == 1040.0
        assert tracker.targets == ()
        assert tracker.last_event is not None
        assert tracker.last_event.event_type == "session_reset"

        now[0] = 1050.0
        again = tracker.emit_combat_event(
            event(KIND_VEHICLE_DESTRUCTION, STATUS_SOFT_DEAD,
                  "Avenger", now[0]))
        assert again is not None
        assert tracker.stats.targets_neutralized == 1
        assert tracker.stats.engagement_count == 1
        assert tracker.stats.last_engagement_delta is None

        flushed = tracker.flush_stats(reset=True)
        assert flushed.targets_neutralized == 1
        assert flushed.uptime_seconds == 10.0
        assert tracker.stats.targets_neutralized == 0
        assert tracker.stats.uptime_seconds == 0.0

        manual = tracker.emit_combat_event({
            "target_name": "Vulture",
            "target_type": "Vehicle",
            "operational_status": "Soft-Dead",
            "kind": "manual",
            "timestamp": 1060.0,
        })
        assert manual is not None
        assert manual.operational_status == STATUS_SOFT_DEAD
        assert manual.target_name == "Vulture"
        assert tracker.stats.targets_neutralized == 1
    finally:
        tracker.close()

    assert watcher.stopped
    assert tracker.last_event is not None


def check_stats_dataclass() -> None:
    stats = CombatSessionStats(
        session_started_at=10.0,
        updated_at=15.0,
        targets_neutralized=2,
        uptime_seconds=5.0,
    )
    payload = stats.to_dict()
    assert payload["targets_neutralized"] == 2
    assert payload["total_targets_neutralized"] == 2
    assert payload["survival_uptime_seconds"] == 5.0
    assert stats.total_targets_neutralized == 2
    assert stats.survival_uptime_seconds == 5.0


def main() -> int:
    check_timestamp_and_names()
    check_parser()
    check_stats_dataclass()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        check_watcher(root)
        check_tracker()
    print("ALL COMBAT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
