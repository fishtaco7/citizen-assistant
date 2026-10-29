"""Live scminer.rocks "Where to Mine" client.

The site is a Next.js app with no public JSON API, so this module parses the
server-rendered pages:

* the index page gives every ore in one request (price, signature, top hotspot,
  best refinery, hotspot count),
* an ore page gives the ranked hotspot list, all refinery bonuses and gear.

Data is fetched live and mirrored to a cache file, which is only used when the
network or the site is unavailable.
"""
from __future__ import annotations

import html as H
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .config import config_dir

BASE_URL = "https://scminer.rocks/data/where-to-mine"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36")

INDEX_MAX_AGE = 15 * 60       # seconds before the ore list is re-synced
DETAIL_MAX_AGE = 6 * 3600     # seconds before an ore page is re-fetched

# scminer spells a few names differently from the in-game signature table.
ALIASES = {"aluminum": "aluminium"}


def cache_path() -> Path:
    return config_dir() / "where_to_mine.json"


@dataclass
class Hotspot:
    rank: int
    name: str
    system: str = ""
    kind: str = ""
    abundance: float = 0.0
    gravity: str = ""
    hazard: str = ""
    gear: str = ""


@dataclass
class Refinery:
    name: str
    bonus: int
    method: str = ""


@dataclass
class Ore:
    slug: str
    name: str
    category: str = ""            # Ship | ROC | FPS
    price: int | None = None
    signature: int | None = None
    volatile: bool = False
    top: Hotspot | None = None
    hotspot_count: int = 0
    best_refinery: Refinery | None = None
    laser: str = ""
    # Filled in by the per-ore page.
    hotspots: list[Hotspot] = field(default_factory=list)
    refineries: list[Refinery] = field(default_factory=list)
    vessel: str = ""
    gadget: str = ""
    notes: list[str] = field(default_factory=list)
    detail_at: float = 0.0        # unix time of the last successful fetch

    @property
    def has_detail(self) -> bool:
        return bool(self.hotspots)

    @property
    def top_hotspot(self) -> Hotspot | None:
        return self.hotspots[0] if self.hotspots else self.top


# ---------------------------------------------------------------------------
def _fetch(url: str, timeout: float = 25.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _stripped(raw: str) -> str:
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.S)
    return body.replace("<!-- -->", "")


def _text_lines(raw: str) -> list[str]:
    """HTML -> visible text lines, order preserved, once per element."""
    body = re.sub(r"<[^>]+>", "\n", _stripped(raw))
    return [line.strip() for line in H.unescape(body).split("\n") if line.strip()]


def _int(value: str) -> int | None:
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def _category(text: str) -> str:
    lowered = text.lower()
    if "fps" in lowered or "multi-tool" in lowered or "hand mining" in lowered:
        return "FPS"
    if "roc" in lowered or "ground" in lowered:
        return "ROC"
    if "ship" in lowered:
        return "Ship"
    return ""


# ---------------------------------------------------------------------------
def _parse_index(raw: str) -> tuple[str, list[Ore]]:
    body = _stripped(raw)
    patch_match = re.search(r"PATCH\s*([\d.]+)", " ".join(_text_lines(raw)))
    patch = patch_match.group(1) if patch_match else ""

    ores: list[Ore] = []
    for match in re.finditer(r'href="/data/where-to-mine/([a-z0-9\-]+)"', body):
        slug = match.group(1)
        if any(ore.slug == slug for ore in ores):
            continue
        lines = _text_lines(body[match.end():match.end() + 6000])[:40]
        _parse_index_card(slug, lines, ores)
    return patch, ores


def _parse_index_card(slug: str, lines: list[str], out: list[Ore]) -> None:
    name = ""
    name_index = None
    for i, line in enumerate(lines):
        if line.startswith("Where to Mine "):
            name = line[len("Where to Mine "):].strip()
            name_index = i
            break
    if not name:
        return

    head = lines[:name_index]
    category = next((_category(line) for line in head
                     if line.upper().endswith("DEPOSIT")), "")

    signature = None
    volatile = False
    for line in head:
        if re.fullmatch(r"RS\s*[\d,]+", line):
            signature = _int(line)
        elif line.strip().upper() == "VOLATILE":
            volatile = True

    tail = lines[name_index + 1:]
    price = None
    for line in tail:
        price_match = re.fullmatch(r"([\d,]+)\s*aUEC\s*/\s*SCU", line)
        if price_match:
            price = _int(price_match.group(1))
            break

    ore = Ore(slug=slug, name=name, category=category, price=price,
              signature=signature, volatile=volatile)

    for i, line in enumerate(tail):
        if "DEPOSIT HOTSPOT" in line.upper():
            spot_name = tail[i + 1] if i + 1 < len(tail) else ""
            abundance = tail[i + 2] if i + 2 < len(tail) else ""
            place = re.fullmatch(r"(.*?)\s*\((.*?)\)", spot_name)
            abundance_match = re.match(r"([\d.,]+)", abundance)
            ore.top = Hotspot(
                rank=1,
                name=place.group(1) if place else spot_name,
                system=place.group(2) if place else "",
                abundance=float(abundance_match.group(1).replace(",", ""))
                if abundance_match else 0.0,
            )
        elif line.startswith("BEST REFINERY DECK"):
            refinery_name = tail[i + 1] if i + 1 < len(tail) else ""
            bonus_line = tail[i + 2] if i + 2 < len(tail) else ""
            bonus_match = re.match(r"([+-]\d+)", bonus_line)
            ore.best_refinery = Refinery(
                name=refinery_name,
                bonus=int(bonus_match.group(1)) if bonus_match else 0,
            )
        elif line.startswith("RECOMMENDED:"):
            ore.laser = (line[len("RECOMMENDED:"):].strip()
                         or (tail[i + 1] if i + 1 < len(tail) else ""))
        elif line.endswith("Known Hotspots"):
            count_match = re.match(r"(\d+)", line)
            if count_match:
                ore.hotspot_count = int(count_match.group(1))
    out.append(ore)


