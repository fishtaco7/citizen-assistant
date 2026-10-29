"""Headless hauling-route optimizer.

The module has two deliberately separate halves:

* :class:`PlannerDataController` owns the UEX market/terminal lookup and the
  local ``routes_cache.json`` cache. It knows nothing about time or profit.
* :class:`RouteOptimizer` is pure arithmetic. It receives plain rows and a
  :class:`PlannerConfig`, then returns sorted :class:`PlannerRoute` objects.

Nothing here touches the game process. Data comes from the public UEX API or
the local cache only.

The UI is expected to run :func:`build_plan` on a worker thread because the
first refresh makes one request per commodity. Cached plans calculate in a
fraction of a second, but the same worker entry point is still used so the
hosting UI never blocks.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .config import config_dir
from .stations import clean_terminal_name

API = "https://api.uexcorp.uk/2.0"
USER_AGENT = "Citizen-Assistant/1.0 (personal overlay; +https://uexcorp.space)"
CACHE_NAME = "routes_cache.json"
SEED_NAME = "hauling_top_routes.json"
REFRESH_SECONDS = 15 * 60
FETCH_WORKERS = 5
MAX_RESULTS = 500

# A freighter's quantum drive speed varies, but 0.35 Gm/s is a useful middle
# ground for the route-planning view. Coordinates are not exposed by UEX, so
# the distance field (Gm) and system/orbit anchors are used instead.
DEFAULT_QT_SPEED_GM_S = 0.35

AUTO_LOAD_BASE_MIN = 1.0
AUTO_LOAD_MIN_PER_SCU = 1.0 / 240.0
MANUAL_LOAD_BASE_MIN = 2.0
MANUAL_LOAD_MIN_PER_SCU = 1.0 / 8.0
DOCK_MINUTES = 0.8
JUMP_MINUTES = 3.5

# Ground cities carry a real ascent/descent cost. Orbital stations do not.
ATMOSPHERE_PENALTY = {
    "area18": 5.5,
    "lorville": 5.0,
    "newbabbage": 4.5,
    "orison": 5.0,
    "levski": 4.0,
}
DEFAULT_GROUND_PENALTY = 3.0

SORT_KEYS = {
    "ppm": lambda route: -route.ppm,
    "profit": lambda route: -route.gross_profit,
    "time": lambda route: route.duration_minutes,
    "investment": lambda route: route.investment,
}


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value) -> int:
    return int(_num(value))


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)
    except OSError:
        pass


def _get(url: str, timeout: float = 30.0) -> dict:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    if payload.get("status") not in (None, "ok"):
        raise RuntimeError(payload.get("message") or str(payload.get("status")))
    return payload


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlannerConfig:
    """Inputs supplied by the hosting UI."""

    cargo_capacity: int = 32
    max_trading_capital: float = 0.0
    filter_auto_load_only: bool = False
    avoid_high_gravity: bool = False
    qt_speed_gm_s: float = DEFAULT_QT_SPEED_GM_S
    sort_by: str = "ppm"

    def clean(self) -> "PlannerConfig":
        return PlannerConfig(
            cargo_capacity=max(1, min(3000, int(self.cargo_capacity))),
            max_trading_capital=max(0.0, float(self.max_trading_capital)),
            filter_auto_load_only=bool(self.filter_auto_load_only),
            avoid_high_gravity=bool(self.avoid_high_gravity),
            qt_speed_gm_s=max(0.05, float(self.qt_speed_gm_s)),
            sort_by=self.sort_by if self.sort_by in SORT_KEYS else "ppm",
        )


@dataclass(frozen=True)
class StationInfo:
    display: str
    system: str = ""
    planet: str = ""
    moon: str = ""
    orbit: str = ""
    kind: str = ""
    ground: bool = False
    major: bool = False
    auto_load: bool = False
    high_gravity: bool = False


@dataclass(frozen=True)
class PlannerRoute:
    commodity: str
    buy_station: str
    sell_station: str
    path: tuple[str, ...]
    investment: float
    gross_profit: float
    duration_minutes: float
    ppm: float
    trip_scu: int
    per_scu: float
    distance_gm: float
    auto_load: bool
    high_gravity: bool
    stock_origin: int
    demand_destination: int
    load_minutes: float
    qt_minutes: float
    jump_minutes: float
    atmo_minutes: float
    buy_system: str = ""
    sell_system: str = ""
    buy_price: float = 0.0
    sell_price: float = 0.0
    buy_auto_load: bool = False
    sell_auto_load: bool = False
    buy_high_gravity: bool = False
    sell_high_gravity: bool = False
    buy_kind: str = ""
    sell_kind: str = ""
    buy_orbit: str = ""
    sell_orbit: str = ""

    @property
    def route_text(self) -> str:
        return " → ".join(self.path)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["path"] = list(self.path)
        data["route_text"] = self.route_text
        return data


@dataclass
class PlannerResult:
    routes: list[PlannerRoute]
    fetched: float = 0.0
    source: str = ""
    total_rows: int = 0
    fresh: bool = False
    error: str = ""

    @property
    def updated_text(self) -> str:
        if not self.fetched:
            return "NOT SYNCED"
        return time.strftime("%H:%M", time.localtime(self.fetched))


# ---------------------------------------------------------------------------
class PlannerDataController:
    """Fetches, caches and loads the raw route/terminal/commodity data."""

    def __init__(self, cache_path: Path | None = None,
                 seed_path: Path | None = None):
        self.cache_path = Path(cache_path) if cache_path is not None \
            else config_dir() / CACHE_NAME
        self.seed_path = Path(seed_path) if seed_path is not None \
            else config_dir() / SEED_NAME
        self.rows: list[dict] = []
        self.terminals: dict[str, dict] = {}
        self.commodities: list[dict] = []
        self.fetched = 0.0
        self.refresh_seconds = REFRESH_SECONDS
        self.source = ""
        self.error = ""

    # -- cache ---------------------------------------------------------
    def load(self, allow_seed: bool = True) -> bool:
        if self._apply(_read_json(self.cache_path), "planner cache"):
            return True
        if allow_seed and self._apply(_read_json(self.seed_path),
                                      "hauling cache"):
            # The existing HAULING cache has routes but not terminal names.
            # The optimizer falls back to the bundled station directory.
            self.terminals = {}
            self.commodities = []
            return True
        return False

    def _apply(self, data: dict, source: str) -> bool:
        rows = data.get("rows")
        if not isinstance(rows, list) or not rows:
            return False
        self.rows = [row for row in rows if isinstance(row, dict)]
        if not self.rows:
            return False
        self.fetched = _num(data.get("fetched"))
        self.refresh_seconds = _num(data.get("refresh_seconds")) \
            or REFRESH_SECONDS
        self.source = str(data.get("source") or source)
        terminals = data.get("terminals")
        self.terminals = terminals if isinstance(terminals, dict) else {}
        commodities = data.get("commodities")
        self.commodities = commodities if isinstance(commodities, list) else []
        self.error = ""
        return True

    @property
    def is_fresh(self) -> bool:
        return bool(self.rows and self.fetched
                    and time.time() - self.fetched < self.refresh_seconds)

    def ensure(self, force: bool = False,
               progress: Callable[[str, int, int], None] | None = None,
               cancelled: Callable[[], bool] | None = None) -> bool:
        """Load a usable cache, or fetch one when nothing is available."""
        if not force and self.load(allow_seed=True):
            if progress is not None:
                progress("Loaded cached routes", len(self.rows), len(self.rows))
            return True
        return self.refresh(progress=progress, cancelled=cancelled)

    # -- live refresh --------------------------------------------------
    def refresh(self, progress: Callable[[str, int, int], None] | None = None,
                cancelled: Callable[[], bool] | None = None) -> bool:
        if progress is not None:
            progress("Fetching commodities", 0, 0)
        try:
            commodity_rows = _get(f"{API}/commodities").get("data", [])
            commodities = [
                {
                    "id": _int(row.get("id")),
                    "name": row.get("name") or "",
                    "price_buy": _num(row.get("price_buy")),
                    "price_sell": _num(row.get("price_sell")),
                }
                for row in commodity_rows
                if _int(row.get("id"))
            ]
            if not commodities:
                raise RuntimeError("UEX returned no commodities")

            if progress is not None:
                progress("Fetching station locations", 0, 0)
            terminal_rows = _get(f"{API}/terminals").get("data", [])
            terminals = {
                str(_int(row.get("id"))): _compact_terminal(row)
                for row in terminal_rows if _int(row.get("id"))
            }

            total = len(commodities)
            done = 0
            collected: list[dict] = []
            failures: list[str] = []
            pool = ThreadPoolExecutor(max_workers=FETCH_WORKERS)
            futures = {
                pool.submit(
                    _get, f"{API}/commodities_routes?id_commodity={item['id']}"
                ): item for item in commodities
            }
            try:
                for future in as_completed(futures):
                    if cancelled is not None and cancelled():
                        for pending in futures:
                            pending.cancel()
                        break
                    item = futures[future]
                    done += 1
                    try:
                        payload = future.result()
                        for row in payload.get("data", []):
                            if isinstance(row, dict):
                                collected.append(row)
                    except Exception as exc:
                        failures.append(f"{item['name']}: {exc}")
                    if progress is not None:
                        progress("Optimizing market data", done, total)
            finally:
                pool.shutdown(wait=True, cancel_futures=True)

            if cancelled is not None and cancelled():
                return bool(self.rows)

            rows = _clean_rows(collected)
            if not rows:
                raise RuntimeError(
                    failures[0] if failures else "UEX returned no usable routes")

            self.rows = rows
            self.terminals = terminals
            self.commodities = commodities
            self.fetched = time.time()
            self.refresh_seconds = REFRESH_SECONDS
            self.source = "UEX API 2.0 · all commodities"
            self.error = ""
            _write_json(self.cache_path, {
                "version": 1,
                "fetched": self.fetched,
                "refresh_seconds": self.refresh_seconds,
                "source": self.source,
                "model": {
                    "distance_units": "Gm",
                    "qt_speed_gm_s": DEFAULT_QT_SPEED_GM_S,
                    "auto_load_base_min": AUTO_LOAD_BASE_MIN,
                    "auto_load_min_per_scu": AUTO_LOAD_MIN_PER_SCU,
                    "manual_load_base_min": MANUAL_LOAD_BASE_MIN,
                    "manual_load_min_per_scu": MANUAL_LOAD_MIN_PER_SCU,
                    "dock_minutes": DOCK_MINUTES,
                    "jump_minutes": JUMP_MINUTES,
                },
                "commodities": commodities,
                "terminals": terminals,
                "rows": rows,
            })
            if progress is not None:
                progress("Planner ready", total, total)
            return True
        except Exception as exc:
            self.error = str(exc) or "UEX unavailable"
            if progress is not None:
                progress("Planner offline — using cached routes", 0, 0)
            return bool(self.rows)


def _compact_terminal(row: dict) -> dict:
    city = _int(row.get("id_city"))
    station = _int(row.get("id_space_station"))
    outpost = _int(row.get("id_outpost"))
    ground = bool(city or outpost)
    major = bool(city or station)
    if city:
        kind = "city"
    elif station:
        kind = "station"
    elif outpost:
        kind = "outpost"
    else:
        kind = ""
    display = (row.get("displayname") or row.get("name") or "").strip()
    auto = bool(_int(row.get("is_auto_load")))
    if not auto and major and _int(row.get("has_freight_elevator")):
        auto = True
    system = (row.get("star_system_name") or "").strip()
    planet = (row.get("planet_name") or "").strip()
    moon = (row.get("moon_name") or "").strip()
    orbit = (row.get("orbit_name") or "").strip()
    return {
        "display": display,
        "system": system,
        "planet": planet,
        "moon": moon,
        "orbit": orbit,
        # UEX does not expose x/y/z coordinates. These navigation anchors plus
        # the route distance (Gm) are the stable location data used for QT.
        "coordinates": {
            "system": system,
            "planet": planet,
            "moon": moon,
            "orbit": orbit,
        },
        "kind": kind,
        "ground": ground,
        "major": major,
        "auto": auto,
        "elevator": bool(_int(row.get("has_freight_elevator"))),
        # Cities are high-gravity/atmospheric. An outpost on a moon is a
        # low-gravity location even though it still needs an atmosphere timer.
        "high_gravity": bool(city) or (bool(outpost) and not moon),
    }


def _clean_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    cleaned: list[dict] = []
    for row in rows:
        buy = _num(row.get("price_origin"))
        sell = _num(row.get("price_destination"))
        available = _int(row.get("scu_reachable")) or _int(row.get("scu_origin"))
        if sell <= buy or available <= 0:
            continue
        key = (
            _int(row.get("id_commodity")),
            _int(row.get("id_terminal_origin")),
            _int(row.get("id_terminal_destination")),
            buy,
            sell,
        )
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(row)
    return cleaned


# ---------------------------------------------------------------------------
class RouteOptimizer:
    """Pure PPM maths; no network, filesystem, threads or widgets."""

    def __init__(self, terminals: dict[str, dict] | None = None):
        self.terminals = terminals or {}

    def optimize(self, rows: list[dict],
                 config: PlannerConfig) -> list[PlannerRoute]:
        cfg = config.clean()
        best: dict[tuple, PlannerRoute] = {}
        for row in rows:
            route = self._evaluate(row, cfg)
            if route is None:
                continue
            key = (
                route.commodity.casefold(),
                route.buy_station.casefold(),
                route.sell_station.casefold(),
            )
            existing = best.get(key)
            if existing is None or route.ppm > existing.ppm:
                best[key] = route
        routes = list(best.values())
        routes.sort(key=SORT_KEYS[cfg.sort_by])
        return routes[:MAX_RESULTS]

    # ------------------------------------------------------------------
    def _evaluate(self, row: dict, cfg: PlannerConfig) -> PlannerRoute | None:
        buy_price = _num(row.get("price_origin"))
        sell_price = _num(row.get("price_destination"))
        per_scu = sell_price - buy_price
        if per_scu <= 0:
            return None

        stock = _int(row.get("scu_reachable")) or _int(row.get("scu_origin"))
        if stock <= 0:
            return None
        trip_scu = min(cfg.cargo_capacity, stock)
        if cfg.max_trading_capital > 0:
            if buy_price > 0:
                affordable = int(cfg.max_trading_capital // buy_price)
            else:
                affordable = trip_scu
            if affordable <= 0:
                return None
            trip_scu = min(trip_scu, affordable)
        if trip_scu <= 0:
            return None

        origin = self._station(row, "origin")
        destination = self._station(row, "destination")
        if (origin.display.casefold() == destination.display.casefold()
                and _int(row.get("id_terminal_origin"))
                == _int(row.get("id_terminal_destination"))):
            return None

        auto_load = origin.auto_load and destination.auto_load
        high_gravity = origin.high_gravity or destination.high_gravity
        if cfg.filter_auto_load_only and not auto_load:
            return None
        if cfg.avoid_high_gravity and high_gravity:
            return None

        investment = buy_price * trip_scu
        gross = per_scu * trip_scu
        load_minutes = (
            _load_minutes(origin, trip_scu)
            + _load_minutes(destination, trip_scu))
        qt_minutes = _qt_minutes(_num(row.get("distance")),
                                 cfg.qt_speed_gm_s)
        jump_minutes = _jump_minutes(origin.system, destination.system)
        atmo_minutes = (
            _atmo_minutes(origin) + _atmo_minutes(destination))
        duration = max(1.0, load_minutes + qt_minutes + jump_minutes
                       + atmo_minutes + DOCK_MINUTES)
        ppm = gross / duration
        path = _waypoints(origin, destination, jump_minutes)

        return PlannerRoute(
            commodity=str(row.get("commodity_name") or "Unknown"),
            buy_station=origin.display,
            sell_station=destination.display,
            path=tuple(path),
            investment=investment,
            gross_profit=gross,
            duration_minutes=duration,
            ppm=ppm,
            trip_scu=trip_scu,
            per_scu=per_scu,
            distance_gm=_num(row.get("distance")),
            auto_load=auto_load,
            high_gravity=high_gravity,
            stock_origin=stock,
            demand_destination=_int(row.get("scu_destination")),
            load_minutes=load_minutes,
            qt_minutes=qt_minutes,
            jump_minutes=jump_minutes,
            atmo_minutes=atmo_minutes,
            buy_system=origin.system,
            sell_system=destination.system,
            buy_price=buy_price,
            sell_price=sell_price,
            buy_auto_load=origin.auto_load,
            sell_auto_load=destination.auto_load,
            buy_high_gravity=origin.high_gravity,
            sell_high_gravity=destination.high_gravity,
            buy_kind=origin.kind,
            sell_kind=destination.kind,
            buy_orbit=origin.orbit,
            sell_orbit=destination.orbit,
        )

    def _station(self, row: dict, side: str) -> StationInfo:
        terminal_id = _int(row.get(f"id_terminal_{side}"))
        meta = self.terminals.get(str(terminal_id))
        raw_name = row.get(f"{side}_terminal_name") or ""
        if meta:
            display = (meta.get("display") or "").strip() \
                or clean_terminal_name(raw_name)
            ground = bool(meta.get("ground"))
            kind = str(meta.get("kind") or "")
            moon = str(meta.get("moon") or "")
            if kind:
                high_gravity = kind == "city" or (kind == "outpost" and not moon)
            else:
                high_gravity = bool(meta.get("high_gravity", ground))
            return StationInfo(
                display=display,
                system=str(meta.get("system") or ""),
                planet=str(meta.get("planet") or ""),
                moon=moon,
                orbit=str(meta.get("orbit") or ""),
                kind=kind,
                ground=ground,
                major=bool(meta.get("major")),
                auto_load=bool(meta.get("auto")),
                high_gravity=high_gravity,
            )

        ground = bool(_int(row.get(f"is_on_ground_{side}")))
        elevator = bool(_int(row.get(f"has_freight_elevator_{side}")))
        station = bool(_int(row.get(f"is_space_station_{side}")))
        return StationInfo(
            display=clean_terminal_name(raw_name) or "Unknown",
            system=str(row.get(f"{side}_star_system_name") or ""),
            planet=str(row.get(f"{side}_planet_name") or ""),
            moon="",
            orbit=str(row.get(f"{side}_orbit_name") or ""),
            kind="outpost" if ground else ("station" if station else ""),
            ground=ground,
            major=station or not ground,
            auto_load=elevator and not ground,
            high_gravity=ground,
        )


def _load_minutes(station: StationInfo, trip_scu: int) -> float:
    if station.auto_load:
        return AUTO_LOAD_BASE_MIN + trip_scu * AUTO_LOAD_MIN_PER_SCU
    return MANUAL_LOAD_BASE_MIN + trip_scu * MANUAL_LOAD_MIN_PER_SCU


def _atmo_minutes(station: StationInfo) -> float:
    if not station.ground:
        return 0.25
    key = _norm(station.display)
    for city, penalty in ATMOSPHERE_PENALTY.items():
        if city in key:
            return penalty
    return DEFAULT_GROUND_PENALTY if station.kind == "outpost" else 4.0


def _qt_minutes(distance_gm: float, speed_gm_s: float) -> float:
    if distance_gm <= 0:
        return 0.3
    return 0.8 + distance_gm / max(0.05, speed_gm_s) / 60.0


def _jump_minutes(origin_system: str, destination_system: str) -> float:
    if (origin_system and destination_system
            and origin_system.casefold() != destination_system.casefold()):
        return JUMP_MINUTES
    return 0.0


def _waypoints(origin: StationInfo, destination: StationInfo,
               jump_minutes: float) -> list[str]:
    path: list[str] = [origin.display]
    if origin.ground and origin.orbit:
        marker = f"{origin.orbit} orbit"
        if _norm(marker) not in _norm(origin.display):
            path.append(marker)
    if jump_minutes > 0:
        start = origin.system or "Origin"
        end = destination.system or "Destination"
        path.append(f"{start} → {end} jump")
    if destination.ground and destination.orbit:
        marker = f"{destination.orbit} orbit"
        if _norm(marker) not in _norm(destination.display):
            path.append(marker)
    path.append(destination.display)

    result: list[str] = []
    seen: set[str] = set()
    for point in path:
        key = _norm(point)
        if key and key not in seen:
            result.append(point)
            seen.add(key)
    return result


# ---------------------------------------------------------------------------
def build_plan(config: PlannerConfig,
               controller: PlannerDataController | None = None,
               force_refresh: bool = False,
               progress: Callable[[str, int, int], None] | None = None,
               cancelled: Callable[[], bool] | None = None) -> PlannerResult:
    """Load data, optimize, and return a structured result.

    Call this from a worker thread. The function is deliberately UI-free.
    """
    controller = controller or PlannerDataController()
    controller.ensure(force=force_refresh, progress=progress,
                      cancelled=cancelled)
    routes = []
    if controller.rows:
        routes = RouteOptimizer(controller.terminals).optimize(
            controller.rows, config)
    return PlannerResult(
        routes=routes,
        fetched=controller.fetched,
        source=controller.source,
        total_rows=len(controller.rows),
        fresh=controller.is_fresh,
        error=controller.error,
    )


__all__ = [
    "PlannerConfig",
    "PlannerDataController",
    "PlannerResult",
    "PlannerRoute",
    "RouteOptimizer",
    "StationInfo",
    "build_plan",
]
