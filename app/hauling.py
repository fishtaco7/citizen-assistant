"""Hauling data: live UEX commodity routes + a community mission snapshot.

Two deliberately separate sources:

* Trade routes come from the public UEX API 2.0 (``commodities_routes``),
  which returns buy -> sell pairs, prices, stock, distance and container
  sizes. No key is needed. Results are cached per commodity and only one
  commodity is kept on disk at a time.
* Cargo-mission boards are not exposed by any public API. The contracts tab
  therefore uses a snapshot of the community-maintained SCHaulingPlanner
  mission list (MIT), bundled under ``app/data`` and refreshed from GitHub
  when possible. It is explicitly a reference, not a live feed.

Nothing here touches the game process.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .config import config_dir
from .version import VERSION

API = "https://api.uexcorp.uk/2.0"
USER_AGENT = (f"Citizen-Assistant/{VERSION} "
              "(+https://github.com/fishtaco7/citizen-assistant)")

COMMODITIES_TTL = 12 * 3600
ROUTES_TTL = 15 * 60
TOP_ROUTES_TTL = ROUTES_TTL
TOP_FETCH_WORKERS = 5
TERMINALS_TTL = 7 * 24 * 3600
MISSIONS_TTL = 24 * 3600

MISSIONS_URL = ("https://raw.githubusercontent.com/"
                "FSCSOFT-Official/SCHaulingPlanner/main/missions.js")
BUNDLED_MISSIONS = Path(__file__).resolve().parent / "data" / "hauling_missions.json"


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value) -> int:
    return int(_num(value))


def _bool(value) -> bool:
    return bool(_int(value))


def _cache_path(name: str) -> Path:
    return config_dir() / name


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
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)
    except OSError:
        pass


def _get(url: str, timeout: float = 25.0) -> dict:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    if payload.get("status") not in (None, "ok"):
        raise RuntimeError(payload.get("message") or payload.get("status"))
    return payload


def _get_text(url: str, timeout: float = 25.0) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "text/plain, */*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


# ---------------------------------------------------------------------------
@dataclass
class Commodity:
    id: int
    name: str
    kind: str = ""
    price_buy: float = 0.0
    price_sell: float = 0.0


@dataclass
class Route:
    commodity_id: int
    commodity: str
    slug: str
    origin: str
    destination: str
    origin_system: str = ""
    origin_planet: str = ""
    origin_orbit: str = ""
    origin_code: str = ""
    destination_system: str = ""
    destination_planet: str = ""
    destination_orbit: str = ""
    destination_code: str = ""
    origin_id: int = 0
    destination_id: int = 0
    price_origin: float = 0.0
    price_destination: float = 0.0
    margin: float = 0.0
    roi: float = 0.0
    stock_origin: int = 0
    stock_destination: int = 0
    reachable: int = 0
    investment: float = 0.0
    profit: float = 0.0
    distance: float = 0.0
    score: float = 0.0
    containers_origin: str = ""
    containers_destination: str = ""
    flags_origin: dict = field(default_factory=dict)
    flags_destination: dict = field(default_factory=dict)

    @property
    def per_scu(self) -> float:
        return self.price_destination - self.price_origin

    @property
    def available(self) -> int:
        """Best estimate of how much can actually be bought for this run."""
        if self.reachable > 0:
            return self.reachable
        return self.stock_origin

    def stock_ok(self, capacity: int) -> bool:
        return self.available >= int(capacity)

    def trip_scu(self, capacity: int) -> int:
        available = self.available if self.available > 0 else capacity
        return max(0, min(int(capacity), int(available)))

    def trip_profit(self, capacity: int) -> float:
        return self.trip_scu(capacity) * self.per_scu

    def trip_cost(self, capacity: int) -> float:
        return self.trip_scu(capacity) * self.price_origin

    @property
    def origin_place(self) -> str:
        return self.origin_planet or self.origin_orbit or self.origin_system

    @property
    def destination_place(self) -> str:
        return (self.destination_planet or self.destination_orbit
                or self.destination_system)


_SHIP_FLAGS = (
    ("freight", "has_freight_elevator"),
    ("docking", "has_docking_port"),
    ("loading", "has_loading_dock"),
    ("cargo", "has_cargo_center"),
    ("quantum", "has_quantum_marker"),
    ("monitored", "is_monitored"),
    ("ground", "is_on_ground"),
    ("station", "is_space_station"),
)


def _route_from_row(row: dict) -> Route:
    def flags(side: str) -> dict:
        return {name: _bool(row.get(f"{key}_{side}"))
                for name, key in _SHIP_FLAGS}

    return Route(
        commodity_id=_int(row.get("id_commodity")),
        commodity=row.get("commodity_name") or "Unknown",
        slug=row.get("commodity_slug") or "",
        origin=row.get("origin_terminal_name") or "Unknown terminal",
        destination=row.get("destination_terminal_name") or "Unknown terminal",
        origin_system=row.get("origin_star_system_name") or "",
        origin_planet=row.get("origin_planet_name") or "",
        origin_orbit=row.get("origin_orbit_name") or "",
        origin_code=row.get("origin_terminal_code") or "",
        destination_system=row.get("destination_star_system_name") or "",
        destination_planet=row.get("destination_planet_name") or "",
        destination_orbit=row.get("destination_orbit_name") or "",
        destination_code=row.get("destination_terminal_code") or "",
        origin_id=_int(row.get("id_terminal_origin")),
        destination_id=_int(row.get("id_terminal_destination")),
        price_origin=_num(row.get("price_origin")),
        price_destination=_num(row.get("price_destination")),
        margin=_num(row.get("price_margin")),
        roi=_num(row.get("price_roi")),
        stock_origin=_int(row.get("scu_origin")),
        stock_destination=_int(row.get("scu_destination")),
        reachable=_int(row.get("scu_reachable")),
        investment=_num(row.get("investment")),
        profit=_num(row.get("profit")),
        distance=_num(row.get("distance")),
        score=_num(row.get("score")),
        containers_origin=row.get("container_sizes_origin") or "",
        containers_destination=row.get("container_sizes_destination") or "",
        flags_origin=flags("origin"),
        flags_destination=flags("destination"),
    )


def _route_to_row(route: Route) -> dict:
    row = {
        "id_commodity": route.commodity_id,
        "commodity_name": route.commodity,
        "commodity_slug": route.slug,
        "origin_terminal_name": route.origin,
        "destination_terminal_name": route.destination,
        "origin_star_system_name": route.origin_system,
        "origin_planet_name": route.origin_planet,
        "origin_orbit_name": route.origin_orbit,
        "origin_terminal_code": route.origin_code,
        "destination_star_system_name": route.destination_system,
        "destination_planet_name": route.destination_planet,
        "destination_orbit_name": route.destination_orbit,
        "destination_terminal_code": route.destination_code,
        "id_terminal_origin": route.origin_id,
        "id_terminal_destination": route.destination_id,
        "price_origin": route.price_origin,
        "price_destination": route.price_destination,
        "price_margin": route.margin,
        "price_roi": route.roi,
        "scu_origin": route.stock_origin,
        "scu_destination": route.stock_destination,
        "scu_reachable": route.reachable,
        "investment": route.investment,
        "profit": route.profit,
        "distance": route.distance,
        "score": route.score,
        "container_sizes_origin": route.containers_origin,
        "container_sizes_destination": route.containers_destination,
    }
    for name, key in _SHIP_FLAGS:
        row[f"{key}_origin"] = int(route.flags_origin.get(name, False))
        row[f"{key}_destination"] = int(route.flags_destination.get(name, False))
    return row


# ---------------------------------------------------------------------------
@dataclass
class Delivery:
    scu: int
    cargo: str
    destination: str
    pickups: list[str] = field(default_factory=list)


@dataclass
class HaulingMission:
    id: int
    reward: int
    contractor: str
    rank: str
    mission_type: str
    system: str
    scu: int
    rate: float
    locations: list[str] = field(default_factory=list)
    pickups: list[str] = field(default_factory=list)
    destinations: list[str] = field(default_factory=list)
    deliveries: list[Delivery] = field(default_factory=list)
    source_file: str = ""

    def over_by(self, capacity: int) -> int:
        return max(0, int(self.scu) - int(capacity))

    def fits(self, capacity: int) -> bool:
        return self.scu <= capacity

    @property
    def cargo_types(self) -> list[str]:
        seen: list[str] = []
        for delivery in self.deliveries:
            if delivery.cargo and delivery.cargo not in seen:
                seen.append(delivery.cargo)
        return seen

    @property
    def route_summary(self) -> str:
        origin = self.pickups[0] if self.pickups else "?"
        if len(self.destinations) == 1:
            return f"{origin} → {self.destinations[0]}"
        if self.destinations:
            return f"{origin} → {len(self.destinations)} stops"
        return origin


@dataclass
class MissionBoard:
    missions: list[HaulingMission] = field(default_factory=list)
    captured: str = ""
    source: str = ""
    source_url: str = ""
    license: str = ""
    fetched_at: float = 0.0
    stale: bool = False

    @property
    def loaded(self) -> bool:
        return bool(self.missions)


def _mission_from_dict(row: dict) -> HaulingMission:
    # Accept both our compact bundled shape and the upstream SCHaulingPlanner
    # shape (totalSCU, rewardPerSCU, pickupLocations, missionType, sourceFile).
    return HaulingMission(
        id=_int(row.get("id")),
        reward=_int(row.get("reward")),
        contractor=row.get("contractor") or "",
        rank=row.get("rank") or "",
        mission_type=row.get("type") or row.get("missionType") or "",
        system=row.get("system") or "",
        scu=_int(row.get("scu") if row.get("scu") is not None
                 else row.get("totalSCU")),
        rate=_num(row.get("rate") if row.get("rate") is not None
                  else row.get("rewardPerSCU")),
        locations=list(row.get("locations") or []),
        pickups=list(row.get("pickups") or row.get("pickupLocations") or []),
        destinations=list(row.get("destinations") or []),
        deliveries=[Delivery(
            scu=_int(delivery.get("scu")),
            cargo=delivery.get("cargo") or "",
            destination=delivery.get("destination") or "",
            pickups=list(delivery.get("pickups") or []),
        ) for delivery in (row.get("deliveries") or [])],
        source_file=row.get("source_file") or row.get("sourceFile") or "",
    )


def _board_from_payload(payload: dict, fetched_at: float = 0.0,
                        stale: bool = False) -> MissionBoard:
    meta = payload.get("meta") or {}
    return MissionBoard(
        missions=[_mission_from_dict(row) for row in payload.get("missions") or []],
        captured=str(meta.get("captured") or ""),
        source=str(meta.get("source") or ""),
        source_url=str(meta.get("source_url") or ""),
        license=str(meta.get("license") or ""),
        fetched_at=fetched_at,
        stale=stale,
    )


def _parse_missions_js(raw: str) -> dict:
    """Pull the JSON object out of the generated ``window.MISSION_DATA`` file."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("mission snapshot is not valid JSON")
    payload = json.loads(raw[start:end + 1])
    return _normalise_missions(payload)