def _parse_detail(ore: Ore, raw: str) -> None:
    lines = _text_lines(raw)

    start = next((i for i, line in enumerate(lines)
                  if "HOTSPOT INTELLIGENCE" in line), None)
    if start is None:
        raise ValueError("unrecognised ore page layout")

    method = next((line for line in lines[max(0, start - 3):start]
                   if "Mining" in line or "Backpack" in line), "")
    if method:
        ore.category = _category(method) or ore.category

    for label, attr in (("RECOMMENDED VESSEL", "vessel"),
                        ("MINING LASER HEAD", "laser"),
                        ("MINING GADGET / MODULE", "gadget")):
        index = next((i for i, line in enumerate(lines) if line == label), None)
        if index is not None and index + 1 < len(lines):
            setattr(ore, attr, lines[index + 1])

    sig_index = next((i for i, line in enumerate(lines)
                      if line == "RADAR SCAN SIGNATURE"), None)
    if sig_index is not None:
        for line in lines[sig_index + 1:sig_index + 3]:
            if re.fullmatch(r"RS\s*[\d,]+", line):
                ore.signature = _int(line)
                break
            if "VOLATILE" in line.upper():
                ore.volatile = True
                break

    spots_index = next((i for i, line in enumerate(lines)
                        if line.endswith("Known Hotspots")), None)
    if spots_index is not None:
        ore.hotspots = _parse_hotspots(lines, spots_index + 1)

    refine_index = next((i for i, line in enumerate(lines)
                         if line.startswith("Where to Refine")), None)
    if refine_index is not None:
        ore.refineries = _parse_refineries(lines, refine_index + 2)

    ore.notes = [line for line in lines[start:start + 14]
                 if "Warning:" in line or line.startswith("Refinery Yield Bonus:")]
    if ore.hotspots:
        ore.top = ore.hotspots[0]
        ore.hotspot_count = len(ore.hotspots)
    if ore.refineries:
        ore.best_refinery = ore.refineries[0]
    ore.detail_at = datetime.now().timestamp()


def _parse_hotspots(lines: list[str], index: int) -> list[Hotspot]:
    spots: list[Hotspot] = []
    i = index
    while i < len(lines):
        rank_match = re.fullmatch(r"#(\d+)", lines[i])
        if not rank_match:
            i += 1
            if spots and not lines[i - 1].startswith(("Gravity:", "Hazard:", "Gear:")):
                break
            continue

        spot = Hotspot(rank=int(rank_match.group(1)),
                       name=lines[i + 1] if i + 1 < len(lines) else "")
        place = lines[i + 2] if i + 2 < len(lines) else ""
        place_match = re.fullmatch(r"\((.*?)\s*[•\u2022\u00b7\ufffd]\s*(.*?)\)", place)
        if place_match:
            spot.system = place_match.group(1).strip()
            spot.kind = place_match.group(2).strip()

        j = i + 3
        while j < len(lines):
            line = lines[j]
            abundance_match = re.fullmatch(r"([\d.,]+)\s*%\s*Abundance", line)
            if abundance_match:
                spot.abundance = float(abundance_match.group(1).replace(",", ""))
            elif line.startswith("Gravity:"):
                spot.gravity = line.split(":", 1)[1].strip()
            elif line.startswith("Hazard:"):
                spot.hazard = line.split(":", 1)[1].strip()
            elif line.startswith("Gear:"):
                spot.gear = line.split(":", 1)[1].strip()
            else:
                break
            j += 1

        spots.append(spot)
        i = j
    return spots


