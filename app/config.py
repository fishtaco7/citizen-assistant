"""Persistent user settings, stored per OS outside OneDrive.

Windows: %APPDATA%\\Citizen Assistant
macOS:   ~/Library/Application Support/Citizen Assistant
Linux:   $XDG_CONFIG_HOME/Citizen Assistant (or ~/.config)

Older builds used an "SC-Overlay" folder; the user files are copied over on
first run so the region calibration and notes survive the rename.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


APP_NAME = "Citizen Assistant"
LEGACY_APP_NAMES = ("SC-Overlay",)
MIGRATE_FILES = ("config.json", "inventory.json", "unlocked_blueprints.json")


def _migrate_legacy(base: Path, target: Path) -> None:
    """Copy user data from the pre-rename config folder, once."""
    for name in LEGACY_APP_NAMES:
        legacy = base / name
        if not legacy.is_dir():
            continue
        for fname in MIGRATE_FILES:
            old = legacy / fname
            new = target / fname
            if old.is_file() and not new.exists():
                try:
                    shutil.copy2(old, new)
                except OSError:
                    pass


def config_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    _migrate_legacy(base, d)
    return d


CONFIG_PATH = config_dir() / "config.json"

DEFAULTS: dict = {
    "hotkey_toggle_nav": 0x72,          # VK_F3
    "hotkey_panic": 0x73,               # VK_F4 - hide everything
    "poll_ms": 125,
    "stability_reads": 2,               # fallback reads before the value is shown
    "fast_single_read": True,           # high-confidence exact reads lock immediately
    "fast_confidence": 0.97,            # OCR confidence needed for the fast path
    "scan_tuning_version": 1,           # migration marker for the faster defaults
    "min_value": 2500,
    "max_value": 60000,
    "tolerance": 0.08,
    # Physical-pixel rectangle of the in-game signature readout.
    "region": None,
    "show_debug_preview": False,
    "voice_enabled": True,             # speak the ore name when a scan locks
    "voice_volume": 0.8,               # 0.0 - 1.0
    # Forget the last spoken rock after this many seconds without a scan.
    # Brief HUD flicker keeps the dedupe, so the same rock is not re-announced.
    "voice_idle_reset_seconds": 12.0,
    "ui_scale": 1.0,                   # 0.75 - 1.5, applied on next launch
    "overlay_opacity": 1.0,            # 0.3 - 1.0, applies to overlay windows
    "nav_visible": True,
    "mining_panel_pos": None,
    "orefinder_pos": None,
    "hauling_pos": None,
    "inventory_pos": None,
    "uex_pos": None,
    "combat_pos": None,
    "hauling_cargo": 32,              # SCU used for trip-profit maths
    "hauling_commodity": 0,           # 0 = all commodities / top routes
    "hauling_selection_version": 2,   # migration marker for the new default
    # True keeps the overlay out of screenshots/streams via
    # WDA_EXCLUDEFROMCAPTURE.  Visible by default so a normal screenshot or
    # screen share includes the panels; toggle it in Settings for stream privacy.
    "capture_exclude": False,
    "capture_visibility_version": 1,   # migration marker for the visible default
}


def open_folder(path) -> None:
    """Reveal a folder in the platform file manager."""
    target = str(path)
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


class Config:
    def __init__(self, data: dict | None = None):
        self.data = dict(DEFAULTS)
        if data:
            self.data.update({k: v for k, v in data.items() if k in DEFAULTS})

    @classmethod
    def load(cls) -> "Config":
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            # The trade-routes tab used to open on Aluminum. Migrate existing
            # configs once so it opens on the all-commodity top routes instead.
            try:
                version = int(data.get("hauling_selection_version", 1))
            except (TypeError, ValueError):
                version = 0
            if version < 2:
                data["hauling_commodity"] = 0
                data["hauling_selection_version"] = 2
            # Move existing installs from the old 250 ms scan cadence to the
            # faster default once. Custom values other than the old default
            # are left alone.
            try:
                scan_version = int(data.get("scan_tuning_version", 0))
            except (TypeError, ValueError):
                scan_version = 0
            if scan_version < 1:
                try:
                    old_poll = int(data.get("poll_ms", 250))
                except (TypeError, ValueError):
                    old_poll = 250
                if old_poll == 250:
                    data["poll_ms"] = 125
                data["scan_tuning_version"] = 1
            # Capture exclusion used to be on by default with no UI toggle, so
            # existing installs could not screenshot the overlay at all.  Flip
            # that old default once; the new Settings toggle still lets anyone
            # hide the overlay from streams afterwards.
            try:
                capture_version = int(data.get("capture_visibility_version", 0))
            except (TypeError, ValueError):
                capture_version = 0
            if capture_version < 1:
                data["capture_exclude"] = False
                data["capture_visibility_version"] = 1
            return cls(data)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return cls()

    def save(self) -> None:
        tmp = CONFIG_PATH.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2)
            os.replace(tmp, CONFIG_PATH)
        except OSError:
            pass

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)
