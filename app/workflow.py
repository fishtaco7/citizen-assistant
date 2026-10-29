"""Thread-safe hauling workflow bridge.

This module joins the two read-only datasets already used by the overlay:

* :class:`app.hauling.Route` from the Commodity Trading tab, and
* :class:`app.planner.PlannerRoute` from the HAULING PLANNER.

It deliberately contains no Qt imports and no widget creation. The hosting UI
subscribes to :class:`WorkflowEvent` objects and can switch views on its own
thread. All state changes, disk writes and wallet settlements run under one
re-entrant lock; network-free route mapping happens before the lock is taken.

The only game-facing component is the read-only ``Game.log`` tailer in
:mod:`app.game_log`. Nothing here reads game memory or writes to the game.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import config_dir
from .game_log import GameLogEvent, GameLogMonitor
from .hauling import Route
from .planner import (AUTO_LOAD_BASE_MIN, AUTO_LOAD_MIN_PER_SCU,
                      DEFAULT_GROUND_PENALTY, DEFAULT_QT_SPEED_GM_S,
                      DOCK_MINUTES, JUMP_MINUTES, MANUAL_LOAD_BASE_MIN,
                      MANUAL_LOAD_MIN_PER_SCU, PlannerRoute)
from .stations import clean_terminal_name

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
ACTIVE_CACHE_NAME = "active_run.json"
WALLET_CACHE_NAME = "workflow_wallet.json"

__all__ = [
    "ActiveRouteManifest",
    "HaulingWorkflowEngine",
    "InsufficientCapital",
    "InvalidTransition",
    "ManifestLocation",
    "NoActiveJob",
    "WalletLedger",
    "WorkflowError",
    "WorkflowEvent",
    "WorkflowEventBus",
    "WorkflowState",
]


# ---------------------------------------------------------------------------
class WorkflowState(str, Enum):
    BROWSING = "BROWSING"
    LOCKED_LOADED = "LOCKED_LOADED"
    EN_ROUTE = "EN_ROUTE"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"

    @property
    def active(self) -> bool:
        return self in (WorkflowState.LOCKED_LOADED, WorkflowState.EN_ROUTE)

    @property
    def terminal(self) -> bool:
        return self in (WorkflowState.COMPLETED, WorkflowState.ABORTED)


class WorkflowError(RuntimeError):
    """Base class for workflow failures."""


class InvalidTransition(WorkflowError):
    """The requested state change is not legal from the current state."""


class InsufficientCapital(WorkflowError):
    """The active run costs more than the unlocked trading capital."""


class NoActiveJob(WorkflowError):
    """An operation requiring an active run was called while browsing."""


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _norm(value: str) -> str:
    text = (value or "").casefold().replace("&", "and")
    return "".join(char for char in text if char.isalnum())


def _pick(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _estimate_commodity_duration(
        distance_gm: float, trip_scu: int, *, origin_auto: bool,
        destination_auto: bool, origin_ground: bool, destination_ground: bool,
        origin_system: str, destination_system: str) -> float:
    """Estimate the planner's route timing for a Commodity-tab route."""
    def load(auto: bool) -> float:
        if auto:
            return AUTO_LOAD_BASE_MIN + trip_scu * AUTO_LOAD_MIN_PER_SCU
        return MANUAL_LOAD_BASE_MIN + trip_scu * MANUAL_LOAD_MIN_PER_SCU

    distance = max(0.0, float(distance_gm or 0.0))
    qt = 0.8 + distance / max(0.05, DEFAULT_QT_SPEED_GM_S) / 60.0
    jump = (JUMP_MINUTES if (origin_system and destination_system
                             and origin_system.casefold()
                             != destination_system.casefold()) else 0.0)
    atmo = ((0.25 if not origin_ground else DEFAULT_GROUND_PENALTY)
            + (0.25 if not destination_ground else DEFAULT_GROUND_PENALTY))
    return max(1.0, load(origin_auto) + load(destination_auto)
               + qt + jump + atmo + DOCK_MINUTES)


def _location(value: Any, *, default_name: str = "") -> "ManifestLocation":
    if isinstance(value, ManifestLocation):
        return value
    if isinstance(value, Mapping):
        name = str(_pick(value, "name", "display", "station", "city",
                         default=default_name) or default_name)
        return ManifestLocation(
            name=name,
            kind=str(value.get("kind") or ""),
            auto_load=bool(value.get("auto_load", False)),
            gravity=str(value.get("gravity") or "unknown"),
            demand_price=_num(value.get("demand_price")),
        )
    return ManifestLocation(name=str(value or default_name))


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ManifestLocation:
    """The parts of a station/city snapshot relevant to a hauling run."""

    name: str
    kind: str = ""
    auto_load: bool = False
    gravity: str = "unknown"
    demand_price: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "auto_load": self.auto_load,
            "gravity": self.gravity,
            "demand_price": self.demand_price,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | str | None) -> "ManifestLocation":
        if not isinstance(value, Mapping):
            return ManifestLocation(name=str(value or ""))
        return cls(
            name=str(value.get("name") or ""),
            kind=str(value.get("kind") or ""),
            auto_load=bool(value.get("auto_load", False)),
            gravity=str(value.get("gravity") or "unknown"),
            demand_price=_num(value.get("demand_price")),
        )