def _parse_refineries(lines: list[str], index: int) -> list[Refinery]:
    refineries: list[Refinery] = []
    i = index
    while i + 2 < len(lines):
        if lines[i].startswith("Tactical Gear Configuration"):
            break
        bonus_match = re.fullmatch(r"([+-]\d+)\s*%(?:\s*Yield)?", lines[i + 2])
        if lines[i + 1].startswith("Recommended Method:") and bonus_match:
            refineries.append(Refinery(
                name=lines[i],
                bonus=int(bonus_match.group(1)),
                method=lines[i + 1].split(":", 1)[1].strip(),
            ))
            i += 3
            continue
        i += 1
    return refineries


# ---------------------------------------------------------------------------
class LiveData:
    """Ore locations, refreshed from scminer.rocks and cached to disk."""

    def __init__(self, path: Path | None = None):
        self.path = path or cache_path()
        self.ores: list[Ore] = []
        self.patch: str = ""
        self.index_at: float = 0.0
        self.error: str = ""
        self._by_slug: dict[str, Ore] = {}
        self._load_cache()

    # -- cache ---------------------------------------------------------
    def _load_cache(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        self.patch = raw.get("patch", "")
        self.index_at = float(raw.get("index_at", 0.0))
        for item in raw.get("ores", []):
            known = {k: v for k, v in item.items()
                     if k in Ore.__dataclass_fields__}
            ore = Ore(**{**known,
                         "top": Hotspot(**item["top"]) if item.get("top") else None,
                         "best_refinery": Refinery(**item["best_refinery"])
                         if item.get("best_refinery") else None,
                         "hotspots": [Hotspot(**s) for s in item.get("hotspots", [])],
                         "refineries": [Refinery(**r) for r in item.get("refineries", [])]})
            self.ores.append(ore)
            self._by_slug[ore.slug] = ore
        self._sort()

    def save_cache(self) -> None:
        payload = {
            "source": BASE_URL,
            "patch": self.patch,
            "index_at": self.index_at,
            "saved": datetime.now().isoformat(timespec="seconds"),
            "ores": [asdict(ore) for ore in self.ores],
        }
        tmp = self.path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=1, ensure_ascii=False)
            tmp.replace(self.path)
        except OSError:
            pass

    # -- freshness -----------------------------------------------------
    def __len__(self) -> int:
        return len(self.ores)

    @property
    def loaded(self) -> bool:
        return bool(self.ores)

    def age(self) -> float | None:
        return (datetime.now().timestamp() - self.index_at) if self.index_at else None

    def needs_index(self, max_age: float = INDEX_MAX_AGE) -> bool:
        age = self.age()
        return age is None or age > max_age

    def needs_detail(self, ore: Ore, max_age: float = DETAIL_MAX_AGE) -> bool:
        return not ore.has_detail or (
            datetime.now().timestamp() - ore.detail_at > max_age)

    def synced_text(self) -> str:
        if not self.index_at:
            return "NEVER SYNCED"
        stamp = datetime.fromtimestamp(self.index_at)
        today = datetime.now().date()
        if stamp.date() == today:
            return f"SYNCED {stamp:%H:%M}"
        return f"CACHED {stamp:%d %b %H:%M}"

    # -- network -------------------------------------------------------
    def fetch_index(self) -> None:
        raw = _fetch(BASE_URL)
        patch, ores = _parse_index(raw)
        if not ores:
            raise ValueError("no ores found on the index page")

        # Keep already-fetched detail pages for ores that are still listed.
        for ore in ores:
            previous = self._by_slug.get(ore.slug)
            if previous and previous.has_detail:
                ore.hotspots = previous.hotspots
                ore.refineries = previous.refineries
                ore.vessel = previous.vessel
                ore.gadget = previous.gadget
                ore.notes = previous.notes
                ore.detail_at = previous.detail_at

        self.ores = ores
        self.patch = patch
        self.index_at = datetime.now().timestamp()
        self.error = ""
        self._sort()
        self.save_cache()

    def fetch_detail(self, slug: str) -> None:
        ore = self._by_slug.get(slug)
        if ore is None:
            raise KeyError(slug)
        _parse_detail(ore, _fetch(f"{BASE_URL}/{slug}"))
        self.error = ""
        self.save_cache()

    # -- lookup --------------------------------------------------------
    def _sort(self) -> None:
        self.ores.sort(key=lambda o: (-(o.price or 0), o.name))
        self._by_slug = {ore.slug: ore for ore in self.ores}

    def by_slug(self, slug: str) -> Ore | None:
        return self._by_slug.get(slug)

    def find(self, name: str) -> Ore | None:
        if not name:
            return None
        key = re.sub(r"[^a-z]", "", name.lower())
        key = ALIASES.get(key, key)
        for ore in self.ores:
            if re.sub(r"[^a-z]", "", ore.name.lower()) == key:
                return ore
        for ore in self.ores:
            other = re.sub(r"[^a-z]", "", ore.name.lower())
            if key and (other.startswith(key) or key.startswith(other)):
                return ore
        return None

    def categories(self) -> list[str]:
        seen: list[str] = []
        for ore in self.ores:
            if ore.category and ore.category not in seen:
                seen.append(ore.category)
        return seen
