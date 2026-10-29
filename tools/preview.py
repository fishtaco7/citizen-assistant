"""Render the overlay windows to PNGs for quick visual checks (no game needed).

    python -m tools.preview

HAULING, ORE FINDER and the planner use the network for their sample data;
without a connection they render their cached or offline states.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.combat import CombatEvent, CombatSessionStats  # noqa: E402
from app.inventory import InventoryStore  # noqa: E402
from app.main import TOOLS  # noqa: E402
from app.mining import MiningData  # noqa: E402
from app.orefinder import LiveData  # noqa: E402
from app.ui.combat_panel import CombatPanel  # noqa: E402
from app.ui.hauling_panel import HaulingPanel  # noqa: E402
from app.ui.inventory_panel import InventoryPanel  # noqa: E402
from app.ui.mining_panel import MiningPanel  # noqa: E402
from app.ui.navbar import NavBar  # noqa: E402
from app.ui.orefinder_panel import OreFinderPanel  # noqa: E402
from app.ui.settings_panel import SettingsPanel  # noqa: E402
from app.ui import theme  # noqa: E402

OUT = Path(__file__).resolve().parent / "preview_out"


def main() -> int:
    scale = 1.0
    for arg in sys.argv[1:]:
        if arg.startswith("--scale="):
            scale = float(arg.split("=", 1)[1])
    out = OUT if scale == 1.0 else OUT / f"scale_{round(scale * 100)}"
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    theme.set_scale(scale)
    data = MiningData()

    def snap(widget, name: str) -> None:
        """Grab a widget after pending layout work has been flushed."""
        app.processEvents()
        widget.grab().save(str(out / name))

    nav = NavBar(TOOLS)
    nav.place()
    nav.show()

    panel = MiningPanel()
    panel.set_state("stable")
    panel.set_scan(7200, 0.82, "stable", data.lookup(7200))

    settings = SettingsPanel()
    settings.set_region({"left": 2560, "top": 980, "width": 190, "height": 46})
    settings.set_version(data.version, len(data.ores))

    ore_data = LiveData()
    finder = OreFinderPanel(ore_data)
    hauling = HaulingPanel()
    hauling.set_preferences(32, 0)

    inventory_store = InventoryStore(
        Path(tempfile.mkdtemp()) / "inventory.json")
    inventory_store.add("Seraphim Station", "FS-9 LMG", "2",
                        "Blue locker near the armory")
    inventory_store.add("Area 18", "Vulture salvage", "12 SCU",
                        "Left in the hangar storage")
    inventory_store.add("CRU-L1 Ambitious Dream Station", "Coda pistol", "1",
                        "Medical bay locker")
    inventory = InventoryPanel(inventory_store)
    inventory._set_sort("LOCATION")

    combat = CombatPanel()
    now = time.time()
    for event in (
        CombatEvent("target_state", "player_kill", now - 48,
                    "Destroyed", "VictimOne", "Actor",
                    attacker="KillerOne", weapon="FS-9"),
        CombatEvent("target_state", "vehicle_destruction", now - 36,
                    "Soft-Dead", "Gladius", "Vehicle",
                    attacker="Pirate", weapon="S3 Laser"),
        CombatEvent("vehicle_damage", "vehicle_damage", now - 25,
                    "Active", "Cutlass", "Vehicle",
                    attacker="Pirate", weapon="S2 Gun"),
        CombatEvent("target_state", "vehicle_destruction", now - 14,
                    "Destroyed", "Arrow", "Vehicle",
                    attacker="Pirate", weapon="Missile"),
    ):
        combat.add_event(event)
    combat.damage_btn.setChecked(True)
    combat.set_stats(CombatSessionStats(
        session_started_at=now - 3600, updated_at=now,
        kills=4, deaths=1, soft_dead_targets=2, destroyed_targets=3))

    nav.move(0, 0)
    panel.move(0, 80)
    settings.move(0, 300)
    finder.move(0, 580)
    hauling.move(460, 0)
    inventory.move(920, 0)
    combat.move(920, 640)
    panel.show()
    settings.show()
    finder.show()
    hauling.show()
    hauling.refresh_if_stale()
    finder.refresh_if_stale()
    inventory.show()
    combat.show()

    def grab_basic():
        snap(nav, "navbar.png")
        snap(panel, "panel.png")
        panel.set_scan(6000, 0.81, "stable", data.lookup(6000))
        snap(panel, "panel_debris.png")
        panel.set_scan(7200, 0.82, "stable", data.lookup(7200))
        snap(settings, "settings.png")
        snap(finder, "orefinder_list.png")
        inventory.location.setCurrentIndex(-1)
        inventory.location.clearEditText()
        snap(inventory, "inventory.png")
        snap(combat, "combat.png")
        wait_ore()

    ore_attempts = [0]

    def wait_ore():
        # The ore index may still be downloading on a cold cache.
        if finder.focus_ore("Bexalite"):
            QTimer.singleShot(250, grab_ore_detail)
        elif ore_attempts[0] < 30:
            ore_attempts[0] += 1
            QTimer.singleShot(500, wait_ore)
        else:
            print("no Bexalite in cache")
            QTimer.singleShot(0, wait_hauling)

    def grab_ore_detail():
        snap(finder, "orefinder_detail.png")
        print("ores:", len(ore_data), "patch:", ore_data.patch,
              "synced:", ore_data.synced_text())
        wait_hauling()

    hauling_attempts = [0]

    def wait_hauling():
        # HAULING fetches in the background. Give it up to ~20 s, then render
        # whatever state it has so the preview still completes offline.
        if (not hauling._routes and not hauling._route_error
                and not hauling._top_error and hauling_attempts[0] < 60):
            hauling_attempts[0] += 1
            QTimer.singleShot(500, wait_hauling)
            return
        snap(hauling, "hauling_routes.png")
        hauling.auto_button.setChecked(True)
        hauling.major_button.setChecked(True)
        hauling.stock_button.setChecked(True)
        hauling._toggle_route_filter("auto")
        hauling._toggle_route_filter("major")
        hauling._toggle_route_filter("stock")
        QTimer.singleShot(300, grab_hauling_filters)

    def grab_hauling_filters():
        snap(hauling, "hauling_filters.png")
        routes = [
            route for route in hauling._routes
            if hauling._route_auto_ok(route)
            and hauling._route_major_ok(route)
            and route.stock_ok(hauling.capacity())
        ] or hauling._routes
        if routes:
            hauling._show_route_detail(routes[0])
            QTimer.singleShot(400, grab_hauling_route_detail)
        else:
            grab_hauling_contracts()

    def grab_hauling_route_detail():
        snap(hauling, "hauling_route_detail.png")
        hauling.stack.setCurrentIndex(0)
        hauling.auto_button.setChecked(False)
        hauling.major_button.setChecked(False)
        hauling.stock_button.setChecked(False)
        hauling._toggle_route_filter("auto")
        hauling._toggle_route_filter("major")
        hauling._toggle_route_filter("stock")
        hauling._set_tab("contracts")
        QTimer.singleShot(350, grab_hauling_contracts)

    def grab_hauling_contracts():
        snap(hauling, "hauling_contracts.png")
        if hauling._missions:
            hauling._show_contract_detail(hauling._missions[0])
            QTimer.singleShot(400, grab_hauling_contract_detail)
        else:
            finish_preview()

    def grab_hauling_contract_detail():
        snap(hauling, "hauling_contract_detail.png")
        hauling._set_tab("planner")
        planner_attempts[0] = 0
        QTimer.singleShot(400, wait_planner)

    planner_attempts = [0]

    def wait_planner():
        planner = hauling.planner
        if planner._running or (planner._result is None and planner_attempts[0] < 60):
            planner_attempts[0] += 1
            QTimer.singleShot(500, wait_planner)
            return
        # Let the event loop lay out the 500 rows before grabbing.
        QTimer.singleShot(500, grab_planner)

    def grab_planner():
        snap(hauling, "hauling_planner.png")
        finish_preview()

    def finish_preview():
        print("saved to", out)
        hauling.shutdown()
        app.quit()

    QTimer.singleShot(700, grab_basic)
    code = app.exec()
    # Delete the windows before the QApplication: see OverlayApp.dispose().
    for widget in (nav, panel, settings, finder, hauling, inventory, combat):
        widget.hide()
        widget.deleteLater()
    app.processEvents()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