@dataclass(frozen=True)
class ActiveRouteManifest:
    """Immutable snapshot of the route locked into an active hauling loop."""

    session_id: str
    created_at: float
    updated_at: float
    commodity: str
    total_scu: int
    purchase_price: float
    origin: ManifestLocation
    destination: ManifestLocation
    base_investment: float
    gross_projected_profit: float
    target_ppm: float
    planned_duration_minutes: float = 0.0
    actual_ppm: float = 0.0
    cargo_lost_scu: int = 0
    current_location: str = ""
    source: str = "commodity_tab"
    arrived_at: float = 0.0
    completed_at: float = 0.0
    final_sale_price: float = 0.0
    final_proceeds: float = 0.0
    realized_profit: float = 0.0
    abort_reason: str = ""

    @property
    def remaining_scu(self) -> int:
        return max(0, int(self.total_scu) - int(self.cargo_lost_scu))

    @property
    def per_scu(self) -> float:
        return float(self.destination.demand_price) - float(self.purchase_price)

    @property
    def sale_value(self) -> float:
        return max(0.0, self.destination.demand_price) * self.remaining_scu

    @property
    def route_text(self) -> str:
        return f"{self.origin.name} → {self.destination.name}"

    def to_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "commodity": self.commodity,
            "total_scu": self.total_scu,
            "purchase_price": self.purchase_price,
            "origin": self.origin.to_dict(),
            "destination": self.destination.to_dict(),
            "base_investment": self.base_investment,
            "gross_projected_profit": self.gross_projected_profit,
            "target_ppm": self.target_ppm,
            "planned_duration_minutes": self.planned_duration_minutes,
            "actual_ppm": self.actual_ppm,
            "cargo_lost_scu": self.cargo_lost_scu,
            "current_location": self.current_location,
            "source": self.source,
            "arrived_at": self.arrived_at,
            "completed_at": self.completed_at,
            "final_sale_price": self.final_sale_price,
            "final_proceeds": self.final_proceeds,
            "realized_profit": self.realized_profit,
            "abort_reason": self.abort_reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActiveRouteManifest":
        if not isinstance(value, Mapping):
            raise ValueError("manifest must be a mapping")
        origin = _location(value.get("origin"))
        destination = _location(value.get("destination"))
        total_scu = max(0, _int(value.get("total_scu")))
        if total_scu <= 0:
            raise ValueError("manifest has no cargo volume")
        return cls(
            session_id=str(value.get("session_id") or uuid.uuid4().hex),
            created_at=_num(value.get("created_at")) or time.time(),
            updated_at=_num(value.get("updated_at")) or time.time(),
            commodity=str(value.get("commodity") or "Unknown"),
            total_scu=total_scu,
            purchase_price=max(0.0, _num(value.get("purchase_price"))),
            origin=origin,
            destination=destination,
            base_investment=max(0.0, _num(value.get("base_investment"))),
            gross_projected_profit=_num(value.get("gross_projected_profit")),
            target_ppm=max(0.0, _num(value.get("target_ppm"))),
            planned_duration_minutes=max(
                0.0, _num(value.get("planned_duration_minutes"))),
            actual_ppm=max(0.0, _num(value.get("actual_ppm"))),
            cargo_lost_scu=min(total_scu, max(
                0, _int(value.get("cargo_lost_scu")))),
            current_location=str(value.get("current_location") or ""),
            source=str(value.get("source") or "commodity_tab"),
            arrived_at=_num(value.get("arrived_at")),
            completed_at=_num(value.get("completed_at")),
            final_sale_price=_num(value.get("final_sale_price")),
            final_proceeds=_num(value.get("final_proceeds")),
            realized_profit=_num(value.get("realized_profit")),
            abort_reason=str(value.get("abort_reason") or ""),
        )

    @classmethod
    def from_route_data(
            cls, route_data: Any, *,
            cargo_capacity: int | None = None,
            target_ppm: float | None = None,
            planned_duration_minutes: float | None = None,
            source: str | None = None,
            session_id: str | None = None,
            now: float | None = None) -> "ActiveRouteManifest":
        """Map a Commodity-tab ``Route``, planner route, or dict to a manifest."""
        stamp = time.time() if now is None else float(now)
        if isinstance(route_data, PlannerRoute):
            return cls._from_planner(
                route_data, target_ppm=target_ppm,
                planned_duration_minutes=planned_duration_minutes,
                source=source, session_id=session_id, now=stamp)
        if isinstance(route_data, Route):
            return cls._from_commodity(
                route_data, cargo_capacity=cargo_capacity,
                target_ppm=target_ppm,
                planned_duration_minutes=planned_duration_minutes,
                source=source, session_id=session_id, now=stamp)
        if isinstance(route_data, Mapping):
            return cls._from_mapping(
                route_data, cargo_capacity=cargo_capacity,
                target_ppm=target_ppm,
                planned_duration_minutes=planned_duration_minutes,
                source=source, session_id=session_id, now=stamp)
        raise TypeError(
            "route_data must be a Route, PlannerRoute, or mapping")

    # Alias that reads naturally in host code.
    from_route = from_route_data

    @classmethod
    def _build(
            cls, *, commodity: str, total_scu: int, purchase_price: float,
            destination_price: float, origin: ManifestLocation,
            destination: ManifestLocation, target_ppm: float,
            planned_duration_minutes: float, source: str,
            session_id: str | None, now: float) -> "ActiveRouteManifest":
        if total_scu <= 0:
            raise ValueError("route has no purchasable cargo volume")
        if purchase_price < 0 or destination_price < 0:
            raise ValueError("route prices cannot be negative")
        investment = purchase_price * total_scu
        gross = (destination_price - purchase_price) * total_scu
        planned = max(0.0, float(planned_duration_minutes or 0.0))
        if planned <= 0 and target_ppm and gross > 0:
            planned = gross / target_ppm
        actual = target_ppm if target_ppm > 0 else (
            gross / planned if planned > 0 else 0.0)
        destination = replace(destination, demand_price=destination_price)
        return cls(
            session_id=session_id or uuid.uuid4().hex,
            created_at=now,
            updated_at=now,
            commodity=commodity or "Unknown",
            total_scu=total_scu,
            purchase_price=purchase_price,
            origin=origin,
            destination=destination,
            base_investment=investment,
            gross_projected_profit=gross,
            target_ppm=max(0.0, target_ppm),
            planned_duration_minutes=planned,
            actual_ppm=max(0.0, actual),
            source=source,
        )

    @classmethod
    def _from_commodity(
            cls, route: Route, *, cargo_capacity: int | None,
            target_ppm: float | None, planned_duration_minutes: float | None,
            source: str | None, session_id: str | None,
            now: float) -> "ActiveRouteManifest":
        capacity = max(1, _int(cargo_capacity or route.available or 32))
        total = route.trip_scu(capacity)
        origin_flags = route.flags_origin or {}
        destination_flags = route.flags_destination or {}
        origin_ground = bool(origin_flags.get("ground"))
        destination_ground = bool(destination_flags.get("ground"))
        origin_kind = ("station" if origin_flags.get("station")
                       else "city" if origin_ground else "unknown")
        destination_kind = ("station" if destination_flags.get("station")
                            else "city" if destination_ground else "unknown")
        origin_auto = bool(origin_flags.get("freight", False))
        destination_auto = bool(destination_flags.get("freight", False))
        origin = ManifestLocation(
            name=clean_terminal_name(route.origin) or route.origin,
            kind=origin_kind,
            auto_load=origin_auto,
            gravity="high" if origin_ground else "low",
        )
        destination = ManifestLocation(
            name=clean_terminal_name(route.destination) or route.destination,
            kind=destination_kind,
            auto_load=destination_auto,
            gravity="high" if destination_ground else "low",
            demand_price=route.price_destination,
        )
        planned = planned_duration_minutes
        if planned is None and target_ppm is None:
            planned = _estimate_commodity_duration(
                route.distance, total,
                origin_auto=origin_auto,
                destination_auto=destination_auto,
                origin_ground=origin_ground,
                destination_ground=destination_ground,
                origin_system=route.origin_system,
                destination_system=route.destination_system)
        gross = (route.price_destination - route.price_origin) * total
        target = target_ppm
        if target is None:
            target = (max(0.0, gross) / max(1.0, planned or 1.0)
                      if gross > 0 else 0.0)
        return cls._build(
            commodity=route.commodity,
            total_scu=total,
            purchase_price=route.price_origin,
            destination_price=route.price_destination,
            origin=origin,
            destination=destination,
            target_ppm=target,
            planned_duration_minutes=planned or 0.0,
            source=source or "commodity_tab",
            session_id=session_id,
            now=now,
        )

    @classmethod
    def _from_planner(
            cls, route: PlannerRoute, *, target_ppm: float | None,
            planned_duration_minutes: float | None,
            source: str | None, session_id: str | None,
            now: float) -> "ActiveRouteManifest":
        total = max(0, int(route.trip_scu))
        purchase = route.buy_price
        if purchase <= 0 and total > 0:
            purchase = route.investment / total
        destination_price = route.sell_price
        if destination_price <= 0:
            destination_price = purchase + route.per_scu
        origin = ManifestLocation(
            name=route.buy_station,
            kind=route.buy_kind,
            auto_load=route.buy_auto_load,
            gravity="high" if route.buy_high_gravity else "low",
        )
        destination = ManifestLocation(
            name=route.sell_station,
            kind=route.sell_kind,
            auto_load=route.sell_auto_load,
            gravity="high" if route.sell_high_gravity else "low",
            demand_price=destination_price,
        )
        return cls._build(
            commodity=route.commodity,
            total_scu=total,
            purchase_price=purchase,
            destination_price=destination_price,
            origin=origin,
            destination=destination,
            target_ppm=(route.ppm if target_ppm is None else target_ppm),
            planned_duration_minutes=(
                route.duration_minutes if planned_duration_minutes is None
                else planned_duration_minutes),
            source=source or "planner",
            session_id=session_id,
            now=now,
        )

    @classmethod
    def _from_mapping(
            cls, data: Mapping[str, Any], *, cargo_capacity: int | None,
            target_ppm: float | None, planned_duration_minutes: float | None,
            source: str | None, session_id: str | None,
            now: float) -> "ActiveRouteManifest":
        origin_value = data.get("origin")
        destination_value = data.get("destination")
        origin_map = origin_value if isinstance(origin_value, Mapping) else {}
        destination_map = (destination_value
                           if isinstance(destination_value, Mapping) else {})
        commodity = str(_pick(
            data, "commodity", "commodity_name", default="Unknown"))
        total = _int(_pick(
            data, "total_scu", "trip_scu", "load_scu", "scu", "scu_reachable",
            default=0))
        if total <= 0 and cargo_capacity:
            total = max(0, _int(cargo_capacity))
        if total <= 0:
            raise ValueError("route mapping has no cargo volume")
        purchase = _num(_pick(
            data, "purchase_price", "buy_price", "price_origin", "price_buy",
            default=0))
        destination_price = _num(_pick(
            data, "demand_price", "sell_price", "price_destination",
            "price_sell", default=0))
        if destination_price <= 0 and "per_scu" in data:
            destination_price = purchase + _num(data.get("per_scu"))
        if origin_map:
            origin_name = str(origin_map.get("name") or "")
        elif isinstance(origin_value, str):
            origin_name = origin_value
        else:
            origin_name = ""
        if not origin_name:
            origin_name = str(_pick(data, "origin_terminal_name", "buy_station",
                                    default=""))
        if destination_map:
            destination_name = str(destination_map.get("name") or "")
        elif isinstance(destination_value, str):
            destination_name = destination_value
        else:
            destination_name = ""
        if not destination_name:
            destination_name = str(_pick(
                data, "destination_terminal_name", "sell_station",
                default=""))
        origin_kind = str(
            origin_map.get("kind")
            or _pick(data, "buy_kind", "origin_kind", default="")
        )
        destination_kind = str(
            destination_map.get("kind")
            or _pick(data, "sell_kind", "destination_kind", default="")
        )
        origin_auto = bool(
            origin_map.get("auto_load")
            if "auto_load" in origin_map else
            _pick(data, "origin_auto_load", "buy_auto_load", "auto_load_origin",
                  default=False))
        destination_auto = bool(
            destination_map.get("auto_load")
            if "auto_load" in destination_map else
            _pick(data, "destination_auto_load", "sell_auto_load",
                  "auto_load_destination", default=False))
        origin_gravity = str(
            origin_map.get("gravity")
            or _pick(data, "origin_gravity", "buy_gravity", default="unknown"))
        destination_gravity = str(
            destination_map.get("gravity")
            or _pick(data, "destination_gravity", "sell_gravity",
                     default="unknown"))
        if origin_gravity == "unknown":
            high = _pick(data, "buy_high_gravity",
                         "origin_high_gravity", default=None)
            if high is not None:
                origin_gravity = "high" if bool(high) else "low"
        if destination_gravity == "unknown":
            high = _pick(data, "sell_high_gravity",
                         "destination_high_gravity", default=None)
            if high is not None:
                destination_gravity = "high" if bool(high) else "low"
        return cls._build(
            commodity=commodity,
            total_scu=total,
            purchase_price=purchase,
            destination_price=destination_price,
            origin=ManifestLocation(
                name=clean_terminal_name(origin_name) or origin_name,
                kind=origin_kind,
                auto_load=origin_auto,
                gravity=origin_gravity,
            ),
            destination=ManifestLocation(
                name=clean_terminal_name(destination_name) or destination_name,
                kind=destination_kind,
                auto_load=destination_auto,
                gravity=destination_gravity,
                demand_price=destination_price,
            ),
            target_ppm=(_num(target_ppm) if target_ppm is not None
                        else _num(_pick(data, "target_ppm", "ppm", default=0))),
            planned_duration_minutes=(
                _num(planned_duration_minutes)
                if planned_duration_minutes is not None
                else _num(_pick(data, "planned_duration_minutes",
                                "duration_minutes", default=0))),
            source=source or str(data.get("source") or (
                "planner" if ("buy_station" in data
                              and "sell_station" in data
                              and "duration_minutes" in data)
                else "commodity_tab")),
            session_id=session_id,
            now=now,
        )