def _normalise_missions(payload: dict) -> dict:
    """Convert upstream mission data into the shape used by the panel/cache."""
    source = payload.get("missions") or []
    missions = []
    dates = []
    for index, row in enumerate(source):
        source_file = row.get("sourceFile") or row.get("source_file") or ""
        date = ""
        parts = source_file.split("_")
        if len(parts) > 1:
            date = parts[1].replace(".", "-")
        if date:
            dates.append(date)
        deliveries = []
        for delivery in row.get("deliveries") or []:
            deliveries.append({
                "scu": _int(delivery.get("scu")),
                "cargo": delivery.get("cargo") or "",
                "pickups": list(delivery.get("pickups") or []),
                "destination": delivery.get("destination") or "",
            })
        missions.append({
            "id": index,
            "reward": _int(row.get("reward")),
            "contractor": row.get("contractor") or "",
            "rank": row.get("rank") or "",
            "type": row.get("missionType") or row.get("type") or "",
            "system": row.get("system") or "",
            "scu": _int(row.get("totalSCU")
                        if row.get("totalSCU") is not None else row.get("scu")),
            "rate": round(_num(row.get("rewardPerSCU")
                               if row.get("rewardPerSCU") is not None
                               else row.get("rate")), 2),
            "locations": list(row.get("location") or row.get("locations") or []),
            "pickups": list(row.get("pickupLocations") or row.get("pickups") or []),
            "destinations": list(row.get("destinations") or []),
            "deliveries": deliveries,
            "source_file": source_file,
        })

    meta = payload.get("meta") or {}
    captured = dates[-1] if dates else str(meta.get("captured") or "")
    return {
        "meta": {
            "source": meta.get("source") or "FSCSOFT-Official/SCHaulingPlanner",
            "source_url": meta.get("source_url") or (
                "https://github.com/FSCSOFT-Official/SCHaulingPlanner"),
            "license": meta.get("license") or "MIT",
            "captured": captured,
            "total": len(missions),
        },
        "missions": missions,
    }


