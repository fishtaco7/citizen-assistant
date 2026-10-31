"""Application entry point and controller."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QGuiApplication, QIcon,
                           QKeySequence, QPainter, QPainterPath, QPixmap,
                           QShortcut)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import win32
from .blueprints import BlueprintTracker
from .capture import is_region_valid
from .combat import CombatTracker
from .config import Config, config_dir, open_folder
from .game_log import GameLogMonitor
from .hotkeys import HotkeyThread
from .mining import MiningData
from .orefinder import LiveData
from .ui.combat_panel import CombatPanel
from .ui.hauling_panel import HaulingPanel
from .ui.inventory_panel import InventoryPanel
from .ui.mining_panel import MiningPanel
from .ui.navbar import NavBar
from .ui.notes_panel import NotesPanel
from .ui.orefinder_panel import OreFinderPanel
from .ui.region_selector import RegionSelector
from .ui.settings_panel import SettingsPanel
from .ui.uex_panel import UexPanel
from .ui import theme
from .version import label as version_label
from .voice import Voice, spoken_text

APP_TITLE = "Citizen Assistant"
ICON_PATH = Path(__file__).resolve().parents[1] / "app.ico"

TOOLS = [
    {"id": "mining", "label": "MINING", "enabled": True,
     "hint": "Read scan signatures and identify the rock"},
    {"id": "orefinder", "label": "ORE FINDER", "enabled": True,
     "hint": "Where to mine each ore, live from scminer.rocks"},
    {"id": "hauling", "label": "HAULING", "enabled": True,
     "hint": "Trade routes, hauling contracts and the PPM planner"},
    {"id": "uex", "label": "UEX", "enabled": True,
     "hint": "Search the UEX player marketplace for items on sale"},
    {"id": "inventory", "label": "INVENTORY", "enabled": True,
     "hint": "Remember loot and supplies left at other stations"},
    {"id": "combat", "label": "COMBAT", "enabled": True,
     "hint": "Live kill, soft-death and destruction feed from Game.log"},
    {"id": "notes", "label": "NOTES", "enabled": True,
     "hint": "A notepad that saves itself"},
]


# ---------------------------------------------------------------------------
class ScanThread(QThread):
    """Grabs the calibrated region, OCRs it and resolves the signature."""

    result = Signal(object, object)   # OCRResult, list[Match]
    failed = Signal(str)

    def __init__(self, data: MiningData, cfg: Config, demo: bool = False, parent=None):
        super().__init__(parent)
        self.data = data
        self.demo = demo
        self._region = cfg["region"]
        self._enabled = True
        self._stop = False
        self._interval = int(cfg["poll_ms"])
        self._min_value = int(cfg["min_value"])
        self._max_value = int(cfg["max_value"])
        self._tolerance = float(cfg["tolerance"])
        self._min_value = int(cfg["min_value"])
        self._last_hash: int | None = None
        self._last_payload: tuple | None = None

    # -- setters are called from the GUI thread -------------------------
    def configure(self, region=None, interval_ms=None, tolerance=None) -> None:
        if region is not None:
            self._region = region
        if interval_ms is not None:
            self._interval = max(50, int(interval_ms))
        if tolerance is not None:
            self._tolerance = float(tolerance)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D102
        # numpy, OpenCV and the OCR model are loaded only when there is an
        # active region or demo frame. On a fresh install the region picker is
        # shown first, so the scanner no longer pays that cost at launch.
        grabber = None
        ocr = None
        demo_mod = None
        try:
            while not self._stop:
                started = time.perf_counter()
                region = self._region
                active = self.demo or (self._enabled and is_region_valid(region))
                if active:
                    try:
                        if self.demo and demo_mod is None:
                            from . import demo as demo_mod
                        if grabber is None:
                            from .capture import ScreenGrabber
                            grabber = ScreenGrabber()
                        if ocr is None:
                            from .ocr import SignatureOCR
                            ocr = SignatureOCR(self._min_value,
                                               self._max_value)
                    except Exception as exc:
                        self.failed.emit(f"Scanner unavailable: {exc}")
                        return

                    try:
                        crop = (demo_mod.signature_crop() if self.demo
                                else grabber.grab(region))
                        # A static screen means the OCR result cannot change;
                        # re-running the model would only burn CPU.
                        digest = hash(crop.tobytes())
                        if digest == self._last_hash and self._last_payload is not None:
                            self.result.emit(*self._last_payload)
                        else:
                            res = ocr.read(crop)
                            matches = self.data.lookup(res.value, self._tolerance) if res.ok else []
                            self._last_hash = digest
                            self._last_payload = (res, matches)
                            self.result.emit(res, matches)
                    except Exception as exc:  # keep the loop alive
                        self.failed.emit(str(exc))
                elapsed = (time.perf_counter() - started) * 1000.0
                self.msleep(max(25, int(self._interval - elapsed)))
        finally:
            if grabber is not None:
                grabber.close()


# ---------------------------------------------------------------------------
def logical_to_physical(rect: QRect) -> dict:
    """Qt global-logical rect -> mss physical-pixel rect."""
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    geo = screen.geometry()
    dpr = float(screen.devicePixelRatio())

    origin_x, origin_y = 0, 0
    for mon in win32.monitors():
        if (abs(mon["width"] - geo.width() * dpr) <= 2
                and abs(mon["height"] - geo.height() * dpr) <= 2):
            origin_x, origin_y = mon["left"], mon["top"]
            break

    return {
        "left": int(round(origin_x + (rect.x() - geo.x()) * dpr)),
        "top": int(round(origin_y + (rect.y() - geo.y()) * dpr)),
        "width": int(round(rect.width() * dpr)),
        "height": int(round(rect.height() * dpr)),
    }


def _tray_icon(size: int = 64) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    k = size / 64.0
    path = QPainterPath()
    path.moveTo(32 * k, 6 * k)
    path.lineTo(58 * k, 32 * k)
    path.lineTo(32 * k, 58 * k)
    path.lineTo(6 * k, 32 * k)
    path.closeSubpath()
    pen = p.pen()
    pen.setColor(QColor(79, 209, 224))
    pen.setWidthF(max(1.0, size / 64.0))
    p.setPen(pen)
    p.setBrush(QColor(16, 26, 34))
    p.drawPath(path)
    p.setPen(QColor(232, 240, 248))
    p.setFont(QFont(theme.UI_FAMILY, max(5, int(round(20 * k))),
                     QFont.Weight.Bold))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "CA")
    p.end()
    return QIcon(pix)


def _install_crash_log() -> Path:
    """Keep unhandled errors on disk when launched with pythonw (no console)."""
    path = config_dir() / "error.log"

    def hook(exc_type, exc, tb) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {getattr(exc_type, '__name__', exc_type)}: {exc}\n")
                fh.write("".join(traceback.format_exception(exc_type, exc, tb)))
                fh.write("\n")
        except OSError:
            pass

    sys.excepthook = hook
    return path


_MUTEX_HANDLE = None        # held open for the life of the process


def _acquire_single_instance(replacing: bool = False) -> bool:
    """Windows named mutex: only one overlay may own the global hotkeys.

    A second copy would start with every window in place but could not
    register F3/F4, so pressing them would appear to do nothing.
    """
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                      wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    name = "Local\\CitizenAssistantOverlay"
    # Restarting ourselves spawns the new copy before this one quits, so the
    # replacement waits for the old process to let go of the mutex.
    deadline = time.monotonic() + (10.0 if replacing else 0.0)
    while True:
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True             # no mutex: never block the app
        if ctypes.get_last_error() != 183:     # ERROR_ALREADY_EXISTS
            _MUTEX_HANDLE = handle
            return True
        kernel32.CloseHandle(handle)
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def _already_running_notice() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(
        None,
        "Citizen Assistant is already running.\n\n"
        "Use its tray icon, or press F3 to show the nav bar.\n\n"
        f"This copy is {version_label()}. If the running copy is older, "
        "quit it from the tray and start this one.",
        APP_TITLE,
        0x00000040)                 # MB_ICONINFORMATION


# ---------------------------------------------------------------------------
class OverlayApp(QObject):
    # Host-facing headless bridges.  The trackers never create windows; the
    # host decides whether to render a toast, update a HUD layer, or ignore it.
    combat_event = Signal(object)
    blueprint_unlocked_signal = Signal(object)
    inventory_event_signal = Signal(object)
    # Re-emitted from the Game.log tailer's thread so the GUI can react.
    pilot_seat = Signal(bool)

    def __init__(self, app: QApplication, cfg: Config, demo: bool = False,
                 capture_visible: bool = False, launch_args: list[str] | None = None):
        super().__init__()
        self.app = app
        self.cfg = cfg
        self.demo = demo
        self.capture_visible = capture_visible
        self._launch_args = list(launch_args or [])

        self.data = MiningData()
        self.voice = Voice()
        self._spoken_key: tuple | None = None
        self._spoken_value: int | None = None
        self._selector: RegionSelector | None = None
        self._recent: deque[int] = deque(maxlen=max(1, int(cfg["stability_reads"])))
        self._last_value: int | None = None
        self._last_matches = []
        self._last_good_at = 0.0
        self._last_res = None
        self._paused = False
        self._last_match_name = ""

        self.nav = NavBar(TOOLS)
        self.panel = MiningPanel()
        self.settings = SettingsPanel()
        self.ore_data = LiveData()
        self.orefinder = OreFinderPanel(self.ore_data)
        self.hauling = HaulingPanel()
        self.inventory = InventoryPanel()
        self.uex = UexPanel()
        self.notes = NotesPanel()

        # Headless combat feed: the tracker stays Qt-free; this panel only
        # renders the structured events it forwards.
        self.combat = CombatTracker()
        self.combat.subscribe(self._on_combat_event)
        self.combat_panel = CombatPanel(self.combat)

        # Headless blueprint/inventory feed.  The log watcher owns matching;
        # the registry worker owns JSON reads and writes.
        self.blueprint_tracker = BlueprintTracker()
        self.blueprint_tracker.subscribe(self._on_inventory_event)

        self.scan = ScanThread(self.data, cfg, demo=demo)
        self.scan.result.connect(self._on_result)
        self.scan.failed.connect(self._on_scan_error)

        self.hotkeys = HotkeyThread({1: int(cfg["hotkey_toggle_nav"]),
                                     2: int(cfg["hotkey_panic"])})
        self.hotkeys.pressed.connect(self._on_hotkey)

        # Read-only Game.log tailer for pilot-seat notifications, so the
        # scanner can close when the player stands up. Nothing touches the
        # game process; the monitor only reads text.
        self.game_log = GameLogMonitor(callback=self._on_game_log_event)
        self.pilot_seat.connect(self._on_pilot_seat)

        self._build_tray()
        self._wire()
        self.app.aboutToQuit.connect(self.hauling.shutdown)
        self.app.aboutToQuit.connect(self.combat.close)
        self.app.aboutToQuit.connect(self.blueprint_tracker.close)
        # Last chance to write the notepad before the process exits.
        self.app.aboutToQuit.connect(self.notes.flush)
        self.app.aboutToQuit.connect(self.game_log.stop)

        self._freshness = QTimer(self)
        self._freshness.setInterval(500)
        self._freshness.timeout.connect(self._check_freshness)

        # Re-assert topmost periodically: games and browsers can out-stack a
        # NOACTIVATE window, which previously left the bar hidden behind Chrome.
        self._topmost = QTimer(self)
        self._topmost.setInterval(2000)
        self._topmost.timeout.connect(self._assert_topmost)

    # ------------------------------------------------------------------
    def _wire(self) -> None:
        self.nav.tool_requested.connect(self._on_tool)
        self.nav.close_requested.connect(self._hide_all)
        self.nav.settings_requested.connect(self._toggle_settings)
        self.panel.close_requested.connect(self._on_panel_close)
        self.orefinder.close_requested.connect(self._on_orefinder_close)
        self.hauling.close_requested.connect(self._on_hauling_close)
        self.inventory.close_requested.connect(self._on_inventory_close)
        self.combat_panel.close_requested.connect(self._on_combat_close)
        self.uex.close_requested.connect(self._on_uex_close)
        self.notes.close_requested.connect(self._on_notes_close)
        self.combat_event.connect(
            self.combat_panel.add_event,
            Qt.ConnectionType.QueuedConnection)
        self.settings.region_requested.connect(self.select_region)
        self.settings.pause_toggled.connect(self._on_pause)
        self.settings.debug_toggled.connect(self._on_debug)
        self.settings.voice_toggled.connect(self._on_voice)
        self.settings.volume_changed.connect(self._on_volume)
        self.settings.opacity_changed.connect(self._on_opacity)
        self.settings.capture_toggled.connect(self._on_capture_visible)
        self.settings.ui_scale_changed.connect(self._on_ui_scale)
        self.settings.restart_requested.connect(self.restart)
        self.settings.reload_requested.connect(self._reload_data)
        self.settings.close_requested.connect(self.settings.hide)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_tray_icon(), self)
        self.tray.setToolTip(APP_TITLE)

        menu = QMenu()
        self.act_show = QAction("Show command bar", self)
        self.act_show.triggered.connect(self._show_bar)
        menu.addAction(self.act_show)

        act_panel = QAction("Mining scanner", self)
        act_panel.triggered.connect(lambda: self._toggle_panel("mining"))
        menu.addAction(act_panel)

        act_inventory = QAction("Inventory notes", self)
        act_inventory.triggered.connect(self._toggle_inventory)
        menu.addAction(act_inventory)

        act_combat = QAction("Combat feed", self)
        act_combat.triggered.connect(self._toggle_combat)
        menu.addAction(act_combat)

        menu.addSeparator()
        act_region = QAction("Select capture region…", self)
        act_region.triggered.connect(self.select_region)
        menu.addAction(act_region)

        act_reload = QAction("Reload signature data", self)
        act_reload.triggered.connect(self._reload_data)
        menu.addAction(act_reload)

        act_folder = QAction("Open config folder", self)
        act_folder.triggered.connect(lambda: open_folder(config_dir()))
        menu.addAction(act_folder)

        menu.addSeparator()
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self._tray_menu = menu
        self.tray.activated.connect(
            lambda reason: self._show_bar()
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    # ------------------------------------------------------------------
    def start(self) -> None:
        # Window styles must be applied *before* the windows are shown: on
        # macOS WA_MacAlwaysShowToolWindow only takes effect at that point,
        # otherwise Qt's tool windows hide whenever the app is deactivated.
        self._apply_window_tweaks()
        self._apply_opacity()

        self.nav.place()
        if self.cfg["nav_visible"] or self.demo:
            self.nav.slide_in()

        pos = self.cfg["mining_panel_pos"]
        if isinstance(pos, list) and len(pos) == 2:
            self.panel.move(int(pos[0]), int(pos[1]))
        else:
            self.panel.place_default()

        has_region = is_region_valid(self.cfg["region"])
        self.settings.set_region(self.cfg["region"] if has_region else None)
        self.settings.set_version(self.data.version, len(self.data.ores))
        self.settings.set_debug(bool(self.cfg["show_debug_preview"]))
        self.voice.set_enabled(bool(self.cfg["voice_enabled"]))
        self.voice.set_volume(float(self.cfg["voice_volume"]))
        self.settings.set_voice(bool(self.cfg["voice_enabled"]))
        self.settings.set_volume(round(float(self.cfg["voice_volume"]) * 100))
        self.settings.set_opacity(round(self._opacity_level() * 100))
        self.settings.set_ui_scale(round(self._ui_scale_level() * 100))
        self.settings.set_capture_visible(self._capture_visible(),
                                          locked=self.capture_visible)
        self.settings.set_voice_hint(
            "" if self.voice.available else "No speech engine available")

        ore_pos = self.cfg.get("orefinder_pos")
        if isinstance(ore_pos, list) and len(ore_pos) == 2:
            self.orefinder.move(int(ore_pos[0]), int(ore_pos[1]))

        self.panel.show()
        if not isinstance(ore_pos, list):
            self.orefinder.place_default(self.panel)

        hauling_pos = self.cfg.get("hauling_pos")
        if isinstance(hauling_pos, list) and len(hauling_pos) == 2:
            self.hauling.move(int(hauling_pos[0]), int(hauling_pos[1]))
        else:
            self.hauling.place_default(self.panel)
        self.hauling.set_preferences(int(self.cfg["hauling_cargo"]),
                                     int(self.cfg["hauling_commodity"]))

        inventory_pos = self.cfg.get("inventory_pos")
        if isinstance(inventory_pos, list) and len(inventory_pos) == 2:
            self.inventory.move(int(inventory_pos[0]), int(inventory_pos[1]))
        else:
            self.inventory.place_default(self.panel)

        combat_pos = self.cfg.get("combat_pos")
        if isinstance(combat_pos, list) and len(combat_pos) == 2:
            self.combat_panel.move(int(combat_pos[0]), int(combat_pos[1]))
        else:
            self.combat_panel.place_default(self.panel)
        # A saved position can belong to a monitor that is no longer attached,
        # which made COMBAT look like it opened nothing. Pull it back on screen.
        self.combat_panel.clamp_to_screen()

        uex_pos = self.cfg.get("uex_pos")
        if isinstance(uex_pos, list) and len(uex_pos) == 2:
            self.uex.move(int(uex_pos[0]), int(uex_pos[1]))
        else:
            self.uex.place_default(self.panel)
        self.uex.clamp_to_screen()

        notes_pos = self.cfg.get("notes_pos")
        if isinstance(notes_pos, list) and len(notes_pos) == 2:
            self.notes.move(int(notes_pos[0]), int(notes_pos[1]))
        else:
            self.notes.place_default(self.panel)
        self.notes.clamp_to_screen()

        # Reopen whatever was on screen last time. A UI-scale restart used to
        # come back with every panel closed, which made the restart feel like a
        # crash instead of a settings change.
        for tool_id in self.cfg.get("open_tools") or []:
            if isinstance(tool_id, str):
                self.open_tool(tool_id)

        # macOS has no permission-free global hotkey API. Register application
        # shortcuts so F3/F4 still work whenever one of our windows has focus;
        # the tray menu is the always-available fallback.
        if not self.hotkeys.supported:
            self._shortcuts = []
            for target in (self.hauling, self.inventory):
                for key, slot in (("F3", self.nav.toggle), ("F4", self._hide_all)):
                    sc = QShortcut(QKeySequence(key), target)
                    sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
                    sc.activated.connect(slot)
                    self._shortcuts.append(sc)

        self.combat.start()
        self.blueprint_tracker.start()
        self.scan.start()
        self._freshness.start()
        self._topmost.start()
        self.hotkeys.start()
        self.game_log.start()

        if not has_region and not self.demo:
            self.panel.set_scan_idle("No capture region — open settings (⚙) to calibrate")
            QTimer.singleShot(650, self.select_region)
        elif self.demo:
            self.panel.set_scan_idle("Demo mode")

    def _apply_window_tweaks(self) -> None:
        for widget in (self.nav, self.panel, self.settings, self.orefinder,
                       self.combat_panel):
            try:
                win32.make_overlay_window(widget)
            except Exception:
                pass
        # HAULING, INVENTORY, UEX and NOTES have text controls, so they must be
        # able to take keyboard focus.
        for widget in (self.hauling, self.inventory, self.uex, self.notes):
            try:
                win32.make_focusable_window(widget)
            except Exception:
                pass
        self._apply_capture_visibility()
        self._assert_topmost()

    def _capture_visible(self) -> bool:
        """Whether the overlay should appear in screenshots and screen shares."""
        return bool(self.capture_visible
                    or not self.cfg.get("capture_exclude", False))

    def _apply_capture_visibility(self) -> None:
        show = self._capture_visible()
        for widget in (self.nav, self.panel, self.settings, self.orefinder,
                       self.hauling, self.inventory, self.uex, self.notes,
                       self.combat_panel):
            try:
                if show:
                    win32.allow_capture(widget)
                else:
                    win32.exclude_from_capture(widget)
            except Exception:
                pass

    def _on_capture_visible(self, visible: bool) -> None:
        # --capture-visible is a development override: keep the toggle honest.
        if self.capture_visible:
            self.settings.set_capture_visible(True, locked=True)
            return
        self.cfg["capture_exclude"] = not bool(visible)
        self.cfg.save()
        self._apply_capture_visibility()

    def _assert_topmost(self) -> None:
        for widget in (self.nav, self.panel, self.orefinder, self.hauling,
                       self.settings, self.inventory, self.uex, self.notes,
                       self.combat_panel):
            if not widget.isVisible():
                continue
            try:
                win32.keep_on_top(widget)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _on_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == 1:
            self.nav.toggle()
        elif hotkey_id == 2:
            self._hide_all()

    def _show_bar(self) -> None:
        if not self.nav.shown:
            self.nav.slide_in()

    def _hide_all(self) -> None:
        if self.nav.shown:
            self.nav.slide_out()
        self.panel.hide()
        self.settings.hide()
        self.orefinder.hide()
        self.hauling.hide()
        self.inventory.hide()
        self.uex.hide()
        self.notes.hide()
        self.combat_panel.hide()
        self.nav.set_active(None)

    def _on_tool(self, tool_id: str) -> None:
        if tool_id == "mining":
            self._toggle_panel("mining")
        elif tool_id == "orefinder":
            self._toggle_orefinder()
        elif tool_id == "hauling":
            self._toggle_hauling()
        elif tool_id == "inventory":
            self._toggle_inventory()
        elif tool_id == "uex":
            self._toggle_uex()
        elif tool_id == "notes":
            self._toggle_notes()
        elif tool_id == "combat":
            self._toggle_combat()

    def open_tool(self, tool_id: str) -> None:
        """Open a tool by id unless it is already visible."""
        if tool_id == "mining" and not self.panel.isVisible():
            self._toggle_panel("mining")
        elif tool_id == "orefinder" and not self.orefinder.isVisible():
            self._toggle_orefinder()
        elif tool_id == "hauling" and not self.hauling.isVisible():
            self._toggle_hauling()
        elif tool_id == "inventory" and not self.inventory.isVisible():
            self._toggle_inventory()
        elif tool_id == "uex" and not self.uex.isVisible():
            self._toggle_uex()
        elif tool_id == "notes" and not self.notes.isVisible():
            self._toggle_notes()
        elif tool_id == "combat" and not self.combat_panel.isVisible():
            self._toggle_combat()
        elif tool_id == "settings" and not self.settings.isVisible():
            self._toggle_settings()

    def _sync_nav_active(self) -> None:
        for tool_id, widget in (("hauling", self.hauling),
                                ("uex", self.uex),
                                ("inventory", self.inventory),
                                ("combat", self.combat_panel),
                                ("notes", self.notes),
                                ("orefinder", self.orefinder),
                                ("mining", self.panel)):
            if widget.isVisible():
                self.nav.set_active(tool_id)
                return
        self.nav.set_active(None)

    def _on_combat_event(self, event) -> None:
        """Forward tracker payloads without creating or touching a window."""
        self.combat_event.emit(event)

    def _on_game_log_event(self, event) -> None:
        """Runs on the log tailer's thread: re-emit, never touch widgets."""
        if event.kind == "pilot_seat" and event.seated is not None:
            self.pilot_seat.emit(bool(event.seated))

    def _on_pilot_seat(self, seated: bool) -> None:
        """Close the mining scanner when the player leaves the pilot seat."""
        if seated:
            return
        if not bool(self.cfg.get("close_scanner_on_seat_exit", True)):
            return
        if self.panel.isVisible():
            self.panel.hide()
            self._sync_nav_active()

    def _on_inventory_event(self, event) -> None:
        """Forward blueprint payloads to the host toast / shopping-list hook."""
        if event.is_blueprint:
            self.blueprint_unlocked_signal.emit(
                event.to_blueprint_dict())
        self.inventory_event_signal.emit(event.to_dict())

    def _toggle_panel(self, tool_id: str) -> None:
        if tool_id != "mining":
            return
        if self.panel.isVisible():
            self.panel.hide()
            self._sync_nav_active()
        else:
            self.panel.show()
            self.panel.raise_()
            self.nav.set_active("mining")
            self._assert_topmost()

    def _toggle_orefinder(self) -> None:
        if self.orefinder.isVisible():
            self.orefinder.hide()
            self._sync_nav_active()
            return

        self.orefinder.show()
        self.orefinder.raise_()
        self.nav.set_active("orefinder")
        self._assert_topmost()

        if self._last_match_name:
            self.orefinder.focus_ore(self._last_match_name)
        self.orefinder.refresh_if_stale()

    def _on_orefinder_close(self) -> None:
        self.orefinder.hide()
        self._sync_nav_active()

    def _toggle_hauling(self) -> None:
        if self.hauling.isVisible():
            self.hauling.hide()
            self._sync_nav_active()
            return
        self.hauling.show()
        self.hauling.raise_()
        self.nav.set_active("hauling")
        self._assert_topmost()
        self.hauling.refresh_if_stale()

    def _on_hauling_close(self) -> None:
        self.hauling.hide()
        self._sync_nav_active()

    def _toggle_inventory(self) -> None:
        if self.inventory.isVisible():
            self.inventory.hide()
            self._sync_nav_active()
            return
        self.inventory.focus_first()
        self.nav.set_active("inventory")
        self._assert_topmost()

    def _on_inventory_close(self) -> None:
        self.inventory.hide()
        self._sync_nav_active()

    def _toggle_uex(self) -> None:
        if self.uex.isVisible():
            self.uex.hide()
            self._sync_nav_active()
            return
        self.uex.clamp_to_screen()
        self.uex.show()
        self.uex.raise_()
        self.nav.set_active("uex")
        self._assert_topmost()

    def _on_uex_close(self) -> None:
        self.uex.hide()
        self._sync_nav_active()

    def _toggle_notes(self) -> None:
        if self.notes.isVisible():
            self.notes.hide()
            self._sync_nav_active()
            return
        self.notes.clamp_to_screen()
        self.notes.show()
        self.notes.raise_()
        self.nav.set_active("notes")
        self._assert_topmost()

    def _on_notes_close(self) -> None:
        self.notes.hide()
        self._sync_nav_active()

    def _toggle_combat(self) -> None:
        if self.combat_panel.isVisible():
            self.combat_panel.hide()
            self._sync_nav_active()
            return
        self.combat_panel.clamp_to_screen()
        self.combat_panel.show()
        self.combat_panel.raise_()
        self.combat_panel.scroll.verticalScrollBar().setValue(0)
        self.nav.set_active("combat")
        self._assert_topmost()

    def _on_combat_close(self) -> None:
        self.combat_panel.hide()
        self._sync_nav_active()

    def _toggle_settings(self) -> None:
        if self.settings.isVisible():
            self.settings.hide()
            return
        self.settings.place_default(self.panel)
        self.settings.show()
        self.settings.raise_()
        self._assert_topmost()
        if self._last_res is not None:
            self.settings.set_preview(self._last_res.crop)

    def _on_panel_close(self) -> None:
        self.panel.hide()
        self._sync_nav_active()

    def _on_pause(self, paused: bool) -> None:
        self._paused = paused
        self.scan.set_enabled(not paused)
        self.panel.set_state("paused" if paused else "reading")
        if paused and self._last_value is not None:
            self.panel.set_scan(self._last_value, 0.0, "lost",
                                self._last_matches, "paused")
        elif not paused:
            # Re-announce whatever is on screen when scanning resumes.
            self._spoken_key = None
            self._spoken_value = None

    def _on_debug(self, enabled: bool) -> None:
        self.cfg["show_debug_preview"] = enabled
        if enabled and self._last_res is not None:
            self.settings.set_preview(self._last_res.crop)

    def _on_voice(self, enabled: bool) -> None:
        self.cfg["voice_enabled"] = bool(enabled)
        self.voice.set_enabled(enabled)
        if enabled:
            # Announce the rock that is already locked.
            self._spoken_key = None
            self._spoken_value = None
        self.settings.set_voice_hint(
            "" if self.voice.available or not enabled
            else "No speech engine available")

    def _on_volume(self, percent: int) -> None:
        level = max(0.0, min(1.0, int(percent) / 100.0))
        self.cfg["voice_volume"] = level
        self.voice.set_volume(level)

    def _on_opacity(self, percent: int) -> None:
        level = max(0.3, min(1.0, int(percent) / 100.0))
        self.cfg["overlay_opacity"] = level
        self._apply_opacity()

    def _opacity_level(self) -> float:
        try:
            return max(0.3, min(1.0,
                                float(self.cfg.get("overlay_opacity", 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def _on_ui_scale(self, percent: int) -> None:
        level = max(0.75, min(1.5, int(percent) / 100.0))
        self.cfg["ui_scale"] = level
        # The panels bake the scale in when they are built, so persist it right
        # away: the RESTART button (or the next launch) reads it back.
        self.cfg.save()

    def _ui_scale_level(self) -> float:
        try:
            return max(0.75, min(1.5,
                                float(self.cfg.get("ui_scale", 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def _apply_opacity(self) -> None:
        level = self._opacity_level()
        # Keep the nav bar and settings window fully visible while adjusting
        # the content panels.
        for widget in (self.panel, self.orefinder, self.hauling,
                       self.inventory, self.combat_panel):
            try:
                widget.setWindowOpacity(level)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def select_region(self) -> None:
        if self._selector is not None:
            self._selector.raise_()
            return
        hint = ("Drag a box around the scan signature"
                if not self.demo else
                "Demo mode: no real region needed")
        self._selector = RegionSelector(hint)
        self._selector.selected.connect(self._on_region_selected)
        self._selector.cancelled.connect(self._on_region_cancelled)
        self._selector.show()

    def _on_region_cancelled(self) -> None:
        self._selector = None
        if not is_region_valid(self.cfg["region"]):
            self.panel.set_scan_idle("Region selection cancelled")

    def _on_region_selected(self, rect: QRect) -> None:
        self._selector = None
        region = logical_to_physical(rect)
        self.cfg["region"] = region
        self.cfg.save()

        self.scan.configure(region=region)
        self._recent.clear()
        self._last_value = None
        self._last_matches = []
        self._spoken_key = None
        self._spoken_value = None
        self._scan_sig_reset()

        self.settings.set_region(region)
        self.panel.set_scan_idle(f"Scanning {region['width']} × {region['height']} px")
        self.panel.set_state("reading")

    def _scan_sig_reset(self) -> None:
        self.panel.reset_cache()

    # ------------------------------------------------------------------
    def _on_result(self, res, matches) -> None:
        if res is not self._last_res:
            self._last_res = res
            self.settings.set_preview(res.crop)

        if not res.ok:
            self._recent.clear()
            return

        self._recent.append(res.value)
        # Refresh the liveness timer on any readable number: "lost" means the
        # HUD left the screen, not that we are unsure which of two readings is
        # right.
        self._last_good_at = time.monotonic()
        needed = max(1, int(self.cfg["stability_reads"]))
        stable = (len(self._recent) >= needed
                  and len(set(self._recent)) == 1)
        top = matches[0] if matches else None
        value = res.value
        # A high-confidence exact signature is trustworthy enough to show on
        # the first read, but only while nothing is on screen. Replacing a
        # number that is already shown needs the same confirmation as a first
        # lock: OCR can alternate between two plausible readings (8,000, and
        # 28,000 when a stray leading digit joins the crop) and both are exact
        # matches, which used to flip the panel on every frame.
        changing = self._last_value is not None and value != self._last_value
        fast_lock = (
            bool(self.cfg.get("fast_single_read", True))
            and top is not None
            and top.exact
            and float(res.confidence) >= float(self.cfg["fast_confidence"])
            and not changing
        )
        if not stable and not fast_lock:
            self.panel.set_state("reading")
            return

        self._last_value = value
        self._last_matches = matches
        if matches and not matches[0].is_ground:
            self._last_match_name = matches[0].name

        self.panel.set_state("stable")
        note = ""
        if not matches:
            near = self.data.nearest(value)
            if near:
                note = f"closest {near[0].name} ×{near[0].count} ({near[0].target:,})"
        self.panel.set_scan(value, res.confidence, "stable", matches, note)

        # Announce only exact signatures: a fuzzy match is usually an OCR
        # misread, and saying the wrong ore is worse than saying nothing.
        if top is not None and top.exact:
            self._announce_signature(value, top)

    def _announce_signature(self, value: int, top) -> None:
        """Speak a locked rock once, not on every read while flying at it.

        The scan HUD can drop and re-acquire the same rock, and OCR can jitter
        the signature by a digit.  Dedupe on the identified ore and treat a
        near-identical value as the same target, so approaching one rock does
        not repeat the announcement.  A genuinely different signature still
        speaks even when the ore type is the same.
        """
        key = (top.kind, top.name, top.count)
        same_target = (
            key == self._spoken_key
            and self._spoken_value is not None
            and abs(int(value) - int(self._spoken_value))
            <= max(2, int(round(abs(int(self._spoken_value)) * 0.02)))
        )
        if same_target:
            return
        self._spoken_key = key
        self._spoken_value = int(value)
        self.voice.say(spoken_text(top.label, top.count))

    def _on_scan_error(self, message: str) -> None:
        self.panel.set_state("error")
        self.panel.set_scan_idle(f"Capture error: {message}")

    def _check_freshness(self) -> None:
        if self._paused:
            return
        if self._last_value is None:
            return
        idle = time.monotonic() - self._last_good_at
        if idle <= 2.0:
            return
        self.panel.set_state("idle")
        self.panel.set_scan(self._last_value, 0.0, "lost", self._last_matches)
        # Keep the spoken signature through brief flicker; only forget it after
        # a real break so a new rock of the same type can announce again.
        try:
            reset_after = float(self.cfg.get("voice_idle_reset_seconds", 12.0))
        except (TypeError, ValueError):
            reset_after = 12.0
        if idle >= max(2.0, reset_after):
            self._spoken_key = None
            self._spoken_value = None

    # ------------------------------------------------------------------
    def _reload_data(self) -> None:
        try:
            self.data.reload()
            self.settings.set_version(self.data.version, len(self.data.ores))
            self.panel.reset_cache()
            self.panel.set_scan_idle(f"Signature data reloaded · {self.data.version}")
        except Exception as exc:
            self.panel.set_state("error")
            self.panel.set_scan_idle(f"Reload failed: {exc}")

    # ------------------------------------------------------------------
    def _save_state(self) -> None:
        try:
            self.cfg["mining_panel_pos"] = [self.panel.x(), self.panel.y()]
            self.cfg["orefinder_pos"] = [self.orefinder.x(), self.orefinder.y()]
            self.cfg["hauling_pos"] = [self.hauling.x(), self.hauling.y()]
            self.cfg["inventory_pos"] = [self.inventory.x(), self.inventory.y()]
            self.cfg["combat_pos"] = [self.combat_panel.x(),
                                      self.combat_panel.y()]
            self.cfg["uex_pos"] = [self.uex.x(), self.uex.y()]
            self.cfg["notes_pos"] = [self.notes.x(), self.notes.y()]
            self.cfg["open_tools"] = [
                tool_id for tool_id, widget in (
                    ("mining", self.panel),
                    ("orefinder", self.orefinder),
                    ("hauling", self.hauling),
                    ("uex", self.uex),
                    ("inventory", self.inventory),
                    ("combat", self.combat_panel),
                    ("notes", self.notes),
                    ("settings", self.settings),
                ) if widget.isVisible()]
            self.cfg["hauling_cargo"] = self.hauling.capacity()
            self.cfg["hauling_commodity"] = self.hauling.current_commodity()
            self.cfg["nav_visible"] = self.nav.shown
            self.cfg.save()
        except Exception:
            pass

    def restart(self) -> None:
        """Relaunch the overlay, e.g. to apply a new UI scale.

        The scale is baked into the widgets when they are built, so applying it
        needs a fresh process.  State is saved first so the new instance opens
        the panels exactly where they are now.
        """
        self._save_state()
        # The replacement is allowed to wait for this process's single-instance
        # mutex; drop any stale flag first so the list cannot grow.
        extra = [a for a in self._launch_args if a != "--restart"]
        if getattr(sys, "frozen", False):
            args = [sys.executable, *extra, "--restart"]
            root = Path(sys.executable).resolve().parent
        else:
            args = [sys.executable, "-m", "app", *extra, "--restart"]
            root = Path(__file__).resolve().parents[1]

        attempts: list[int] = [0]
        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NO_WINDOW: no console window, and the
            # new copy outlives this one.  CREATE_BREAKAWAY_FROM_JOB is tried
            # first so a launcher that runs us inside a job object (some
            # terminals and installers do) cannot kill the restarted overlay
            # when the old process exits; it is rejected by jobs that do not
            # allow breakaway, hence the plain retry.
            detached = 0x00000008 | 0x08000000
            attempts = [detached | 0x01000000, detached]

        started = False
        for flags in attempts:
            kwargs: dict = {"cwd": str(root), "close_fds": True}
            if flags:
                kwargs["creationflags"] = flags
            try:
                subprocess.Popen(args, **kwargs)
                started = True
                break
            except OSError:
                continue
        if not started:
            self.panel.set_state("error")
            self.panel.set_scan_idle("Restart failed - quit and start again")
            return
        self.quit()

    def quit(self) -> None:
        self._save_state()
        self.combat.close()
        self.blueprint_tracker.close()
        self.scan.stop()
        self.scan.wait(1500)
        self.hotkeys.stop()
        self.hotkeys.wait(800)
        self.hauling.shutdown()
        self.voice.stop()
        self.settings.hide()
        self.tray.hide()
        self.app.quit()

    def dispose(self) -> None:
        """Delete the windows while the QApplication is still alive.

        Top-level widgets have no Qt parent, so they are owned by Python.  If
        the interpreter frees the QApplication first, Qt frees those widgets a
        second time in its exit handlers, which on Windows shows up as an
        intermittent access violation (0xC0000005) after a clean quit.
        """
        widgets = (self.nav, self.panel, self.settings, self.orefinder,
                   self.hauling, self.inventory, self.combat_panel)
        for widget in widgets:
            try:
                widget.hide()
                widget.deleteLater()
            except RuntimeError:
                pass          # already deleted by Qt
        try:
            self.tray.hide()
            self.tray.setContextMenu(None)
            self.tray.deleteLater()
            self._tray_menu.deleteLater()
        except RuntimeError:
            pass
        self.app.processEvents()


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    _install_crash_log()
    parser = argparse.ArgumentParser(prog="citizen-assistant", description="Star Citizen overlay")
    parser.add_argument("--demo", action="store_true",
                        help="run the mining scanner against a synthetic signature")
    parser.add_argument("--capture-visible", action="store_true",
                        help="do not hide the overlay from screen capture (debugging/streaming)")
    parser.add_argument("--open", choices=("mining", "orefinder",
                                           "hauling", "inventory", "uex",
                                           "combat", "notes", "settings"),
                        help="open a tool at startup (debugging)")
    parser.add_argument("--restart", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version",
                        version=f"Citizen Assistant {version_label()}")
    args = parser.parse_args(argv)

    if not _acquire_single_instance(replacing=args.restart):
        _already_running_notice()
        return 0

    cfg = Config.load()
    # The overlay scales itself (see ui/theme.py) instead of relying on
    # QT_SCALE_FACTOR: Qt only applies that variable to widget geometry on
    # Windows/Linux, so on macOS the slider appeared to do nothing.
    # The panels are built once, so a new scale needs a restart.
    theme.set_scale(cfg.get("ui_scale", 1.0))

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(QIcon(str(ICON_PATH)) if ICON_PATH.exists()
                      else _tray_icon())
    app.setQuitOnLastWindowClosed(False)

    overlay = OverlayApp(app, cfg, demo=args.demo,
                         capture_visible=args.capture_visible,
                         launch_args=(list(argv) if argv is not None
                                      else sys.argv[1:]))
    app.aboutToQuit.connect(lambda: cfg.save())
    overlay.start()
    if args.open:
        QTimer.singleShot(350, lambda: overlay.open_tool(args.open))
    code = app.exec()
    overlay.dispose()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