@dataclass
class WalletLedger:
    """Persistent capital/profit ledger for the workflow, not the game."""

    liquid_capital: float = 0.0
    reserved_capital: float = 0.0
    realized_profit: float = 0.0
    completed_runs: int = 0
    aborted_runs: int = 0
    cargo_lost_scu: int = 0
    capital_known: bool = False
    updated_at: float = 0.0

    @property
    def available_capital(self) -> float:
        return max(0.0, float(self.liquid_capital))

    @property
    def total_equity(self) -> float:
        return float(self.liquid_capital) + float(self.reserved_capital)

    def to_dict(self) -> dict:
        return {
            "liquid_capital": self.liquid_capital,
            "reserved_capital": self.reserved_capital,
            "realized_profit": self.realized_profit,
            "completed_runs": self.completed_runs,
            "aborted_runs": self.aborted_runs,
            "cargo_lost_scu": self.cargo_lost_scu,
            "capital_known": self.capital_known,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "WalletLedger":
        data = value if isinstance(value, Mapping) else {}
        return cls(
            liquid_capital=_num(data.get("liquid_capital")),
            reserved_capital=_num(data.get("reserved_capital")),
            realized_profit=_num(data.get("realized_profit")),
            completed_runs=max(0, _int(data.get("completed_runs"))),
            aborted_runs=max(0, _int(data.get("aborted_runs"))),
            cargo_lost_scu=max(0, _int(data.get("cargo_lost_scu"))),
            capital_known=bool(data.get("capital_known", False)),
            updated_at=_num(data.get("updated_at")),
        )

    def reserve(self, amount: float, *, enforce: bool = True) -> None:
        amount = max(0.0, float(amount))
        if self.capital_known and enforce and amount > self.liquid_capital + 1e-9:
            raise InsufficientCapital(
                f"run needs {amount:,.0f} aUEC; "
                f"{self.available_capital:,.0f} is available")
        if self.capital_known:
            self.liquid_capital -= amount
        self.reserved_capital += amount
        self.updated_at = time.time()

    def settle(self, investment: float, proceeds: float, *,
               completed: bool, cargo_lost_scu: int = 0) -> None:
        investment = max(0.0, float(investment))
        proceeds = max(0.0, float(proceeds))
        if self.capital_known:
            self.liquid_capital += proceeds
        self.realized_profit += proceeds - investment
        self.reserved_capital = max(0.0, self.reserved_capital - investment)
        if completed:
            self.completed_runs += 1
        else:
            self.aborted_runs += 1
        self.cargo_lost_scu += max(0, int(cargo_lost_scu))
        self.updated_at = time.time()


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WorkflowEvent:
    """Asynchronous payload delivered to host listeners."""

    event_type: str
    state: str
    previous_state: str
    target_state: str
    manifest_data: ActiveRouteManifest | None
    wallet_data: dict
    timestamp: float
    reason: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "state": self.state,
            "previous_state": self.previous_state,
            "target_state": self.target_state,
            "manifest_data": (self.manifest_data.to_dict()
                              if self.manifest_data else None),
            "wallet_data": dict(self.wallet_data),
            "timestamp": self.timestamp,
            "reason": self.reason,
            "detail": dict(self.detail),
        }