# ---------------------------------------------------------------------------
class Hauling:
    """Network + cache client used by the HAULING panel (Qt-independent)."""

    def __init__(self):
        self.last_error = ""
        self.commodities_error = ""
        self.routes_error = ""
        self.top_routes_error = ""
        self.missions_error = ""
        self._commodities: list[Commodity] = []
        self._commodities_at = 0.0
        self._routes: dict[int, tuple[float, list[Route]]] = {}
        self._top_routes: list[Route] = []
        self._top_routes_at = 0.0
        self._board: MissionBoard | None = None
        self._known_terminals: set[int] = set()
        self._major_terminals: set[int] = set()
        self._terminals_at = 0.0
        self.terminals_error = ""
        self._load_commodity_cache()
        self._load_route_cache()
        self._load_top_routes_cache()
        self._load_terminal_cache()

    # -- catalog -------------------------------------------------------
    @property
    def cached_commodities(self) -> list[Commodity]:
        return list(self._commodities)

    def _load_commodity_cache(self) -> None:
        cached = _read_json(_cache_path("hauling_commodities.json"))
        rows = cached.get("rows") or []
        if rows:
            self._commodities = [Commodity(
                id=_int(row.get("id")), name=row.get("name") or "",
                kind=row.get("kind") or "",
                price_buy=_num(row.get("price_buy")),
                price_sell=_num(row.get("price_sell")),
            ) for row in rows]
            self._commodities_at = _num(cached.get("fetched"))

    def commodities(self, force: bool = False) -> list[Commodity]:
        fresh = time.time() - self._commodities_at < COMMODITIES_TTL
        if self._commodities and fresh and not force:
            return self._commodities
        try:
            rows = _get(f"{API}/commodities").get("data", [])
            items = [Commodity(
                id=_int(row.get("id")), name=row.get("name") or "",
                kind=row.get("kind") or "",
                price_buy=_num(row.get("price_buy")),
                price_sell=_num(row.get("price_sell")),
            ) for row in rows
                if row.get("is_available_live")
                and row.get("is_buyable") and row.get("is_sellable")
                and _num(row.get("price_buy")) > 0
                and _num(row.get("price_sell")) > 0]
            items.sort(key=lambda item: item.name.casefold())
            if items:
                self._commodities = items
                self._commodities_at = time.time()
                self.last_error = ""
                self.commodities_error = ""
                _write_json(_cache_path("hauling_commodities.json"), {
                    "fetched": self._commodities_at,
                    "rows": [{"id": item.id, "name": item.name,
                              "kind": item.kind, "price_buy": item.price_buy,
                              "price_sell": item.price_sell}
                             for item in items],
                })
            return self._commodities
        except Exception as exc:
            self.last_error = str(exc) or "UEX unavailable"
            self.commodities_error = self.last_error
            return self._commodities

    # -- terminals -----------------------------------------------------
    def _load_terminal_cache(self) -> None:
        cached = _read_json(_cache_path("hauling_terminals.json"))
        entries = cached.get("terminals") or []
        if entries:
            self._terminals_at = _num(cached.get("fetched"))
            self._known_terminals = {_int(row.get("id")) for row in entries}
            self._major_terminals = {
                _int(row.get("id")) for row in entries
                if row.get("major")
            }

    def terminals(self, force: bool = False) -> bool:
        """Load terminal metadata used to distinguish cities/stations from outposts."""
        if (self._known_terminals
                and time.time() - self._terminals_at < TERMINALS_TTL
                and not force):
            return True
        try:
            rows = _get(f"{API}/terminals").get("data", [])
            known: set[int] = set()
            major: set[int] = set()
            compact = []
            for row in rows:
                tid = _int(row.get("id"))
                if not tid:
                    continue
                known.add(tid)
                # A city terminal or an orbital station is a large, safe
                # landing zone. Outposts have neither id_city nor
                # id_space_station.
                is_major = bool(row.get("id_city") or row.get("id_space_station"))
                if is_major:
                    major.add(tid)
                compact.append({"id": tid, "major": int(is_major)})
            if known:
                self._known_terminals = known
                self._major_terminals = major
                self._terminals_at = time.time()
                self.terminals_error = ""
                _write_json(_cache_path("hauling_terminals.json"), {
                    "fetched": self._terminals_at,
                    "terminals": compact,
                })
            return bool(known)
        except Exception as exc:
            self.terminals_error = str(exc) or "terminal data unavailable"
            return bool(self._known_terminals)

    def is_major_terminal(self, terminal_id: int) -> bool | None:
        """True/False when terminal metadata is known, None when it is not."""
        terminal_id = int(terminal_id or 0)
        if terminal_id in self._major_terminals:
            return True
        if terminal_id in self._known_terminals:
            return False
        return None

    # -- routes --------------------------------------------------------
    def _load_route_cache(self) -> None:
        cached = _read_json(_cache_path("hauling_routes.json"))
        commodity = _int(cached.get("commodity_id"))
        rows = cached.get("rows") or []
        if commodity and rows:
            routes = [_route_from_row(row) for row in rows]
            self._routes[commodity] = (_num(cached.get("fetched")), routes)

    def routes(self, commodity_id: int, force: bool = False,
               disk: bool = True) -> list[Route]:
        commodity_id = int(commodity_id)
        cached = self._routes.get(commodity_id)
        if cached and not force and time.time() - cached[0] < ROUTES_TTL:
            return cached[1]
        try:
            query = urllib.parse.urlencode({"id_commodity": commodity_id})
            rows = _get(f"{API}/commodities_routes?{query}").get("data", [])
            routes = [_route_from_row(row) for row in rows]
            routes = [route for route in routes if route.per_scu > 0]
            if routes:
                now = time.time()
                self._routes[commodity_id] = (now, routes)
                self.last_error = ""
                self.routes_error = ""
                if disk:
                    _write_json(_cache_path("hauling_routes.json"), {
                        "commodity_id": commodity_id,
                        "fetched": now,
                        "rows": [_route_to_row(route) for route in routes],
                    })
            return routes
        except Exception as exc:
            self.last_error = str(exc) or "UEX unavailable"
            self.routes_error = self.last_error
            return cached[1] if cached else []

    def has_routes(self, commodity_id: int) -> bool:
        return int(commodity_id) in self._routes

    def cached_routes(self, commodity_id: int) -> list[Route]:
        return list(self._routes.get(int(commodity_id), (0.0, []))[1])

    def routes_updated(self, commodity_id: int) -> float:
        cached = self._routes.get(int(commodity_id))
        return cached[0] if cached else 0.0

    # -- all-commodity top routes -------------------------------------
    def _load_top_routes_cache(self) -> None:
        cached = _read_json(_cache_path("hauling_top_routes.json"))
        rows = cached.get("rows") or []
        if rows:
            self._top_routes = [_route_from_row(row) for row in rows]
            self._top_routes_at = _num(cached.get("fetched"))

    def has_top_routes(self) -> bool:
        return bool(self._top_routes)

    def cached_top_routes(self) -> list[Route]:
        return list(self._top_routes)

    def top_routes_updated(self) -> float:
        return self._top_routes_at

    def top_routes(self, force: bool = False, progress=None,
                   cancelled=None) -> list[Route]:
        """Fetch every tradable commodity and return the combined route pool.

        UEX only exposes routes filtered by commodity, so a global "best route"
        view requires one request per commodity. Five workers keep the first
        scan around 20 seconds; the result is cached as one aggregate file.
        """
        fresh = (self._top_routes
                 and time.time() - self._top_routes_at < TOP_ROUTES_TTL)
        if fresh and not force:
            self.top_routes_error = ""
            return self._top_routes

        items = self.commodities()
        if not items:
            self.top_routes_error = (self.commodities_error
                                     or "commodity list unavailable")
            return self._top_routes

        total = len(items)
        done = 0
        results: list[Route] = []
        failures: list[str] = []
        interrupted = False

        pool = ThreadPoolExecutor(max_workers=TOP_FETCH_WORKERS)
        futures = {
            pool.submit(self.routes, item.id, force, False): item
            for item in items
        }
        try:
            for future in as_completed(futures):
                if cancelled is not None and cancelled():
                    interrupted = True
                    for pending in futures:
                        pending.cancel()
                    break
                item = futures[future]
                done += 1
                try:
                    results.extend(future.result())
                except Exception as exc:
                    failures.append(f"{item.name}: {exc}")
                if progress is not None:
                    try:
                        progress(done, total)
                    except Exception:
                        pass
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        if interrupted:
            return self._top_routes

        # A zero-stock route cannot be run; leaving it in the global list only
        # pushes pointless "profit" to the top of the default view.
        seen: set[tuple] = set()
        routes: list[Route] = []
        for route in results:
            if route.available <= 0:
                continue
            key = (route.commodity_id, route.origin_id, route.destination_id,
                   route.price_origin, route.price_destination)
            if key in seen:
                continue
            seen.add(key)
            routes.append(route)

        if routes:
            self._top_routes = routes
            self._top_routes_at = time.time()
            self.top_routes_error = ""
            self.last_error = ""
            _write_json(_cache_path("hauling_top_routes.json"), {
                "fetched": self._top_routes_at,
                "rows": [_route_to_row(route) for route in routes],
            })
        else:
            if failures:
                self.top_routes_error = failures[0]
            elif self.routes_error:
                self.top_routes_error = self.routes_error
            elif not self._top_routes:
                self.top_routes_error = "UEX returned no runnable routes"
        return self._top_routes

    # -- contracts -----------------------------------------------------
    def _load_missions(self) -> MissionBoard:
        if self._board is None:
            payload = _read_json(BUNDLED_MISSIONS)
            self._board = _board_from_payload(payload)
            cached = _read_json(_cache_path("hauling_missions.json"))
            if cached.get("missions"):
                cached_at = _num(cached.get("fetched"))
                if cached_at > self._board.fetched_at:
                    self._board = _board_from_payload(
                        _normalise_missions(cached), cached_at)
        return self._board

    def missions(self, force: bool = False, refresh: bool = True) -> MissionBoard:
        self._load_missions()
        if not refresh:
            return self._board

        fresh = (self._board.fetched_at
                 and time.time() - self._board.fetched_at < MISSIONS_TTL)
        if fresh and not force:
            return self._board

        try:
            payload = _parse_missions_js(_get_text(MISSIONS_URL))
            now = time.time()
            board = _board_from_payload(payload, now)
            if board.missions:
                self._board = board
                self.last_error = ""
                self.missions_error = ""
                _write_json(_cache_path("hauling_missions.json"), {
                    "meta": payload.get("meta") or {},
                    "missions": payload.get("missions") or [],
                    "fetched": now,
                })
            return self._board
        except Exception as exc:
            self.last_error = str(exc) or "mission snapshot unavailable"
            self.missions_error = self.last_error
            self._board.stale = True
            return self._board

    @property
    def board(self) -> MissionBoard:
        return self._load_missions()
