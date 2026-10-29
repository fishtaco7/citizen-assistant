"""Offline checks for the Commodity → HAULING workflow engine.

    python -m tools.workflow_selftest

No game, network, Qt event loop or real Game.log is required.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.game_log import GameLogEvent, GameLogMonitor, GameLogParser  # noqa: E402
from app.hauling import Route  # noqa: E402
from app.planner import PlannerRoute  # noqa: E402
from app.workflow import (  # noqa: E402
    ActiveRouteManifest, HaulingWorkflowEngine, InsufficientCapital,
    WorkflowState,
)


class FakeMonitor:
    instances: list["FakeMonitor"] = []

    def __init__(self, callback):
        self.callback = callback
        self.started = False
        self.stopped = False
        FakeMonitor.instances.append(self)

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

    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for event in self.events:
                    if predicate(event):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError("workflow event timeout")
                self.condition.wait(remaining)


def commodity_route() -> Route:
    return Route(
        commodity_id=1,
        commodity="Gold",
        slug="gold",
        origin="Admin - Seraphim",
        destination="Admin - Area18",
        origin_system="Stanton",
        origin_planet="Crusader",
        destination_system="Stanton",
        destination_planet="ArcCorp",
        price_origin=1000,
        price_destination=1300,
        stock_origin=100,
        reachable=40,
        flags_origin={"freight": True, "station": True},
        flags_destination={"ground": True, "freight": True},
    )


def planner_route() -> PlannerRoute:
    return PlannerRoute(
        commodity="Gold",
        buy_station="Seraphim Station",
        sell_station="Area 18",
        path=("Seraphim Station", "Area 18"),
        investment=32000,
        gross_profit=9600,
        duration_minutes=10.0,
        ppm=960.0,
        trip_scu=32,
        per_scu=300,
        distance_gm=12.0,
        auto_load=True,
        high_gravity=True,
        stock_origin=100,
        demand_destination=50,
        load_minutes=2.0,
        qt_minutes=2.0,
        jump_minutes=0.0,
        atmo_minutes=4.0,
        buy_system="Stanton",
        sell_system="Stanton",
        buy_price=1000,
        sell_price=1300,
        buy_auto_load=True,
        sell_auto_load=True,
        buy_high_gravity=False,
        sell_high_gravity=True,
        buy_kind="station",
        sell_kind="city",
    )


def check_manifest_mapping() -> None:
    manifest = ActiveRouteManifest.from_route_data(
        commodity_route(), cargo_capacity=32)
    assert manifest.commodity == "Gold"
    assert manifest.total_scu == 32
    assert manifest.purchase_price == 1000
    assert manifest.base_investment == 32000
    assert manifest.gross_projected_profit == 9600
    assert manifest.origin.name == "Seraphim Station"
    assert manifest.origin.auto_load is True
    assert manifest.destination.name == "Area 18"
    assert manifest.destination.gravity == "high"
    assert manifest.destination.demand_price == 1300

    planner = ActiveRouteManifest.from_route_data(planner_route())
    assert planner.source == "planner"
    assert planner.target_ppm == 960
    assert planner.origin.auto_load is True
    assert planner.destination.gravity == "high"
    assert planner.destination.demand_price == 1300
    assert planner.planned_duration_minutes == 10

    planner_dict = ActiveRouteManifest.from_route_data(
        planner_route().as_dict())
    assert planner_dict.source == "planner"
    assert planner_dict.target_ppm == 960
    assert planner_dict.origin.auto_load is True
    assert planner_dict.origin.gravity == "low"
    assert planner_dict.destination.gravity == "high"

    mapped = ActiveRouteManifest.from_route_data({
        "commodity_name": "Gold",
        "buy_station": "Seraphim Station",
        "sell_station": "Area 18",
        "trip_scu": 10,
        "price_origin": 1000,
        "price_destination": 1300,
        "buy_auto_load": True,
        "sell_high_gravity": True,
        "origin": {"name": "Seraphim Station", "kind": "station"},
        "destination": {"name": "Area 18", "kind": "city"},
    })
    assert mapped.total_scu == 10
    assert mapped.origin.name == "Seraphim Station"
    assert mapped.destination.name == "Area 18"
    assert mapped.origin.auto_load is True
    assert mapped.destination.gravity == "high"


def check_workflow(tmp: Path) -> None:
    FakeMonitor.instances.clear()
    recorder = Recorder()
    engine = HaulingWorkflowEngine(
        state_dir=tmp,
        starting_capital=1_000_000,
        log_monitor_factory=FakeMonitor,
    )
    engine.subscribe(recorder)
    assert engine.state is WorkflowState.BROWSING
    assert engine.wallet.available_capital == 1_000_000

    manifest = engine.initialize_hauling_job(
        commodity_route(), cargo_capacity=32)
    assert engine.state is WorkflowState.LOCKED_LOADED
    assert manifest.session_id
    assert engine.active_cache_path.exists()
    assert engine.wallet.available_capital == 968_000
    assert engine.wallet.reserved_capital == 32_000
    assert FakeMonitor.instances and FakeMonitor.instances[-1].started
    event = recorder.wait(
        lambda item: item.event_type == "workflow_transition"
        and item.state == "LOCKED_LOADED")
    assert event.target_state == "HAULING"
    assert event.manifest_data is not None
    assert event.manifest_data.session_id == manifest.session_id

    adjusted = engine.adjust_active_manifest(8)
    assert adjusted.remaining_scu == 24
    assert adjusted.cargo_lost_scu == 8
    assert adjusted.gross_projected_profit == 7200
    assert adjusted.actual_ppm >= 0
    persisted = engine.active_cache_path.read_text()
    assert '"cargo_lost_scu":8' in persisted

    engine.begin_transit(source="test")
    assert engine.state is WorkflowState.EN_ROUTE
    recorder.wait(lambda item: item.state == "EN_ROUTE")

    final = engine.complete_hauling_job(source="test")
    assert engine.state is WorkflowState.COMPLETED
    assert not engine.active_cache_path.exists()
    assert final.realized_profit == -800
    assert final.final_proceeds == 31200
    assert engine.wallet.completed_runs == 1
    assert engine.wallet.cargo_lost_scu == 8
    assert engine.wallet.available_capital == 999_200
    assert FakeMonitor.instances[-1].stopped
    recorder.wait(lambda item: item.state == "COMPLETED")

    # A completed run can be followed by another lock without a manual reset.
    engine.initialize_hauling_job(planner_route())
    assert engine.state is WorkflowState.LOCKED_LOADED
    aborted = engine.abort_hauling_job(
        reason="interdiction", salvage_proceeds=0)
    assert engine.state is WorkflowState.ABORTED
    assert aborted.abort_reason == "interdiction"
    assert engine.wallet.aborted_runs == 1
    assert engine.wallet.realized_profit == -32800
    assert not engine.active_cache_path.exists()

    engine.reset_to_browsing()
    assert engine.state is WorkflowState.BROWSING
    engine.close()


def check_capital_guard() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = HaulingWorkflowEngine(
            state_dir=tmp,
            starting_capital=1000,
            log_monitor_factory=FakeMonitor,
        )
        try:
            engine.initialize_hauling_job(commodity_route(), cargo_capacity=32)
        except InsufficientCapital:
            pass
        else:
            raise AssertionError("capital guard did not fire")
        assert engine.state is WorkflowState.BROWSING
        assert not engine.active_cache_path.exists()
        engine.close()


def check_persistence() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        engine = HaulingWorkflowEngine(
            state_dir=tmp,
            starting_capital=2_000_000,
            log_monitor_factory=FakeMonitor,
        )
        manifest = engine.initialize_hauling_job(
            commodity_route(), cargo_capacity=32)
        engine.close()

        restored = HaulingWorkflowEngine(
            state_dir=tmp,
            log_monitor_factory=FakeMonitor,
        )
        try:
            assert restored.state is WorkflowState.LOCKED_LOADED
            assert restored.manifest is not None
            assert restored.manifest.session_id == manifest.session_id
            assert restored.wallet.reserved_capital == 32_000
            assert restored.wallet.available_capital == 1_968_000
            assert FakeMonitor.instances[-1].started
        finally:
            restored.close()


def check_log_parser_and_monitor() -> None:
    parser = GameLogParser()
    events = parser.parse_line(
        '[Notice] Added notification "Entered pilot seat"')
    assert any(event.kind == "pilot_seat" and event.value is True
               for event in events)
    events = parser.parse_line(
        '[Notice] Added notification "Exited pilot seat"')
    assert any(event.kind == "pilot_seat" and event.value is False
               for event in events)
    events = parser.parse_line("[Notice] Current location: Area18")
    assert any(event.kind == "location" and event.value == "Area 18"
               for event in events)
    events = parser.parse_line(
        '[Notice] Added notification "Entering Area18"')
    assert any(event.kind == "location" and event.value == "Area 18"
               for event in events)

    with tempfile.TemporaryDirectory() as raw:
        log_path = Path(raw) / "Game.log"
        log_path.write_text(
            'line one\n'
            '[Notice] Added notification "Entered pilot seat"\n'
            '[Notice] Current location: Area18\n',
            encoding="utf-8")
        received = []
        ready = threading.Event()

        def callback(event):
            received.append(event)
            if len(received) >= 2:
                ready.set()

        monitor = GameLogMonitor(
            path=log_path, callback=callback, poll_interval=0.02,
            from_start=True)
        monitor.start()
        try:
            assert ready.wait(2.0), "Game.log monitor did not emit"
            assert any(event.kind == "pilot_seat" for event in received)
            assert any(event.kind == "location" for event in received)
        finally:
            monitor.stop()


def check_game_log_transition() -> None:
    with tempfile.TemporaryDirectory() as raw:
        recorder = Recorder()
        engine = HaulingWorkflowEngine(
            state_dir=Path(raw),
            starting_capital=500_000,
            log_monitor_factory=FakeMonitor,
            auto_complete_on_arrival=True,
        )
        engine.subscribe(recorder)
        try:
            engine.initialize_hauling_job(
                commodity_route(), cargo_capacity=32)
            monitor = FakeMonitor.instances[-1]
            monitor.callback(GameLogEvent(
                "pilot_seat", True, "Entered pilot seat", time.time()))
            assert engine.state is WorkflowState.EN_ROUTE
            monitor.callback(GameLogEvent(
                "location", "Area18", "Current location: Area18",
                time.time()))
            assert engine.state is WorkflowState.COMPLETED
            assert not engine.active_cache_path.exists()
            assert monitor.stopped
            recorder.wait(lambda item: item.event_type == "destination_reached")
            recorder.wait(lambda item: item.state == "COMPLETED")
        finally:
            engine.close()


def main() -> int:
    check_manifest_mapping()
    check_capital_guard()
    check_log_parser_and_monitor()
    with tempfile.TemporaryDirectory() as raw:
        check_workflow(Path(raw))
    check_persistence()
    check_game_log_transition()
    print("ALL WORKFLOW CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