class WorkflowEventBus:
    """Queue-backed listener dispatcher; listeners never block a transition."""

    _SENTINEL = object()

    def __init__(self):
        self._listeners: list[Callable[[WorkflowEvent], None]] = []
        self._lock = threading.RLock()
        self._queue: queue.Queue = queue.Queue()
        self._history: deque[WorkflowEvent] = deque(maxlen=256)
        self._closed = False
        self._thread = threading.Thread(
            target=self._dispatch, name="sc-workflow-events", daemon=True)
        self._thread.start()

    def subscribe(self, listener: Callable[[WorkflowEvent], None]):
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            self._listeners.append(listener)
        return lambda: self.unsubscribe(listener)

    def unsubscribe(self, listener: Callable[[WorkflowEvent], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def emit(self, event: WorkflowEvent) -> None:
        with self._lock:
            if self._closed:
                return
            self._history.append(event)
        self._queue.put(event)

    @property
    def history(self) -> tuple[WorkflowEvent, ...]:
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
                        logger.exception("workflow event listener failed")
            finally:
                self._queue.task_done()


# ---------------------------------------------------------------------------
class HaulingWorkflowEngine:
    """Thread-safe hauling state machine and bridge between tab datasets."""

    VIEW_HAULING = "HAULING"
    VIEW_BROWSING = "COMMODITY"

    def __init__(
            self, *,
            state_dir: str | Path | None = None,
            starting_capital: float | None = None,
            game_log_path: str | Path | None = None,
            log_monitor_factory: (
                Callable[[Callable[[GameLogEvent], None]], GameLogMonitor]
                | None) = None,
            auto_start_log_monitor: bool = True,
            enforce_budget: bool = True,
            auto_complete_on_arrival: bool = False):
        self._dir = Path(state_dir) if state_dir is not None else config_dir()
        self._active_path = self._dir / ACTIVE_CACHE_NAME
        self._wallet_path = self._dir / WALLET_CACHE_NAME
        self._lock = threading.RLock()
        self._state = WorkflowState.BROWSING
        self._manifest: ActiveRouteManifest | None = None
        self._last_manifest: ActiveRouteManifest | None = None
        self._wallet = self._load_wallet(starting_capital)
        self._bus = WorkflowEventBus()
        self._auto_start_log_monitor = bool(auto_start_log_monitor)
        self._enforce_budget = bool(enforce_budget)
        self._auto_complete_on_arrival = bool(auto_complete_on_arrival)
        self._closed = False
        if log_monitor_factory is not None:
            self._monitor_factory = log_monitor_factory
        else:
            self._monitor_factory = lambda callback: GameLogMonitor(
                path=game_log_path, callback=callback)
        self._monitor: GameLogMonitor | None = None

        self._load_active_locked()
        self._reconcile_wallet_locked()
        if (self._state.active and self._auto_start_log_monitor):
            self._start_monitor_locked()

    # -- public snapshots ------------------------------------------------
    @property
    def state(self) -> WorkflowState:
        with self._lock:
            return self._state

    @property
    def manifest(self) -> ActiveRouteManifest | None:
        with self._lock:
            return self._manifest

    @property
    def active_manifest(self) -> ActiveRouteManifest | None:
        with self._lock:
            return self._manifest if self._state.active else None

    @property
    def last_manifest(self) -> ActiveRouteManifest | None:
        with self._lock:
            return self._last_manifest

    @property
    def wallet(self) -> WalletLedger:
        with self._lock:
            return replace(self._wallet)

    @property
    def wallet_data(self) -> dict:
        with self._lock:
            return self._wallet.to_dict()

    @property
    def active_cache_path(self) -> Path:
        return self._active_path

    @property
    def wallet_cache_path(self) -> Path:
        return self._wallet_path

    @property
    def event_bus(self) -> WorkflowEventBus:
        return self._bus

    def subscribe(self, listener: Callable[[WorkflowEvent], None]):
        return self._bus.subscribe(listener)

    def set_trading_capital(self, amount: float) -> WalletLedger:
        """Set the permanent, unlocked capital before starting a run."""
        with self._lock:
            if self._state.active:
                raise InvalidTransition(
                    "cannot replace trading capital while a run is active")
            self._wallet.capital_known = True
            self._wallet.liquid_capital = max(0.0, float(amount))
            self._wallet.reserved_capital = 0.0
            self._wallet.updated_at = time.time()
            self._save_wallet_locked()
            return replace(self._wallet)

    def emit_workflow_transition(
            self, target_state: str,
            manifest_data: ActiveRouteManifest | None = None,
            *, reason: str = "", detail: dict | None = None) -> WorkflowEvent:
        """Queue a host-facing transition event without changing state."""
        with self._lock:
            return self._emit_locked(
                "workflow_transition", target_state, self._state.value,
                self._state.value, manifest_data or self._manifest,
                reason=reason, detail=detail)

    # -- state transitions ----------------------------------------------
    def initialize_hauling_job(
            self, route_data: Any, *,
            cargo_capacity: int | None = None,
            target_ppm: float | None = None,
            planned_duration_minutes: float | None = None,
            source: str | None = None,
            session_id: str | None = None) -> ActiveRouteManifest:
        """Lock a route snapshot, reserve capital and start the log watcher."""
        manifest = ActiveRouteManifest.from_route_data(
            route_data,
            cargo_capacity=cargo_capacity,
            target_ppm=target_ppm,
            planned_duration_minutes=planned_duration_minutes,
            source=source,
            session_id=session_id)
        with self._lock:
            self._ensure_open_locked()
            if self._state.active:
                raise InvalidTransition(
                    f"a hauling run is already {self._state.value}")
            if self._state not in (
                    WorkflowState.BROWSING, WorkflowState.COMPLETED,
                    WorkflowState.ABORTED):
                raise InvalidTransition(
                    f"cannot lock a load from {self._state.value}")
            previous = self._state
            self._wallet.reserve(manifest.base_investment,
                                 enforce=self._enforce_budget)
            self._state = WorkflowState.LOCKED_LOADED
            self._manifest = manifest
            self._last_manifest = manifest
            self._save_wallet_locked()
            self._save_active_locked()
            self._start_monitor_locked()
            self._emit_locked(
                "workflow_transition", self.VIEW_HAULING,
                previous.value, self._state.value, manifest,
                reason="load locked")
            return manifest

    def begin_transit(self, *, source: str = "manual") -> ActiveRouteManifest:
        with self._lock:
            self._ensure_open_locked()
            if self._state == WorkflowState.EN_ROUTE:
                return self._manifest  # type: ignore[return-value]
            if self._state != WorkflowState.LOCKED_LOADED:
                raise InvalidTransition(
                    f"cannot start transit from {self._state.value}")
            previous = self._state
            self._state = WorkflowState.EN_ROUTE
            manifest = self._touch_manifest_locked(
                replace(self._manifest, updated_at=time.time()))
            self._manifest = manifest
            self._last_manifest = manifest
            self._save_active_locked()
            self._emit_locked(
                "workflow_transition", self.VIEW_HAULING,
                previous.value, self._state.value, manifest,
                reason=f"transit started ({source})")
            return manifest

    def complete_hauling_job(
            self, *, sale_price: float | None = None,
            location: str | None = None,
            source: str = "manual") -> ActiveRouteManifest:
        with self._lock:
            self._ensure_open_locked()
            self._require_active_locked()
            manifest = self._manifest
            assert manifest is not None
            price = (manifest.destination.demand_price
                     if sale_price is None else max(0.0, float(sale_price)))
            proceeds = price * manifest.remaining_scu
            now = time.time()
            final = replace(
                manifest,
                updated_at=now,
                current_location=(location or manifest.current_location
                                  or manifest.destination.name),
                completed_at=now,
                final_sale_price=price,
                final_proceeds=proceeds,
                realized_profit=proceeds - manifest.base_investment,
                actual_ppm=self._current_ppm(manifest, now),
            )
            previous = self._state
            self._wallet.settle(
                manifest.base_investment, proceeds, completed=True,
                cargo_lost_scu=manifest.cargo_lost_scu)
            self._state = WorkflowState.COMPLETED
            self._manifest = final
            self._last_manifest = final
            # Drop the active pointer before saving the settled wallet. If the
            # process dies between the two writes, startup refunds the stale
            # reservation instead of reserving the completed run twice.
            self._clear_active_locked()
            self._save_wallet_locked()
            self._stop_monitor_locked()
            self._emit_locked(
                "workflow_transition", self.VIEW_BROWSING,
                previous.value, self._state.value, final,
                reason=f"run completed ({source})")
            return final

    def abort_hauling_job(
            self, *, reason: str = "",
            salvage_proceeds: float = 0.0,
            location: str | None = None) -> ActiveRouteManifest:
        with self._lock:
            self._ensure_open_locked()
            self._require_active_locked()
            manifest = self._manifest
            assert manifest is not None
            proceeds = max(0.0, float(salvage_proceeds))
            now = time.time()
            final = replace(
                manifest,
                updated_at=now,
                current_location=(location or manifest.current_location),
                completed_at=now,
                final_sale_price=0.0,
                final_proceeds=proceeds,
                realized_profit=proceeds - manifest.base_investment,
                actual_ppm=self._current_ppm(manifest, now),
                abort_reason=reason or "aborted",
            )
            previous = self._state
            self._wallet.settle(
                manifest.base_investment, proceeds, completed=False,
                cargo_lost_scu=manifest.cargo_lost_scu)
            self._state = WorkflowState.ABORTED
            self._manifest = final
            self._last_manifest = final
            self._clear_active_locked()
            self._save_wallet_locked()
            self._stop_monitor_locked()
            self._emit_locked(
                "workflow_transition", self.VIEW_BROWSING,
                previous.value, self._state.value, final,
                reason=reason or "run aborted")
            return final

    def adjust_active_manifest(
            self, cargo_lost_scu: int, *,
            sale_price: float | None = None,
            now: float | None = None) -> ActiveRouteManifest:
        """Apply a mid-flight cargo loss and recalculate the live PPM yield."""
        lost = _int(cargo_lost_scu)
        if lost < 0:
            raise ValueError("cargo_lost_scu cannot be negative")
        with self._lock:
            self._ensure_open_locked()
            self._require_active_locked()
            manifest = self._manifest
            assert manifest is not None
            if lost > manifest.remaining_scu:
                raise ValueError(
                    f"cannot lose {lost} SCU; only {manifest.remaining_scu} remain")
            stamp = time.time() if now is None else float(now)
            remaining = manifest.remaining_scu - lost
            price = (manifest.destination.demand_price if sale_price is None
                     else max(0.0, float(sale_price)))
            gross = (price - manifest.purchase_price) * remaining
            updated = replace(
                manifest,
                updated_at=stamp,
                cargo_lost_scu=manifest.cargo_lost_scu + lost,
                gross_projected_profit=gross,
                actual_ppm=(max(0.0, gross)
                            / max(1.0, (stamp - manifest.created_at) / 60.0)),
            )
            self._manifest = updated
            self._last_manifest = updated
            self._save_active_locked()
            self._emit_locked(
                "manifest_adjusted", self.VIEW_HAULING,
                self._state.value, self._state.value, updated,
                reason=f"{lost} SCU lost",
                detail={"cargo_lost_scu": lost,
                        "remaining_scu": remaining})
            return updated

    def reset_to_browsing(self) -> None:
        with self._lock:
            self._ensure_open_locked()
            if self._state.active:
                raise InvalidTransition(
                    "cannot browse while a hauling run is active")
            previous = self._state
            self._state = WorkflowState.BROWSING
            self._manifest = None
            self._emit_locked(
                "workflow_transition", self.VIEW_BROWSING,
                previous.value, self._state.value, None,
                reason="returned to browsing")

    # -- game log integration -------------------------------------------
    def _on_game_log_event(self, event: GameLogEvent) -> None:
        with self._lock:
            if self._closed:
                return
            if event.kind == "pilot_seat":
                seated = bool(event.value)
                if (seated and self._state == WorkflowState.LOCKED_LOADED):
                    self.begin_transit(source="game.log")
                    return
                self._emit_locked(
                    "pilot_seat",
                    self.VIEW_HAULING if self._state.active
                    else self.VIEW_BROWSING,
                    self._state.value, self._state.value, self._manifest,
                    reason="pilot seat entered" if seated
                    else "pilot seat left",
                    detail={"seated": seated, "raw": event.raw})
                return
            if event.kind != "location":
                return
            location = clean_terminal_name(str(event.value)) or str(event.value)
            self._record_location_locked(location, source="game.log")
            if (self._state == WorkflowState.EN_ROUTE
                    and self._manifest is not None
                    and _norm(location) == _norm(
                        self._manifest.destination.name)):
                arrived = replace(
                    self._manifest,
                    current_location=location,
                    arrived_at=(self._manifest.arrived_at or time.time()),
                    updated_at=time.time())
                self._manifest = arrived
                self._last_manifest = arrived
                self._save_active_locked()
                self._emit_locked(
                    "destination_reached", self.VIEW_HAULING,
                    self._state.value, self._state.value, arrived,
                    reason="destination location detected",
                    detail={"location": location, "raw": event.raw})
                if self._auto_complete_on_arrival:
                    self.complete_hauling_job(
                        location=location, source="game.log")

    def _record_location_locked(self, location: str, *,
                                source: str = "manual") -> None:
        if not location:
            return
        if self._state.active and self._manifest is not None:
            manifest = replace(
                self._manifest,
                current_location=location,
                updated_at=time.time())
            self._manifest = manifest
            self._last_manifest = manifest
            self._save_active_locked()
            self._emit_locked(
                "location_changed", self.VIEW_HAULING,
                self._state.value, self._state.value, manifest,
                reason=f"location: {location}",
                detail={"location": location, "source": source})
        else:
            self._emit_locked(
                "location_changed", self.VIEW_BROWSING,
                self._state.value, self._state.value, None,
                reason=f"location: {location}",
                detail={"location": location, "source": source})

    # -- persistence and helpers ----------------------------------------
    def _ensure_open_locked(self) -> None:
        if self._closed:
            raise WorkflowError("workflow engine is closed")

    def _require_active_locked(self) -> None:
        if not self._state.active or self._manifest is None:
            raise NoActiveJob("no hauling run is active")

    def _touch_manifest_locked(
            self, manifest: ActiveRouteManifest) -> ActiveRouteManifest:
        if manifest is None:
            return manifest
        return replace(manifest, updated_at=time.time())

    def _current_ppm(self, manifest: ActiveRouteManifest,
                     now: float) -> float:
        elapsed = max(1.0, (now - manifest.created_at) / 60.0)
        return max(0.0, manifest.gross_projected_profit) / elapsed

    def _emit_locked(
            self, event_type: str, target_state: str, previous_state: str,
            state: str, manifest: ActiveRouteManifest | None,
            *, reason: str = "", detail: dict | None = None) -> WorkflowEvent:
        event = WorkflowEvent(
            event_type=event_type,
            state=state,
            previous_state=previous_state,
            target_state=target_state,
            manifest_data=manifest,
            wallet_data=self._wallet.to_dict(),
            timestamp=time.time(),
            reason=reason,
            detail=detail or {},
        )
        self._bus.emit(event)
        return event

    def _save_active_locked(self) -> None:
        if self._manifest is None:
            return
        _write_json(self._active_path, {
            "version": SCHEMA_VERSION,
            "state": self._state.value,
            "saved_at": time.time(),
            "manifest": self._manifest.to_dict(),
        })

    def _clear_active_locked(self) -> None:
        try:
            self._active_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("could not clear active_run.json")

    def _save_wallet_locked(self) -> None:
        _write_json(self._wallet_path, {
            "version": SCHEMA_VERSION,
            "wallet": self._wallet.to_dict(),
        })

    def _load_wallet(self, starting_capital: float | None) -> WalletLedger:
        payload = _read_json(self._wallet_path)
        if payload.get("wallet"):
            wallet = WalletLedger.from_dict(payload.get("wallet"))
            if starting_capital is not None and not wallet.capital_known:
                wallet.capital_known = True
                wallet.liquid_capital = max(0.0, float(starting_capital))
            return wallet
        if starting_capital is None:
            return WalletLedger()
        return WalletLedger(
            liquid_capital=max(0.0, float(starting_capital)),
            capital_known=True,
            updated_at=time.time(),
        )

    def _load_active_locked(self) -> None:
        payload = _read_json(self._active_path)
        if not payload:
            return
        try:
            state = WorkflowState(str(payload.get("state") or ""))
        except ValueError:
            return
        if not state.active:
            return
        try:
            manifest = ActiveRouteManifest.from_dict(
                payload.get("manifest") or {})
        except (TypeError, ValueError):
            logger.exception("ignoring corrupt active_run.json")
            self._clear_active_locked()
            return
        self._state = state
        self._manifest = manifest
        self._last_manifest = manifest

    def _reconcile_wallet_locked(self) -> None:
        """Repair a partial write between wallet reservation and active cache."""
        changed = False
        if self._manifest is not None and self._state.active:
            needed = self._manifest.base_investment
            if self._wallet.capital_known:
                if self._wallet.reserved_capital < needed - 1e-9:
                    gap = needed - self._wallet.reserved_capital
                    self._wallet.liquid_capital -= gap
                    self._wallet.reserved_capital = needed
                    changed = True
                elif self._wallet.reserved_capital > needed + 1e-9:
                    excess = self._wallet.reserved_capital - needed
                    self._wallet.liquid_capital += excess
                    self._wallet.reserved_capital = needed
                    changed = True
            elif abs(self._wallet.reserved_capital - needed) > 1e-9:
                self._wallet.reserved_capital = needed
                changed = True
        elif self._wallet.reserved_capital > 0:
            if self._wallet.capital_known:
                self._wallet.liquid_capital += self._wallet.reserved_capital
            self._wallet.reserved_capital = 0.0
            changed = True
        if changed:
            self._save_wallet_locked()

    def _start_monitor_locked(self) -> None:
        if not self._auto_start_log_monitor:
            return
        if self._monitor is None:
            self._monitor = self._monitor_factory(self._on_game_log_event)
        self._monitor.start()

    def _stop_monitor_locked(self) -> None:
        monitor = self._monitor
        self._monitor = None
        if monitor is not None:
            # Do not join while the engine lock is held. A log callback may be
            # waiting for this same lock.
            monitor.stop(wait=False)

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._save_wallet_locked()
            monitor = self._monitor
            self._monitor = None
        if monitor is not None:
            monitor.stop(wait=True)
        self._bus.close()

    def __enter__(self) -> "HaulingWorkflowEngine":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
