"""Read-only combat feed parser for Star Citizen's ``Game.log``.

The module recognises four kinds of observations:

* ``vehicle_damage`` - a vehicle hull or system took damage;
* ``weapon_impact`` - a weapon, projectile or missile struck a vehicle;
* ``player_kill`` - the log's ``CActor::Kill`` actor-death line;
* ``vehicle_destruction`` - a vehicle changed destroy level.

The parser deliberately distinguishes the two terminal vehicle states:

* ``Soft-Dead`` - the hull is disabled or immobilised but remains intact;
* ``Destroyed`` - the vehicle exploded, broke apart, or was otherwise removed.

Star Citizen changes its diagnostic strings between patches.  The defaults
cover the formats currently seen in the wild and stay intentionally broad;
callers can inject their own compiled patterns without touching the watcher.
No game memory, input, or game files are touched.  The watcher only reads the
log and tolerates a missing, rotated, or truncated file.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

STATUS_ACTIVE = "Active"
STATUS_SOFT_DEAD = "Soft-Dead"
STATUS_DESTROYED = "Destroyed"

KIND_VEHICLE_DAMAGE = "vehicle_damage"
KIND_WEAPON_IMPACT = "weapon_impact"
KIND_PLAYER_KILL = "player_kill"
KIND_VEHICLE_DESTRUCTION = "vehicle_destruction"

STATUS_ORDER = {
    STATUS_ACTIVE: 0,
    STATUS_SOFT_DEAD: 1,
    STATUS_DESTROYED: 2,
}

DEFAULT_LOG_PATH = Path.home() / "starcitizen" / "LIVE" / "Game.log"

CombatLogCallback = Callable[["CombatLogEvent"], None]

# Game.log timestamps look like <2025-10-15T07:31:19.238Z>.
_TIMESTAMP_PATTERN = re.compile(
    r"<(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)>"
)

# Exact current-format destruction line.  Position/velocity and the attacker
# clause are optional because they have appeared in different patch builds.
_VEHICLE_DESTRUCTION_PATTERN = re.compile(
    r"(?i)<Vehicle\s+Destruction>\s*"
    r"CVehicle::OnAdvanceDestroyLevel:\s*"
    r"Vehicle\s+'(?P<vehicle>[^']+)'\s+"
    r"\[(?P<vehicle_id>\d+)\]\s+"
    r"in\s+zone\s+'(?P<zone>[^']*)'"
    r"(?:\s+\[pos[^\]]*\])?"
    r"\s+driven\s+by\s+'(?P<driver>[^']*)'"
    r"\s+\[(?P<driver_id>\d+)\]\s+"
    r"advanced\s+from\s+destroy\s+level\s+(?P<from_level>\d+)"
    r"\s+to\s+(?P<to_level>\d+)"
    r"(?:\s+caused\s+by\s+'(?P<attacker>[^']*)'"
    r"\s+\[(?P<attacker_id>\d+)\])?"
    r"(?:\s+with\s+'(?P<damage_type>[^']*)')?"
)

# Exact current-format actor death line.
_ACTOR_KILL_PATTERN = re.compile(
    r"(?i)<Actor\s+Death>\s*CActor::Kill:\s*"
    r"'(?P<victim>[^']+)'\s+"
    r"\[(?P<victim_id>\d+)\]\s+"
    r"in\s+zone\s+'(?P<zone>[^']*)'\s+"
    r"killed\s+by\s+'(?P<killer>[^']+)'\s+"
    r"\[(?P<killer_id>\d+)\]\s+"
    r"using\s+'(?P<weapon>[^']*)'\s+"
    r"\[Class\s+(?P<weapon_class>[^\]]+)\]\s+"
    r"with\s+damage\s+type\s+'(?P<damage_type>[^']*)'"
    r"(?:\s+from\s+direction\s+x:\s*(?P<dx>[-+]?\d+(?:\.\d+)?),"
    r"\s*y:\s*(?P<dy>[-+]?\d+(?:\.\d+)?),"
    r"\s*z:\s*(?P<dz>[-+]?\d+(?:\.\d+)?))?"
)

_DESTROY_LEVEL_PATTERN = re.compile(
    r"(?i)\badvanced\s+from\s+destroy\s+level\s+(\d+)\s+to\s+(\d+)"
)

# A line has to mention a vehicle, ship, hull, or target before a generic
# damage/destruction phrase is treated as combat data.  This prevents unrelated
# "work order destroyed" notices from entering the combat feed.
_VEHICLE_CONTEXT = re.compile(
    r"(?i)\b(?:vehicle|ship|hull|vessel|spacecraft|target)\b"
)

_TARGET_PATTERNS = (
    re.compile(r"(?i)\bVehicle\s+'([^']+)'"),
    re.compile(r"(?i)\bShip\s+'([^']+)'"),
    re.compile(r"(?i)\bVessel\s+'([^']+)'"),
    re.compile(r"(?i)\bTarget\s+'([^']+)'"),
    re.compile(r"(?i)\b(?:weapon\s+impact|impact|hit|struck)\s+"
               r"(?:on|against)\s+'([^']+)'"),
    re.compile(r"(?i)\b(?:on|against|to)\s+'([^']+)'"),
)

_QUOTED_USING = re.compile(r"(?i)\busing\s+'([^']+)'")
_QUOTED_CAUSED_BY = re.compile(r"(?i)\b(?:caused|destroyed|hit|struck)\s+by\s+'([^']+)'")
_QUOTED_DAMAGE_TYPE = re.compile(
    r"(?i)\bwith\s+(?:damage\s+type\s+)?'([^']+)'")

_TRAILING_NOISE = re.compile(
    r"(?i)\s+(?:added|notification|notice|log|entry|event)\b.*$"
)
_MULTISPACE = re.compile(r"\s+")

_NPC_INDICATORS = (
    "pu_pilots",
    "pu_",
    "ai_crim",
    "ai_",
    "_npc_",
    "criminal-pilot",
    "security-",
    "pirate-",
    "-pilot_light_",
    "-pilot_medium_",
    "-pilot_heavy_",
    "npc_archetypes",
    "kopion_",
)


DEFAULT_SOFT_DEATH_PATTERNS = (
    re.compile(r"(?i)\bsoft[\s_-]*death\b"),
    re.compile(
        r"(?i)\b(?:hull|ship|vehicle|vessel|spacecraft)\b.{0,90}\b"
        r"(?:soft[\s_-]*dead|disabled|crippled|immobilized|immobilised|"
        r"inoperable|incapacitated)\b"
    ),
    re.compile(
        r"(?i)\b(?:soft[\s_-]*dead|disabled|crippled|immobilized|immobilised|"
        r"inoperable|incapacitated)\b.{0,90}\b"
        r"(?:hull|ship|vehicle|vessel|spacecraft)\b"
    ),
)

DEFAULT_DESTRUCTION_PATTERNS = (
    re.compile(
        r"(?i)\b(?:catastroph(?:ic|ically)|destroyed|destroy|exploded|"
        r"explode|explosion|blown\s+apart|wrecked|wreck)\b"
    ),
)

DEFAULT_VEHICLE_DAMAGE_PATTERNS = (
    re.compile(r"(?i)<Vehicle\s+Damage>"),
    re.compile(
        r"(?i)\b(?:vehicle|ship|hull|vessel|spacecraft)\b.{0,90}\b"
        r"(?:took|taking|received|receiving|sustained|sustaining|"
        r"damage|damaged|hit)\b"
    ),
    re.compile(
        r"(?i)\b(?:damage|damaged|hit|impact|impacted|struck)\b"
        r".{0,90}\b(?:vehicle|ship|hull|vessel|spacecraft)\b"
    ),
)

DEFAULT_WEAPON_IMPACT_PATTERNS = (
    re.compile(r"(?i)\bweapon\s+impact\b"),
    re.compile(
        r"(?i)\b(?:projectile|missile|torpedo|laser|ballistic|gun|cannon)\b"
        r".{0,50}\b(?:hit|impact|impacted|struck)\b"
    ),
    re.compile(
        r"(?i)\b(?:hit|impact|impacted|struck)\b.{0,50}\b"
        r"(?:projectile|missile|torpedo|laser|ballistic|gun|cannon)\b"
    ),
)


@dataclass(frozen=True)
class CombatLogEvent:
    """One parsed, read-only combat observation from ``Game.log``."""

    kind: str
    status: str
    target_name: str
    target_type: str = "Vehicle"
    timestamp: float = 0.0
    target_id: str = ""
    attacker: str = ""
    weapon: str = ""
    weapon_class: str = ""
    damage_type: str = ""
    zone: str = ""
    from_level: int | None = None
    to_level: int | None = None
    is_npc_target: bool = False
    is_npc_attacker: bool = False
    raw: str = ""

    @property
    def operational_status(self) -> str:
        return self.status

    @property
    def terminal(self) -> bool:
        return self.status in (STATUS_SOFT_DEAD, STATUS_DESTROYED)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "timestamp": self.timestamp,
            "target_name": self.target_name,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "operational_status": self.status,
            "attacker": self.attacker,
            "weapon": self.weapon,
            "weapon_class": self.weapon_class,
            "damage_type": self.damage_type,
            "zone": self.zone,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "is_npc_target": self.is_npc_target,
            "is_npc_attacker": self.is_npc_attacker,
            "raw": self.raw,
        }


def parse_log_timestamp(line: str, now: float | None = None) -> float:
    """Return the ``Game.log`` epoch timestamp, or ``now`` when absent."""
    fallback = time.time() if now is None else float(now)
    match = _TIMESTAMP_PATTERN.search(line or "")
    if match is None:
        return fallback
    text = match.group("stamp").replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return fallback
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def _clean_entity(value: object) -> str:
    text = str(value or "").strip().strip("\"'").strip()
    text = _TRAILING_NOISE.sub("", text).strip()
    text = re.sub(r"\s+\([^)]*\)\s*$", "", text).strip()
    text = text.strip(" \t,;:-")
    if not text or text.casefold() in {"unknown", "none", "null", "n/a", "-"}:
        return ""
    return _MULTISPACE.sub(" ", text)[:160]


def pretty_vehicle_name(value: object) -> str:
    """Turn ``ANVL_Paladin_6763231335005`` into ``Paladin`` when possible."""
    raw = _clean_entity(value)
    if not raw:
        return ""
    text = raw
    if "_" in text and text.rsplit("_", 1)[-1].isdigit():
        parts = text.split("_")[:-1]
        if len(parts) >= 2 and len(parts[0]) <= 6 and parts[0].isupper():
            parts = parts[1:]
        text = "_".join(parts) or parts[-1]
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = text.replace("_", " ").strip()
    return _clean_entity(text) or raw


def _is_npc(value: object) -> bool:
    """Best-effort NPC detector for actor names emitted by the PU."""
    name = str(value or "").strip()
    if not name:
        return False
    lower = name.casefold()
    if any(indicator in lower for indicator in _NPC_INDICATORS):
        return True
    if len(name) > 40:
        return True
    return name.count("-") >= 3


@lru_cache(maxsize=128)
def _field_pattern(key: str) -> re.Pattern:
    key_pattern = re.escape(key).replace("_", r"[\s_-]*")
    return re.compile(
        rf"(?i)(?<![\w])[\"']?{key_pattern}[\"']?\s*[:=]\s*"
        rf"(?:[\"']([^\"']*)[\"']|([^\"'\r\n,;\]\}}]+))"
    )


def _field(line: str, *keys: str) -> str:
    for key in keys:
        match = _field_pattern(key).search(line)
        if match is None:
            continue
        value = _clean_entity(match.group(1) or match.group(2) or "")
        if value:
            return value
    return ""


def _target_from_line(line: str) -> str:
    for pattern in _TARGET_PATTERNS:
        match = pattern.search(line)
        if match is not None:
            value = _clean_entity(match.group(1))
            if value:
                return value
    value = _field(
        line, "vehicle", "vehicle_name", "ship", "ship_name", "vessel",
        "target", "target_name", "entity", "entity_name")
    return _clean_entity(value)


def _target_type_from_line(line: str, default: str = "Vehicle") -> str:
    value = _field(
        line, "target_type", "entity_type", "vehicle_type", "ship_type")
    return value or default


def _attacker_from_line(line: str) -> str:
    value = _field(
        line, "attacker", "attacker_name", "killer", "killer_name",
        "caused_by", "destroyed_by")
    if value:
        return value
    match = _QUOTED_CAUSED_BY.search(line)
    return _clean_entity(match.group(1)) if match else ""


def _weapon_from_line(line: str) -> str:
    value = _field(line, "weapon", "weapon_name", "projectile", "munition")
    if value:
        return value
    match = _QUOTED_USING.search(line)
    return _clean_entity(match.group(1)) if match else ""


def _damage_type_from_line(line: str) -> str:
    value = _field(line, "damage_type", "damage", "damage_type_name")
    if value:
        return value
    match = _QUOTED_DAMAGE_TYPE.search(line)
    return _clean_entity(match.group(1)) if match else ""


def _zone_from_line(line: str) -> str:
    value = _field(line, "zone", "location", "area")
    if value:
        return value
    match = re.search(r"(?i)\b(?:in|at)\s+zone\s+'([^']*)'", line)
    return _clean_entity(match.group(1)) if match else ""


class CombatLogParser:
    """Deterministic, offline parser for combat lines in ``Game.log``."""

    def __init__(
            self,
            *,
            soft_death_patterns: Iterable[re.Pattern] | None = None,
            destruction_patterns: Iterable[re.Pattern] | None = None,
            vehicle_damage_patterns: Iterable[re.Pattern] | None = None,
            weapon_impact_patterns: Iterable[re.Pattern] | None = None):
        self.soft_death_patterns = (
            DEFAULT_SOFT_DEATH_PATTERNS if soft_death_patterns is None
            else tuple(soft_death_patterns))
        self.destruction_patterns = (
            DEFAULT_DESTRUCTION_PATTERNS if destruction_patterns is None
            else tuple(destruction_patterns))
        self.vehicle_damage_patterns = (
            DEFAULT_VEHICLE_DAMAGE_PATTERNS if vehicle_damage_patterns is None
            else tuple(vehicle_damage_patterns))
        self.weapon_impact_patterns = (
            DEFAULT_WEAPON_IMPACT_PATTERNS if weapon_impact_patterns is None
            else tuple(weapon_impact_patterns))

    # ------------------------------------------------------------------
    def parse_line(
            self, line: str, now: float | None = None) -> list[CombatLogEvent]:
        raw = line.rstrip("\r\n")
        if not raw:
            return []
        stamp = parse_log_timestamp(raw, now)

        event = self._vehicle_destruction(raw, stamp)
        if event is not None:
            return [event]

        event = self._actor_kill(raw, stamp)
        if event is not None:
            return [event]

        event = self._target_state(raw, stamp)
        if event is not None:
            return [event]

        event = self._damage(raw, stamp)
        if event is not None:
            return [event]

        return []

    # ------------------------------------------------------------------
    def _vehicle_destruction(
            self, raw: str, stamp: float) -> CombatLogEvent | None:
        match = _VEHICLE_DESTRUCTION_PATTERN.search(raw)
        if match is not None:
            to_level = int(match.group("to_level"))
            if to_level <= 0:
                return None
            vehicle = match.group("vehicle")
            attacker = _clean_entity(match.group("attacker"))
            driver = _clean_entity(match.group("driver"))
            return CombatLogEvent(
                kind=KIND_VEHICLE_DESTRUCTION,
                status=(STATUS_SOFT_DEAD if to_level == 1
                        else STATUS_DESTROYED),
                target_name=pretty_vehicle_name(vehicle),
                target_type="Vehicle",
                timestamp=stamp,
                target_id=match.group("vehicle_id"),
                attacker=attacker,
                damage_type=_clean_entity(match.group("damage_type")),
                zone=_clean_entity(match.group("zone")),
                from_level=int(match.group("from_level")),
                to_level=to_level,
                is_npc_target=_is_npc(driver),
                is_npc_attacker=_is_npc(attacker),
                raw=raw[:2000],
            )

        # Fallback for a patch that changes the surrounding prose but keeps
        # the stable destroy-level fields.
        level_match = _DESTROY_LEVEL_PATTERN.search(raw)
        if level_match is not None:
            to_level = int(level_match.group(2))
            if to_level <= 0:
                return None
            target = _target_from_line(raw)
            if not target:
                return None
            attacker = _attacker_from_line(raw)
            return CombatLogEvent(
                kind=KIND_VEHICLE_DESTRUCTION,
                status=(STATUS_SOFT_DEAD if to_level == 1
                        else STATUS_DESTROYED),
                target_name=pretty_vehicle_name(target),
                target_type=_target_type_from_line(raw),
                timestamp=stamp,
                attacker=attacker,
                weapon=_weapon_from_line(raw),
                damage_type=_damage_type_from_line(raw),
                zone=_zone_from_line(raw),
                from_level=int(level_match.group(1)),
                to_level=to_level,
                is_npc_attacker=_is_npc(attacker),
                raw=raw[:2000],
            )
        return None

    # ------------------------------------------------------------------
    def _actor_kill(
            self, raw: str, stamp: float) -> CombatLogEvent | None:
        match = _ACTOR_KILL_PATTERN.search(raw)
        if match is not None:
            victim = _clean_entity(match.group("victim"))
            killer = _clean_entity(match.group("killer"))
            victim_id = match.group("victim_id")
            zone = _clean_entity(match.group("zone"))
            weapon = _clean_entity(match.group("weapon"))
            weapon_class = _clean_entity(match.group("weapon_class"))
            damage_type = _clean_entity(match.group("damage_type"))
        else:
            # Some patches drop the [Class ...] or direction fields while
            # keeping the stable CActor::Kill victim/killer pairs.
            if "CActor::Kill:" not in raw:
                return None
            victim_match = re.search(
                r"(?i)CActor::Kill:\s*'([^']+)'\s+\[(\d+)\]", raw)
            if victim_match is None:
                return None
            killer_match = re.search(
                r"(?i)\bkilled\s+by\s+'([^']+)'\s+\[(\d+)\]", raw)
            victim = _clean_entity(victim_match.group(1))
            killer = (_clean_entity(killer_match.group(1))
                      if killer_match else "")
            victim_id = victim_match.group(2)
            zone = _zone_from_line(raw)
            weapon = _weapon_from_line(raw)
            weapon_class = ""
            class_match = re.search(r"(?i)\[Class\s+([^\]]+)\]", raw)
            if class_match:
                weapon_class = _clean_entity(class_match.group(1))
            damage_type = _damage_type_from_line(raw)
        if not victim:
            return None
        target_type = ("Vehicle"
                       if damage_type.casefold() == "vehicledestruction"
                       else "Actor")
        return CombatLogEvent(
            kind=KIND_PLAYER_KILL,
            status=STATUS_DESTROYED,
            target_name=victim,
            target_type=target_type,
            timestamp=stamp,
            target_id=victim_id,
            attacker=killer,
            weapon=weapon,
            weapon_class=weapon_class,
            damage_type=damage_type,
            zone=zone,
            is_npc_target=_is_npc(victim),
            is_npc_attacker=_is_npc(killer),
            raw=raw[:2000],
        )

    # ------------------------------------------------------------------
    def _target_state(
            self, raw: str, stamp: float) -> CombatLogEvent | None:
        if not _VEHICLE_CONTEXT.search(raw):
            return None

        status = ""
        if any(pattern.search(raw) for pattern in self.soft_death_patterns):
            status = STATUS_SOFT_DEAD
        elif any(pattern.search(raw) for pattern in self.destruction_patterns):
            status = STATUS_DESTROYED
        if not status:
            return None

        target = _target_from_line(raw)
        if not target:
            return None
        attacker = _attacker_from_line(raw)
        return CombatLogEvent(
            kind=KIND_VEHICLE_DESTRUCTION,
            status=status,
            target_name=pretty_vehicle_name(target),
            target_type=_target_type_from_line(raw),
            timestamp=stamp,
            attacker=attacker,
            weapon=_weapon_from_line(raw),
            damage_type=_damage_type_from_line(raw),
            zone=_zone_from_line(raw),
            is_npc_attacker=_is_npc(attacker),
            raw=raw[:2000],
        )

    # ------------------------------------------------------------------
    def _damage(self, raw: str, stamp: float) -> CombatLogEvent | None:
        weapon_impact = any(
            pattern.search(raw) for pattern in self.weapon_impact_patterns)
        if not weapon_impact and not any(
                pattern.search(raw) for pattern in self.vehicle_damage_patterns):
            return None
        target = _target_from_line(raw)
        attacker = _attacker_from_line(raw)
        kind = KIND_WEAPON_IMPACT if weapon_impact else KIND_VEHICLE_DAMAGE
        return CombatLogEvent(
            kind=kind,
            status=STATUS_ACTIVE,
            target_name=pretty_vehicle_name(target),
            target_type=_target_type_from_line(raw),
            timestamp=stamp,
            attacker=attacker,
            weapon=_weapon_from_line(raw),
            damage_type=_damage_type_from_line(raw),
            zone=_zone_from_line(raw),
            is_npc_attacker=_is_npc(attacker),
            raw=raw[:2000],
        )


# ---------------------------------------------------------------------------
def _candidate_logs() -> Iterable[Path]:
    override = os.environ.get("SC_GAME_LOG")
    if override:
        yield Path(override).expanduser()

    yield DEFAULT_LOG_PATH
    home = Path.home()
    yield home / "StarCitizen" / "LIVE" / "Game.log"
    for branch in ("PTU", "EPTU"):
        yield home / "starcitizen" / branch / "Game.log"
        yield home / "StarCitizen" / branch / "Game.log"

    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
        for bottle_root in (base / "CrossOver" / "Bottles",
                            base / "Whisky" / "Bottles"):
            try:
                for path in bottle_root.glob(
                        "*/drive_c/Program Files/Roberts Space Industries/"
                        "StarCitizen/*/Game.log"):
                    yield path
            except OSError:
                continue


def discover_combat_log() -> Path | None:
    """Return the first existing known log, or ``None`` when none exists."""
    for path in _candidate_logs():
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    # Reuse the overlay's wider Windows/Linux discovery as a final fallback.
    try:
        from .game_log import discover_game_log as _discover
        return _discover()
    except Exception:
        return None


class CombatLogWatcher:
    """Daemon thread that tails one read-only log and forwards combat events."""

    def __init__(self, path: str | Path | None = None,
                 callback: CombatLogCallback | None = None,
                 poll_interval: float = 0.5,
                 discovery_interval: float = 8.0,
                 from_start: bool = False,
                 parser: CombatLogParser | None = None):
        self._path = Path(path).expanduser() if path else None
        self._callback = callback
        self._poll_interval = max(0.1, float(poll_interval))
        self._discovery_interval = max(1.0, float(discovery_interval))
        self._from_start = bool(from_start)
        self._parser = parser or CombatLogParser()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop.is_set())

    def start(self) -> "CombatLogWatcher":
        if self.running:
            return self
        previous = self._thread
        if (previous is not None and previous.is_alive()
                and previous is not threading.current_thread()):
            previous.join(2.0)
            if previous.is_alive():
                # Never run two tail threads against the same stop event.
                return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="sc-combat-log", daemon=True)
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

    def _resolve_path(self) -> Path | None:
        if self._path is not None:
            return self._path
        self._path = discover_combat_log()
        return self._path

    @staticmethod
    def _file_identity(handle) -> tuple[int, int] | None:
        try:
            stat = os.fstat(handle.fileno())
            return (int(stat.st_dev), int(stat.st_ino))
        except (OSError, ValueError, AttributeError):
            return None

    def _open(self, path: Path, *, at_end: bool = True):
        handle = open(path, "r", encoding="utf-8", errors="replace")
        if at_end and not self._from_start:
            try:
                handle.seek(0, os.SEEK_END)
            except OSError:
                pass
        return handle

    def _run(self) -> None:
        thread = threading.current_thread()
        handle = None
        current: Path | None = None
        handle_id: tuple[int, int] | None = None
        reopen_at_start = False
        # Resolve immediately on the first pass instead of waiting for one
        # discovery interval (some platforms start monotonic time near zero).
        last_discovery = time.monotonic() - self._discovery_interval
        try:
            while not self._stop.is_set():
                if current is None:
                    now = time.monotonic()
                    if now - last_discovery >= self._discovery_interval:
                        last_discovery = now
                        current = self._resolve_path()
                    if current is None:
                        self._stop.wait(self._discovery_interval)
                        continue
                    try:
                        handle = self._open(
                            current, at_end=not reopen_at_start)
                        reopen_at_start = False
                        handle_id = self._file_identity(handle)
                    except OSError:
                        current = None
                        handle_id = None
                        self._stop.wait(self._discovery_interval)
                        continue

                try:
                    line = handle.readline()
                except (OSError, ValueError):
                    line = ""

                if line:
                    try:
                        events = self._parser.parse_line(line)
                    except Exception:
                        # A malformed line or custom regex must never kill
                        # the tail loop.
                        events = ()
                    for event in events:
                        if self._stop.is_set():
                            break
                        if self._callback is not None:
                            try:
                                self._callback(event)
                            except Exception:
                                # A consumer must never kill the tailer.
                                pass
                    continue

                # EOF: detect truncation and replacement, including the POSIX
                # case where the old file remains open after a log rotation.
                try:
                    stat = current.stat()
                    path_id = (int(stat.st_dev), int(stat.st_ino))
                    rotated = (handle_id is not None
                               and path_id != handle_id
                               and stat.st_size > 0)
                    if rotated or stat.st_size < handle.tell():
                        handle.close()
                        handle = None
                        handle_id = None
                        current = None
                        reopen_at_start = True
                        # Re-resolve immediately; do not wait a full
                        # discovery interval before opening the new file.
                        last_discovery = (
                            time.monotonic() - self._discovery_interval)
                        continue
                except OSError:
                    handle.close()
                    handle = None
                    handle_id = None
                    current = None
                    last_discovery = (
                        time.monotonic() - self._discovery_interval)
                    continue
                self._stop.wait(self._poll_interval)
        finally:
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            if self._thread is thread:
                self._thread = None


__all__ = [
    "CombatLogEvent",
    "CombatLogParser",
    "CombatLogWatcher",
    "DEFAULT_DESTRUCTION_PATTERNS",
    "DEFAULT_LOG_PATH",
    "DEFAULT_SOFT_DEATH_PATTERNS",
    "DEFAULT_VEHICLE_DAMAGE_PATTERNS",
    "DEFAULT_WEAPON_IMPACT_PATTERNS",
    "KIND_PLAYER_KILL",
    "KIND_VEHICLE_DAMAGE",
    "KIND_VEHICLE_DESTRUCTION",
    "KIND_WEAPON_IMPACT",
    "STATUS_ACTIVE",
    "STATUS_DESTROYED",
    "STATUS_ORDER",
    "STATUS_SOFT_DEAD",
    "discover_combat_log",
    "parse_log_timestamp",
    "pretty_vehicle_name",
]
