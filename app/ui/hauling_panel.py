"""HAULING: live UEX routes, a contract snapshot and the PPM planner."""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QIntValidator, QPainter, QPalette, QPen)
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QCompleter, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from ..hauling import Hauling, HaulingMission, MissionBoard, Route
from . import theme
from .hauling_planner import PlannerPage

WIDTH = 476
HEIGHT = 630
ROUTE_ROW = 60
CONTRACT_ROW = 66
MAX_ROUTE_ROWS = 500
TOP_COMMODITY = 0

GREEN = QColor(94, 214, 138)
AMBER = QColor(255, 176, 32)
PURPLE = QColor(199, 125, 255)

RANK_COLOR = {
    "Rookie": QColor(144, 190, 109),
    "Junior": QColor(79, 209, 224),
    "Member": PURPLE,
    "Experienced": AMBER,
    "Senior": QColor(232, 106, 106),
    "Master": QColor(255, 214, 102),
}

TABS = (("routes", "TRADE ROUTES"), ("contracts", "CONTRACTS"),
        ("planner", "HAULING PLANNER"))
SORTS = (("TRIP", "TRIP"), ("RATE", "aUEC/SCU"), ("ROI", "ROI"), ("DIST", "DIST"))
RANKS = ("ALL", "ROOKIE", "JUNIOR", "MEMBER", "EXPERIENCED")
MAJOR_HINTS = (
    "TDD", "SPACEPORT", "COMMONS", "BUSINESS DISTRICT", "PROVIDENCE PLATFORM",
    "CLOUDVIEW", "METRO CENTER", "ASPIRE GRAND", "LEVSKI", "AREA 18",
    "LORVILLE", "ORISON", "NEW BABBAGE", "BAIJINI", "EVERUS", "TRESSLER",
    "SERAPHIM", "GATEWAY", "STANTON GATEWAY",
)


def money(value: float) -> str:
    value = float(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M".replace(".00M", "M").replace(".0M", "M")
    if value >= 10_000:
        return f"{value / 1_000:,.1f}k".replace(".0k", "k")
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.0f}"


class _CommodityCombo(QComboBox):
    """Editable combo with a visible chevron (Qt hides the native one in QSS)."""

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(150, 166, 182), 1.3))
        x = w - 13
        y = h // 2
        p.drawLine(x - 3, y - 2, x, y + 1)
        p.drawLine(x, y + 1, x + 3, y - 2)


class _CargoInput(QWidget):
    """Compact numeric cargo field.

    A styled QSpinBox loses its text on macOS, so this is a QLineEdit with
    explicit step buttons instead.
    """

    valueChanged = Signal(int)

    def __init__(self, value: int = 32, parent=None):
        super().__init__(parent)
        self.setFixedSize(theme.px(96), theme.px(24))
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        row.setSpacing(theme.px(2))

        self.minus = QPushButton("−")
        self.plus = QPushButton("+")
        for button in (self.minus, self.plus):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedSize(theme.px(17), theme.px(22))
            button.setStyleSheet(theme.qss("""
                QPushButton { color: rgba(168,184,200,0.9); background: transparent;
                              border: none; border-radius: 4px; font-size: 11px; }
                QPushButton:hover { background: rgba(79,209,224,0.16); color: #e6f7fa; }
            """))

        self.edit = QLineEdit(str(value))
        self.edit.setValidator(QIntValidator(1, 3000, self))
        self.edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.edit.setFont(theme.font(8, QFont.Weight.DemiBold))
        self.edit.setFixedSize(theme.px(38), theme.px(22))
        self.edit.setStyleSheet(theme.qss("""
            QLineEdit {
                color: #e8f0f8; background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10); border-radius: 5px;
                padding: 1px 3px; selection-background-color: rgba(79,209,224,0.35);
            }
            QLineEdit:focus { border-color: rgba(79,209,224,0.65); }
        """))

        self.unit = QLabel("SCU")
        self.unit.setFont(theme.font(7, QFont.Weight.Bold, spacing=0.5))
        self.unit.setStyleSheet(theme.qss("color: rgba(112,128,144,0.95); background: transparent;"))

        row.addWidget(self.minus)
        row.addWidget(self.edit)
        row.addWidget(self.plus)
        row.addWidget(self.unit)

        self.minus.clicked.connect(lambda: self.step(-1))
        self.plus.clicked.connect(lambda: self.step(1))
        self.edit.editingFinished.connect(self._commit)
        self.edit.returnPressed.connect(self._commit)

    def value(self) -> int:
        try:
            return max(1, min(3000, int(self.edit.text())))
        except ValueError:
            return 32

    def setValue(self, value: int) -> None:
        value = max(1, min(3000, int(value)))
        self.edit.blockSignals(True)
        self.edit.setText(str(value))
        self.edit.blockSignals(False)

    def step(self, delta: int) -> None:
        value = max(1, min(3000, self.value() + int(delta)))
        self.setValue(value)
        self.valueChanged.emit(value)

    def _commit(self) -> None:
        value = self.value()
        self.setValue(value)
        self.valueChanged.emit(value)


def _date_text(text: str) -> str:
    months = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    try:
        year, month, day = (int(part) for part in text.split("-"))
        return f"{day:02d} {months[month - 1]} {year}"
    except (ValueError, IndexError):
        return text


def _containers(text: str) -> str:
    bits = [part.strip() for part in (text or "").split(",") if part.strip()]
    return " / ".join(bits) if bits else "unknown"


def _facilities(flags: dict) -> str:
    labels = (("freight", "Freight elevator"), ("docking", "Docking port"),
              ("loading", "Loading dock"), ("cargo", "Cargo center"),
              ("quantum", "Quantum marker"), ("monitored", "Monitored"))
    bits = []
    if flags.get("station"):
        bits.append("Space station")
    elif flags.get("ground"):
        bits.append("Surface")
    bits.extend(label for key, label in labels if flags.get(key))
    return "  ·  ".join(bits) if bits else "No facility data"


# ---------------------------------------------------------------------------
class _CommoditiesWorker(QThread):
    result = Signal(list, str)

    def __init__(self, data: Hauling, force: bool = False):
        super().__init__()
        self._data = data
        self._force = force

    def run(self) -> None:
        try:
            self.result.emit(self._data.commodities(force=self._force),
                             self._data.commodities_error)
        except Exception as exc:  # never take the panel down
            self.result.emit([], str(exc))


class _RoutesWorker(QThread):
    result = Signal(int, list, str)

    def __init__(self, data: Hauling, commodity_id: int, force: bool = False):
        super().__init__()
        self._data = data
        self._id = int(commodity_id)
        self._force = force

    def run(self) -> None:
        try:
            routes = self._data.routes(self._id, force=self._force)
            # Terminal metadata tells cities/stations apart from outposts;
            # fetch it alongside routes so the filters are accurate.
            self._data.terminals()
            self.result.emit(self._id, routes, self._data.routes_error)
        except Exception as exc:
            self.result.emit(self._id, [], str(exc))


class _TopRoutesWorker(QThread):
    progress = Signal(int, int)
    result = Signal(list, str)

    def __init__(self, data: Hauling, force: bool = False):
        super().__init__()
        self._data = data
        self._force = force

    def run(self) -> None:
        try:
            routes = self._data.top_routes(
                force=self._force,
                progress=lambda done, total: self.progress.emit(done, total),
                cancelled=self.isInterruptionRequested)
            self._data.terminals()
            self.result.emit(routes, self._data.top_routes_error)
        except Exception as exc:
            self.result.emit([], str(exc))


class _MissionsWorker(QThread):
    result = Signal(object, str)

    def __init__(self, data: Hauling, force: bool = False):
        super().__init__()
        self._data = data
        self._force = force

    def run(self) -> None:
        try:
            board = self._data.missions(force=self._force)
            self.result.emit(board, self._data.missions_error)
        except Exception as exc:
            self.result.emit(None, str(exc))


# ---------------------------------------------------------------------------
class _RouteRow(QWidget):
    def __init__(self, route: Route, capacity: int, on_click,
                 show_commodity: bool = False, parent=None):
        super().__init__(parent)
        self.route = route
        self.capacity = int(capacity)
        self.show_commodity = show_commodity
        self._on_click = on_click
        self._hover = False
        self.setFixedHeight(theme.px(ROUTE_ROW))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if show_commodity:
            self.setToolTip(f"{route.commodity} · {route.origin} → {route.destination}")
        self.setToolTip(f"{route.origin} → {route.destination}")

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self._on_click(self.route)

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        route = self.route
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 10))
            p.drawRoundedRect(QRectF(0, 1, w, h - 2), 6, 6)

        right = 126
        left = w - right - 14

        p.setFont(theme.pfont(9, QFont.Weight.DemiBold))
        p.setPen(theme.TEXT if self._hover else QColor(216, 228, 240))
        p.drawText(QRectF(4, 4, left, 17), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       route.origin.upper(), Qt.TextElideMode.ElideRight, left))

        p.setFont(theme.pfont(8))
        p.setPen(theme.MUTED)
        second = f"→ {route.destination}"
        if self.show_commodity:
            second = f"{route.commodity.upper()}  →  {route.destination}"
        p.drawText(QRectF(4, 21, left, 15), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       second, Qt.TextElideMode.ElideRight, left))

        trip = route.trip_profit(self.capacity)
        p.setFont(theme.pfont(10, QFont.Weight.DemiBold))
        p.setPen(GREEN)
        p.drawText(QRectF(w - right - 4, 4, right, 17),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"+{money(trip)}")

        p.setFont(theme.pfont(7, QFont.Weight.Medium, spacing=0.4))
        p.setPen(theme.DIM)
        p.drawText(QRectF(w - right - 4, 21, right, 15),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{route.trip_scu(self.capacity)} SCU  ·  {money(route.per_scu)}/SCU")

        bits = [f"ROI {route.roi:.1f}%"]
        if route.distance:
            bits.append(f"{route.distance:,.0f} Gm")
        if route.stock_origin:
            bits.append(f"{route.stock_origin:,} SCU stock")
        p.setFont(theme.pfont(7))
        p.setPen(QColor(104, 120, 136))
        p.drawText(QRectF(4, 38, w - right - 14, 14), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       "  ·  ".join(bits), Qt.TextElideMode.ElideRight,
                       w - right - 18))

        available = route.available
        p.setFont(theme.pfont(7, QFont.Weight.DemiBold, spacing=0.3))
        if route.stock_ok(self.capacity):
            p.setPen(GREEN)
            stock = "STOCK OK"
        elif available:
            p.setPen(AMBER)
            stock = f"ONLY {available:,} SCU"
        else:
            p.setPen(theme.DIM)
            stock = "STOCK ?"
        p.drawText(QRectF(w - right - 4, 38, right, 14),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, stock)
        theme.divider(p, 0, w, h - 1)


class _ContractRow(QWidget):
    def __init__(self, mission: HaulingMission, capacity: int, on_click, parent=None):
        super().__init__(parent)
        self.mission = mission
        self.capacity = int(capacity)
        self._on_click = on_click
        self._hover = False
        self.setFixedHeight(theme.px(CONTRACT_ROW))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(mission.route_summary)

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self._on_click(self.mission)

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        mission = self.mission
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 10))
            p.drawRoundedRect(QRectF(0, 1, w, h - 2), 6, 6)

        p.setFont(theme.pfont(7, QFont.Weight.Bold, spacing=0.9))
        p.setPen(RANK_COLOR.get(mission.rank, theme.ACCENT))
        head = f"{mission.rank.upper()}  ·  {mission.contractor.upper()}"
        p.drawText(QRectF(4, 4, w - 150, 14), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       head, Qt.TextElideMode.ElideRight, w - 158))

        p.setFont(theme.pfont(10, QFont.Weight.DemiBold))
        p.setPen(GREEN)
        p.drawText(QRectF(w - 146, 4, 142, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{money(mission.reward)} aUEC")

        p.setFont(theme.pfont(9, QFont.Weight.DemiBold))
        p.setPen(theme.TEXT if self._hover else QColor(218, 229, 240))
        p.drawText(QRectF(4, 21, w - 8, 16), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       mission.route_summary, Qt.TextElideMode.ElideRight, w - 12))

        tail = f"{mission.scu:,} SCU  ·  {mission.mission_type.upper()}"
        p.setFont(theme.pfont(7))
        p.setPen(theme.DIM)
        p.drawText(QRectF(4, 40, w - 150, 14), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter, tail)

        p.setFont(theme.pfont(8, QFont.Weight.DemiBold))
        if mission.fits(self.capacity):
            p.setPen(GREEN)
            fit = f"FITS {self.capacity} SCU"
        else:
            p.setPen(AMBER)
            fit = f"OVER {mission.over_by(self.capacity):,} SCU"
        p.drawText(QRectF(w - 146, 38, 142, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fit)
        p.setFont(theme.pfont(7))
        p.setPen(theme.DIM)
        p.drawText(QRectF(w - 146, 22, 142, 14),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{money(mission.rate)}/SCU")
        theme.divider(p, 0, w, h - 1)


# ---------------------------------------------------------------------------
class HaulingPanel(QWidget):
    close_requested = Signal()

    def __init__(self, data: Hauling | None = None, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(theme.px(WIDTH), theme.px(HEIGHT))

        self.data = data or Hauling()
        self._tab = "routes"
        self._sort = "TRIP"
        self._rank = "ALL"
        self._fit_only = False
        self._auto_load = False
        self._major_only = False
        self._stock_only = False
        self._shown_routes = 0
        self._displayed_routes = 0
        self._capacity = 32
        self._preferred_commodity = TOP_COMMODITY
        self._commodity_id: int | None = None
        self._route_error = ""
        self._commodity_error = ""
        self._top_error = ""
        self._top_progress = (0, 0)
        self._mission_error = ""
        self._missions_attempted = False
        self._workers: dict[str, QThread] = {}
        self._drag_from = None

        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(5 * 60 * 1000)
        self._auto_timer.timeout.connect(self._auto_refresh)

        board = self.data.missions(refresh=False)
        self._missions: list[HaulingMission] = list(board.missions)
        self._board: MissionBoard = board
        self._routes: list[Route] = []

        self._build()
        self._populate_commodities(self.data.cached_commodities)
        self._rebuild_route_list()
        self._rebuild_contract_list()
        self._update_footer()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(16), theme.px(12), theme.px(16), theme.px(10))
        root.setSpacing(theme.px(6))

        head = QHBoxLayout()
        head.setSpacing(theme.px(8))
        title = QLabel("HAULING")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        title.setStyleSheet(theme.qss("color: rgba(168,184,200,0.95); background: transparent;"))

        self.status = QLabel("IDLE")
        self.status.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.0))

        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.status)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.refresh_btn.setFixedSize(theme.px(22), theme.px(22))
        self.refresh_btn.setToolTip("Re-sync hauling data")
        self.refresh_btn.setStyleSheet(theme.qss("""
            QPushButton { color: rgba(168,184,200,0.85); background: transparent;
                          border: none; border-radius: 11px; font-size: 13px; }
            QPushButton:hover { background: rgba(79,209,224,0.14); color: #e6f7fa; }
        """))
        self.refresh_btn.clicked.connect(self.refresh)

        close = QPushButton("✕")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setFixedSize(theme.px(22), theme.px(22))
        close.setStyleSheet(theme.qss("""
            QPushButton { color: rgba(150,164,180,0.8); background: transparent;
                          border: none; font-size: 12px; }
            QPushButton:hover { color: #ffd9d9; }
        """))
        close.clicked.connect(self.close_requested.emit)

        head.addWidget(self.refresh_btn)
        head.addWidget(close)
        root.addLayout(head)

        # Tabs + cargo capacity -----------------------------------------
        tabs = QHBoxLayout()
        tabs.setSpacing(theme.px(5))
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        for key, label in TABS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == "routes")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedHeight(theme.px(24))
            button.setStyleSheet(theme.qss(self._tab_qss()))
            button.clicked.connect(lambda _=False, k=key: self._set_tab(k))
            self._tab_group.addButton(button)
            tabs.addWidget(button)

        tabs.addStretch(1)
        cargo = QLabel("CARGO")
        cargo.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.0))
        cargo.setStyleSheet(theme.qss("color: rgba(112,128,144,0.95); background: transparent;"))
        tabs.addWidget(cargo)

        self.capacity_box = _CargoInput(self._capacity)
        self.capacity_box.valueChanged.connect(self._on_capacity)
        tabs.addWidget(self.capacity_box)
        root.addLayout(tabs)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_routes_page())
        self.stack.addWidget(self._build_route_detail())
        self.stack.addWidget(self._build_contracts_page())
        self.stack.addWidget(self._build_contract_detail())
        self.planner = PlannerPage()
        self.planner.state_changed.connect(self._on_planner_state)
        self.stack.addWidget(self.planner)
        root.addWidget(self.stack, 1)

        self.footer = QLabel("")
        self.footer.setFont(theme.font(7, QFont.Weight.Medium, spacing=0.8))
        self.footer.setStyleSheet(theme.qss("color: rgba(102,118,134,0.95); background: transparent;"))
        self.footer.setWordWrap(True)
        root.addWidget(self.footer)

    # -- trade route page ----------------------------------------------
    def _build_routes_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(theme.qss("background: transparent;"))
        layout = QVBoxLayout(page)
        layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        layout.setSpacing(theme.px(4))

        self.commodity = _CommodityCombo()
        self.commodity.setEditable(True)
        self.commodity.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.commodity.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.commodity.setMaxVisibleItems(14)
        self.commodity.setFixedHeight(theme.px(30))
        self.commodity.setFont(theme.font(9))
        self.commodity.setStyleSheet(theme.qss(self._combo_qss()))
        self.commodity.lineEdit().setPlaceholderText("Search a commodity…")
        self.commodity.lineEdit().setStyleSheet(theme.qss("background: transparent; border: none;"
                                                "color: #e8f0f8; padding: 0 2px;"))
        self.commodity.lineEdit().setTextMargins(0, 0, 18, 0)
        palette = self.commodity.lineEdit().palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 137, 155))
        self.commodity.lineEdit().setPalette(palette)
        completer = QCompleter(self.commodity.model(), self.commodity)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.commodity.setCompleter(completer)
        self.commodity.currentIndexChanged.connect(self._on_commodity_changed)
        layout.addWidget(self.commodity)

        filters = QHBoxLayout()
        filters.setSpacing(theme.px(5))
        self.auto_button = QPushButton("AUTO-LOAD")
        self.auto_button.setCheckable(True)
        self.auto_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.auto_button.setFixedHeight(theme.px(22))
        self.auto_button.setToolTip(
            "Only runs where both ends are a city or station with a freight "
            "elevator — no planetary outposts")
        self.auto_button.setStyleSheet(theme.qss(self._chip_qss()))
        self.auto_button.clicked.connect(lambda: self._toggle_route_filter("auto"))
        filters.addWidget(self.auto_button)

        self.major_button = QPushButton("CITIES + STATIONS")
        self.major_button.setCheckable(True)
        self.major_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.major_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.major_button.setFixedHeight(theme.px(22))
        self.major_button.setToolTip(
            "Hide planetary outposts and other small, tight-clearance locations")
        self.major_button.setStyleSheet(theme.qss(self._chip_qss()))
        self.major_button.clicked.connect(lambda: self._toggle_route_filter("major"))
        filters.addWidget(self.major_button)

        self.stock_button = QPushButton("RELIABLE STOCK")
        self.stock_button.setCheckable(True)
        self.stock_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stock_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stock_button.setFixedHeight(theme.px(22))
        self.stock_button.setToolTip(
            "Only runs with enough reported stock to fill the cargo capacity")
        self.stock_button.setStyleSheet(theme.qss(self._chip_qss()))
        self.stock_button.clicked.connect(lambda: self._toggle_route_filter("stock"))
        filters.addWidget(self.stock_button)
        filters.addStretch(1)
        layout.addLayout(filters)

        sorts = QHBoxLayout()
        sorts.setSpacing(theme.px(5))
        self._sort_group = QButtonGroup(self)
        self._sort_group.setExclusive(True)
        for key, label in SORTS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == self._sort)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedHeight(theme.px(22))
            button.setStyleSheet(theme.qss(self._chip_qss()))
            button.clicked.connect(lambda _=False, k=key: self._set_sort(k))
            self._sort_group.addButton(button)
            sorts.addWidget(button)
        sorts.addStretch(1)
        layout.addLayout(sorts)

        caps = QHBoxLayout()
        caps.setContentsMargins(theme.px(12), theme.px(2), theme.px(12), theme.px(1))
        left = QLabel("ORIGIN  →  DESTINATION")
        right = QLabel("TRIP PROFIT")
        for label in (left, right):
            label.setFont(theme.font(7, QFont.Weight.Medium, spacing=1.0))
            label.setStyleSheet(theme.qss("color: rgba(88,103,120,0.95); background: transparent;"))
        caps.addWidget(left)
        caps.addStretch(1)
        caps.addWidget(right)
        layout.addLayout(caps)

        self.route_scroll = QScrollArea()
        self.route_scroll.setWidgetResizable(True)
        self.route_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.route_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.route_scroll.setStyleSheet(theme.qss(self._scroll_qss()))
        self.route_host = QWidget()
        self.route_host.setStyleSheet(theme.qss("background: transparent;"))
        self.route_layout = QVBoxLayout(self.route_host)
        self.route_layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(4), theme.px(0))
        self.route_layout.setSpacing(theme.px(0))
        self.route_layout.addStretch(1)
        self.route_scroll.setWidget(self.route_host)
        layout.addWidget(self.route_scroll, 1)
        return page

    # -- contract page --------------------------------------------------
    def _build_contracts_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(theme.qss("background: transparent;"))
        layout = QVBoxLayout(page)
        layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        layout.setSpacing(theme.px(4))

        filters = QHBoxLayout()
        filters.setSpacing(theme.px(5))
        self._rank_group = QButtonGroup(self)
        self._rank_group.setExclusive(True)
        for rank in RANKS:
            button = QPushButton(rank)
            button.setCheckable(True)
            button.setChecked(rank == "ALL")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedHeight(theme.px(22))
            button.setStyleSheet(theme.qss(self._chip_qss()))
            button.clicked.connect(lambda _=False, r=rank: self._set_rank(r))
            self._rank_group.addButton(button)
            filters.addWidget(button)
        filters.addStretch(1)

        self.fit_button = QPushButton("FITS")
        self.fit_button.setCheckable(True)
        self.fit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fit_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fit_button.setFixedHeight(theme.px(22))
        self.fit_button.setToolTip("Only contracts that fit the cargo capacity")
        self.fit_button.setStyleSheet(theme.qss(self._chip_qss()))
        self.fit_button.clicked.connect(self._toggle_fit)
        filters.addWidget(self.fit_button)
        layout.addLayout(filters)

        caps = QHBoxLayout()
        caps.setContentsMargins(theme.px(12), theme.px(2), theme.px(12), theme.px(1))
        left = QLabel("CONTRACT  ·  ROUTE")
        right = QLabel("aUEC / SCU")
        for label in (left, right):
            label.setFont(theme.font(7, QFont.Weight.Medium, spacing=1.0))
            label.setStyleSheet(theme.qss("color: rgba(88,103,120,0.95); background: transparent;"))
        caps.addWidget(left)
        caps.addStretch(1)
        caps.addWidget(right)
        layout.addLayout(caps)

        self.contract_scroll = QScrollArea()
        self.contract_scroll.setWidgetResizable(True)
        self.contract_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.contract_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.contract_scroll.setStyleSheet(theme.qss(self._scroll_qss()))
        self.contract_host = QWidget()
        self.contract_host.setStyleSheet(theme.qss("background: transparent;"))
        self.contract_layout = QVBoxLayout(self.contract_host)
        self.contract_layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(4), theme.px(0))
        self.contract_layout.setSpacing(theme.px(0))
        self.contract_layout.addStretch(1)
        self.contract_scroll.setWidget(self.contract_host)
        layout.addWidget(self.contract_scroll, 1)
        return page

    # -- detail pages ---------------------------------------------------
    def _build_route_detail(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(theme.qss("background: transparent;"))
        layout = QVBoxLayout(page)
        layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        layout.setSpacing(theme.px(4))

        back = QPushButton("‹  ALL ROUTES")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        back.setFixedHeight(theme.px(22))
        back.setStyleSheet(theme.qss(self._back_qss()))
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        layout.addWidget(back)

        self.route_detail_scroll = QScrollArea()
        self.route_detail_scroll.setWidgetResizable(True)
        self.route_detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.route_detail_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.route_detail_scroll.setStyleSheet(theme.qss(self._scroll_qss()))
        self.route_detail_host = QWidget()
        self.route_detail_host.setStyleSheet(theme.qss("background: transparent;"))
        self.route_detail_layout = QVBoxLayout(self.route_detail_host)
        self.route_detail_layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(6), theme.px(0))
        self.route_detail_layout.setSpacing(theme.px(2))
        self.route_detail_scroll.setWidget(self.route_detail_host)
        layout.addWidget(self.route_detail_scroll, 1)
        return page

    def _build_contract_detail(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(theme.qss("background: transparent;"))
        layout = QVBoxLayout(page)
        layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        layout.setSpacing(theme.px(4))

        back = QPushButton("‹  ALL CONTRACTS")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        back.setFixedHeight(theme.px(22))
        back.setStyleSheet(theme.qss(self._back_qss()))
        back.clicked.connect(lambda: self.stack.setCurrentIndex(2))
        layout.addWidget(back)

        self.contract_detail_scroll = QScrollArea()
        self.contract_detail_scroll.setWidgetResizable(True)
        self.contract_detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.contract_detail_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.contract_detail_scroll.setStyleSheet(theme.qss(self._scroll_qss()))
        self.contract_detail_host = QWidget()
        self.contract_detail_host.setStyleSheet(theme.qss("background: transparent;"))
        self.contract_detail_layout = QVBoxLayout(self.contract_detail_host)
        self.contract_detail_layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(6), theme.px(0))
        self.contract_detail_layout.setSpacing(theme.px(2))
        self.contract_detail_scroll.setWidget(self.contract_detail_host)
        layout.addWidget(self.contract_detail_scroll, 1)
        return page

    # ------------------------------------------------------------------
    @staticmethod
    def _tab_qss() -> str:
        return """
            QPushButton {
                color: rgba(130,148,166,0.9); background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.06); border-radius: 6px;
                font-size: 7px; font-weight: 700;
                letter-spacing: 1.1px; padding: 0 10px;
            }
            QPushButton:hover { color: #d6e2ee; border-color: rgba(79,209,224,0.35); }
            QPushButton:checked {
                color: #4fd1e0; background: rgba(79,209,224,0.13);
                border-color: rgba(79,209,224,0.5);
            }
        """

    @staticmethod
    def _chip_qss() -> str:
        return """
            QPushButton {
                color: rgba(130,148,166,0.9); background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.06); border-radius: 5px;
                font-size: 7px; font-weight: 600;
                letter-spacing: 0.8px; padding: 0 7px;
            }
            QPushButton:hover { color: #d6e2ee; border-color: rgba(79,209,224,0.35); }
            QPushButton:checked {
                color: #4fd1e0; background: rgba(79,209,224,0.13);
                border-color: rgba(79,209,224,0.5);
            }
        """

    @staticmethod
    def _back_qss() -> str:
        return """
            QPushButton { color: rgba(130,148,166,0.95); background: transparent;
                          border: none; text-align: left;
                          font-size: 8px; font-weight: 600; letter-spacing: 1.1px; }
            QPushButton:hover { color: #4fd1e0; }
        """

    @staticmethod
    def _scroll_qss() -> str:
        return """
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }
            QScrollBar::handle:vertical {
                background: rgba(79,209,224,0.22); border-radius: 3px; min-height: 26px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(79,209,224,0.42); }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
            QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
        """

    @staticmethod
    def _combo_qss() -> str:
        return """
            QComboBox {
                color: #e8f0f8; background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10); border-radius: 8px;
                padding: 3px 26px 3px 10px;
            }
            QComboBox:focus, QComboBox:on {
                border-color: rgba(79,209,224,0.65);
                background: rgba(79,209,224,0.07);
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                color: #e8f0f8; background: #10161f;
                border: 1px solid rgba(79,209,224,0.35);
                selection-background-color: rgba(79,209,224,0.25);
                selection-color: #ffffff; outline: none;
            }
        """

    # ------------------------------------------------------------------
    def set_preferences(self, capacity: int, commodity_id: int) -> None:
        self._capacity = max(1, min(3000, int(capacity)))
        self._preferred_commodity = max(0, int(commodity_id))
        self.capacity_box.setValue(self._capacity)
        self.planner.set_capacity(self._capacity)
        if self.commodity.count():
            index = self.commodity.findData(self._preferred_commodity)
            if index >= 0:
                self.commodity.blockSignals(True)
                self.commodity.setCurrentIndex(index)
                self.commodity.blockSignals(False)
                self._commodity_id = self._preferred_commodity
                if self._commodity_id == TOP_COMMODITY:
                    self._routes = self.data.cached_top_routes()
                else:
                    self._routes = self.data.cached_routes(self._commodity_id)
        self._rebuild_route_list()
        self._rebuild_contract_list()

    def current_commodity(self) -> int:
        return int(self._commodity_id or self._preferred_commodity)

    def capacity(self) -> int:
        return int(self._capacity)

    def current_tab(self) -> str:
        return self._tab

    # ------------------------------------------------------------------
    def _set_tab(self, key: str) -> None:
        self._tab = key
        for (tab_key, _label), button in zip(TABS,
                                             self._tab_group.buttons()):
            button.setChecked(tab_key == key)
        if key == "routes":
            self.stack.setCurrentIndex(0)
            self._rebuild_route_list()
        elif key == "planner":
            self.stack.setCurrentIndex(4)
            self.planner.activate()
        else:
            self.stack.setCurrentIndex(2)
            self._rebuild_contract_list()
        self._update_footer()
        self._update_status()

    def _set_sort(self, key: str) -> None:
        self._sort = key
        self._rebuild_route_list()

    def _set_rank(self, rank: str) -> None:
        self._rank = rank
        self._rebuild_contract_list()
        self._update_footer()

    def _toggle_fit(self) -> None:
        self._fit_only = self.fit_button.isChecked()
        self._rebuild_contract_list()
        self._update_footer()

    def _toggle_route_filter(self, key: str) -> None:
        if key == "auto":
            self._auto_load = self.auto_button.isChecked()
        elif key == "major":
            self._major_only = self.major_button.isChecked()
        elif key == "stock":
            self._stock_only = self.stock_button.isChecked()
        self._rebuild_route_list()
        self._update_footer()

    def _major_endpoint(self, terminal_id: int, flags: dict, name: str) -> bool:
        known = self.data.is_major_terminal(terminal_id)
        if known is not None:
            return known
        # Fallback while terminal metadata is still loading.
        if flags.get("station"):
            return True
        upper = (name or "").upper()
        return any(hint in upper for hint in MAJOR_HINTS)

    def _auto_endpoint(self, terminal_id: int, flags: dict, name: str) -> bool:
        return (self._major_endpoint(terminal_id, flags, name)
                and bool(flags.get("freight")))

    def _route_auto_ok(self, route: Route) -> bool:
        return (self._auto_endpoint(route.origin_id, route.flags_origin, route.origin)
                and self._auto_endpoint(route.destination_id,
                                        route.flags_destination, route.destination))

    def _route_major_ok(self, route: Route) -> bool:
        return (self._major_endpoint(route.origin_id, route.flags_origin, route.origin)
                and self._major_endpoint(route.destination_id,
                                         route.flags_destination, route.destination))

    def _on_capacity(self, value: int) -> None:
        self._capacity = max(1, min(3000, int(value)))
        self.planner.set_capacity(self._capacity)
        self._rebuild_route_list()
        self._rebuild_contract_list()
        self._update_footer()

    def _on_commodity_changed(self, index: int) -> None:
        if index < 0:
            return
        value = self.commodity.itemData(index)
        if value is None:
            return
        self._commodity_id = int(value)
        self._preferred_commodity = self._commodity_id
        if self._commodity_id == TOP_COMMODITY:
            self._routes = self.data.cached_top_routes()
            self._rebuild_route_list()
            self._fetch_top_routes()
        else:
            self._routes = self.data.cached_routes(self._commodity_id)
            self._rebuild_route_list()
            self._fetch_routes(self._commodity_id)
        self._update_footer()
        self._update_status()

    def _populate_commodities(self, items: list) -> None:
        self.commodity.blockSignals(True)
        self.commodity.clear()
        self.commodity.addItem("All commodities — top routes", TOP_COMMODITY)
        for item in items:
            self.commodity.addItem(item.name, item.id)
        wanted = self._preferred_commodity
        index = self.commodity.findData(wanted)
        if index < 0 and self.commodity.count():
            index = 0
        if index >= 0:
            self.commodity.setCurrentIndex(index)
            self._commodity_id = int(self.commodity.itemData(index) or wanted)
            if self._commodity_id == TOP_COMMODITY:
                self._routes = self.data.cached_top_routes()
            else:
                self._routes = self.data.cached_routes(self._commodity_id)
        self.commodity.blockSignals(False)
        self._rebuild_route_list()
        self._update_footer()

    # ------------------------------------------------------------------
    @staticmethod
    def _clear(layout: QVBoxLayout) -> None:
        while layout.count() > 1:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_route_list(self) -> None:
        if not hasattr(self, "route_layout"):
            return
        self._clear(self.route_layout)
        routes = list(self._routes)
        capacity = self._capacity

        if self._auto_load:
            routes = [route for route in routes if self._route_auto_ok(route)]
        if self._major_only:
            routes = [route for route in routes if self._route_major_ok(route)]
        if self._stock_only:
            routes = [route for route in routes if route.stock_ok(capacity)]
        self._shown_routes = len(routes)

        if self._sort == "RATE":
            routes.sort(key=lambda route: (-route.per_scu, -route.trip_profit(capacity)))
        elif self._sort == "ROI":
            routes.sort(key=lambda route: (-route.roi, -route.trip_profit(capacity)))
        elif self._sort == "DIST":
            routes.sort(key=lambda route: (route.distance if route.distance else 1e9,
                                           -route.trip_profit(capacity)))
        else:
            routes.sort(key=lambda route: (-route.trip_profit(capacity),
                                           -route.per_scu))

        self._displayed_routes = min(len(routes), MAX_ROUTE_ROWS)
        if not routes:
            if (self._commodity_id == TOP_COMMODITY
                    and "top" in self._workers):
                done, total = self._top_progress
                text = (f"Scanning all commodities for the best routes…\n\n"
                        f"{done}/{total} commodities checked")
            elif "routes" in self._workers:
                text = "Loading live commodity routes from UEX…"
            elif self._commodity_id == TOP_COMMODITY and self._top_error:
                text = f"Could not load the top routes.\n\n{self._top_error}"
            elif self._route_error:
                text = f"Could not load routes.\n\n{self._route_error}"
            elif not self.commodity.count():
                text = "No commodity list yet. Press ↻ to sync."
            elif self._auto_load or self._major_only or self._stock_only:
                text = "No routes match these filters. Try turning one off."
            elif self._commodity_id == TOP_COMMODITY:
                text = "No runnable top routes right now. Press ↻ to refresh."
            else:
                text = "No profitable routes for this commodity right now."
            self.route_layout.insertWidget(0, self._empty_label(text))
        else:
            show_commodity = self._commodity_id == TOP_COMMODITY
            for route in routes[:MAX_ROUTE_ROWS]:
                self.route_layout.insertWidget(
                    self.route_layout.count() - 1,
                    _RouteRow(route, capacity, self._show_route_detail,
                              show_commodity=show_commodity))

    def _rebuild_contract_list(self) -> None:
        if not hasattr(self, "contract_layout"):
            return
        self._clear(self.contract_layout)
        missions = list(self._missions)
        if self._rank != "ALL":
            missions = [mission for mission in missions
                        if mission.rank.upper() == self._rank]
        if self._fit_only:
            missions = [mission for mission in missions
                        if mission.fits(self._capacity)]
        missions.sort(key=lambda mission: (-mission.rate, -mission.reward))

        if not missions:
            if not self._missions:
                text = "No contract snapshot loaded."
            else:
                text = "No contracts match these filters."
            self.contract_layout.insertWidget(0, self._empty_label(text))
        else:
            for mission in missions:
                self.contract_layout.insertWidget(
                    self.contract_layout.count() - 1,
                    _ContractRow(mission, self._capacity,
                                 self._show_contract_detail))

    @staticmethod
    def _empty_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(theme.font(9))
        label.setStyleSheet(theme.qss("color: rgba(130,148,166,0.9); padding: 26px 8px;"
                            "background: transparent;"))
        return label

    # ------------------------------------------------------------------
    def _show_route_detail(self, route: Route) -> None:
        layout = self.route_detail_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.route_detail_scroll.verticalScrollBar().setValue(0)
        self.stack.setCurrentIndex(1)

        name = QLabel(route.commodity.upper())
        name.setFont(theme.hud_font(22))
        name.setStyleSheet(theme.qss(f"color: {theme.ACCENT.name()}; background: transparent;"))
        layout.addWidget(name)

        direction = QLabel(f"{route.origin}  →  {route.destination}")
        direction.setWordWrap(True)
        direction.setFont(theme.font(9, QFont.Weight.DemiBold))
        direction.setStyleSheet(theme.qss("color: rgba(220,232,244,0.96); background: transparent;"))
        layout.addWidget(direction)

        place = "  ·  ".join(bit for bit in (
            route.origin_place, route.origin_system,
            route.destination_place, route.destination_system) if bit)
        meta = QLabel(place)
        meta.setFont(theme.font(7, QFont.Weight.Medium, spacing=0.4))
        meta.setStyleSheet(theme.qss("color: rgba(150,166,182,0.95); background: transparent;"))
        layout.addWidget(meta)
        layout.addSpacing(theme.px(6))

        layout.addWidget(self._section("MARGIN", f"UEX SCORE {route.score:,.0f}"))
        layout.addWidget(self._stat("BUY  /  SCU", money(route.price_origin), theme.TEXT))
        layout.addWidget(self._stat("SELL  /  SCU", money(route.price_destination), theme.TEXT))
        layout.addWidget(self._stat("PROFIT  /  SCU", f"+{money(route.per_scu)}", GREEN))
        layout.addWidget(self._stat("ROI  /  MARGIN", f"{route.roi:.1f}%  ·  {route.margin:.1f}%",
                                    theme.ACCENT))
        layout.addWidget(self._stat("STOCK AT ORIGIN",
                                    f"{route.stock_origin:,} SCU" if route.stock_origin
                                    else "not reported", theme.TEXT))
        if route.stock_ok(self._capacity):
            layout.addWidget(self._stat("RELIABLE STOCK",
                                        f"yes — fills {self._capacity} SCU", GREEN))
        else:
            layout.addWidget(self._stat("RELIABLE STOCK",
                                        f"only {route.available:,} SCU reported", AMBER))
        layout.addWidget(self._stat("ENDPOINTS", self._endpoint_summary(route), theme.TEXT))
        layout.addWidget(self._stat("DISTANCE",
                                    f"{route.distance:,.0f} Gm" if route.distance else "—",
                                    theme.MUTED))

        layout.addSpacing(theme.px(6))
        layout.addWidget(self._section("TRIP", f"@ {self._capacity} SCU"))
        scu = route.trip_scu(self._capacity)
        layout.addWidget(self._stat("LOAD", f"{scu:,} SCU", theme.TEXT))
        layout.addWidget(self._stat("BUY COST", f"{money(route.trip_cost(self._capacity))} aUEC",
                                    AMBER))
        layout.addWidget(self._stat("TRIP PROFIT", f"+{money(route.trip_profit(self._capacity))} aUEC",
                                    GREEN))
        if route.stock_origin and route.stock_origin < self._capacity:
            note = QLabel("Limited by origin stock. Confirm availability in game.")
            note.setWordWrap(True)
            note.setFont(theme.font(7))
            note.setStyleSheet(theme.qss("color: rgba(214,168,74,0.9); background: transparent;"))
            layout.addWidget(note)

        layout.addSpacing(theme.px(6))
        layout.addWidget(self._section("ORIGIN TERMINAL", route.origin_code))
        layout.addWidget(self._text_line(_facilities(route.flags_origin)))
        layout.addWidget(self._stat("CONTAINER SIZES", _containers(route.containers_origin),
                                    theme.MUTED))

        layout.addSpacing(theme.px(6))
        layout.addWidget(self._section("DESTINATION TERMINAL", route.destination_code))
        layout.addWidget(self._text_line(_facilities(route.flags_destination)))
        layout.addWidget(self._stat("CONTAINER SIZES",
                                    _containers(route.containers_destination), theme.MUTED))

        layout.addSpacing(theme.px(8))
        layout.addWidget(self._note(
            "UEX API 2.0 · community price/stock data, refreshed hourly by UEX. "
            "Routes auto-refresh every 5 minutes while this panel is open; "
            "stock can still change before you arrive — verify at the terminal."))
        layout.addStretch(1)
        layout.activate()

    def _endpoint_summary(self, route: Route) -> str:
        def label(terminal_id: int, flags: dict, name: str) -> str:
            known = self.data.is_major_terminal(terminal_id)
            if known is True:
                return "CITY / STATION"
            if known is False:
                return "OUTPOST"
            return "STATION" if flags.get("station") else "OUTPOST"
        return (f"{label(route.origin_id, route.flags_origin, route.origin)}"
                f"  →  {label(route.destination_id, route.flags_destination, route.destination)}")

    def _show_contract_detail(self, mission: HaulingMission) -> None:
        layout = self.contract_detail_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.contract_detail_scroll.verticalScrollBar().setValue(0)
        self.stack.setCurrentIndex(3)

        title = QLabel(f"{mission.rank.upper()} HAUL")
        title.setFont(theme.hud_font(22))
        title.setStyleSheet(theme.qss(f"color: {RANK_COLOR.get(mission.rank, theme.ACCENT).name()};"
                            "background: transparent;"))
        layout.addWidget(title)

        contractor = QLabel(f"{mission.contractor}  ·  {mission.mission_type.upper()}")
        contractor.setWordWrap(True)
        contractor.setFont(theme.font(8, QFont.Weight.DemiBold))
        contractor.setStyleSheet(theme.qss("color: rgba(190,204,220,0.96); background: transparent;"))
        layout.addWidget(contractor)

        route = QLabel(mission.route_summary)
        route.setWordWrap(True)
        route.setFont(theme.font(9))
        route.setStyleSheet(theme.qss("color: rgba(150,166,182,0.95); background: transparent;"))
        layout.addWidget(route)
        layout.addSpacing(theme.px(6))

        layout.addWidget(self._section("PAYOUT", mission.system.upper() or "STANTON"))
        layout.addWidget(self._stat("REWARD", f"{mission.reward:,} aUEC", GREEN))
        layout.addWidget(self._stat("TOTAL CARGO", f"{mission.scu:,} SCU", theme.TEXT))
        layout.addWidget(self._stat("RATE", f"{money(mission.rate)} aUEC / SCU", theme.ACCENT))
        if mission.fits(self._capacity):
            layout.addWidget(self._stat("FIT", f"fits {self._capacity} SCU", GREEN))
        else:
            layout.addWidget(self._stat("FIT", f"over by {mission.over_by(self._capacity):,} SCU",
                                        AMBER))

        cargo = ", ".join(mission.cargo_types) or "—"
        layout.addWidget(self._stat("CARGO", cargo, theme.TEXT))

        layout.addSpacing(theme.px(6))
        legs = len(mission.deliveries)
        layout.addWidget(self._section("DELIVERIES", f"{legs} LEG" + ("S" if legs != 1 else "")))
        for delivery in mission.deliveries:
            layout.addWidget(self._delivery_row(delivery))

        layout.addSpacing(theme.px(8))
        source = self._board.source or "SCHaulingPlanner"
        captured = _date_text(self._board.captured) if self._board.captured else "unknown date"
        layout.addWidget(self._note(
            f"Community snapshot of in-game mission boards · {source} · captured {captured}. "
            "NOT LIVE. Payouts and availability change with patches and reputation — "
            "confirm the contract in your mobiGlas before accepting."))
        layout.addStretch(1)
        layout.activate()

    # -- small detail helpers ------------------------------------------
    def _section(self, left: str, right: str = "") -> QWidget:
        holder = QWidget()
        holder.setStyleSheet(theme.qss("background: transparent;"))
        row = QHBoxLayout(holder)
        row.setContentsMargins(theme.px(0), theme.px(10), theme.px(0), theme.px(4))
        row.setSpacing(theme.px(6))

        label = QLabel(left)
        label.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.4))
        label.setStyleSheet(theme.qss("color: rgba(136,152,170,0.98); background: transparent;"))
        row.addWidget(label)
        row.addStretch(1)
        if right:
            tail = QLabel(right)
            tail.setFont(theme.font(7, QFont.Weight.Medium, spacing=0.7))
            tail.setStyleSheet(theme.qss("color: rgba(102,118,134,0.95); background: transparent;"))
            row.addWidget(tail)
        return holder

    @staticmethod
    def _stat(label: str, value: str, colour: QColor) -> QWidget:
        holder = QWidget()
        holder.setStyleSheet(theme.qss("background: transparent;"))
        row = QHBoxLayout(holder)
        row.setContentsMargins(theme.px(0), theme.px(1), theme.px(0), theme.px(1))
        row.setSpacing(theme.px(10))

        left = QLabel(label)
        left.setFont(theme.font(7, QFont.Weight.Medium, spacing=0.7))
        left.setStyleSheet(theme.qss("color: rgba(110,126,142,0.95); background: transparent;"))
        right = QLabel(value)
        right.setFont(theme.font(8, QFont.Weight.DemiBold))
        right.setStyleSheet(theme.qss(f"color: {colour.name()}; background: transparent;"))
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        left.setSizePolicy(left.sizePolicy().horizontalPolicy(),
                           left.sizePolicy().verticalPolicy())
        row.addWidget(left, 1)
        row.addWidget(right)
        return holder

    @staticmethod
    def _text_line(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(theme.font(8))
        label.setStyleSheet(theme.qss("color: rgba(198,212,226,0.95); background: transparent;"))
        return label

    @staticmethod
    def _note(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(theme.font(7))
        label.setStyleSheet(theme.qss("color: rgba(120,136,152,0.95); background: transparent;"))
        return label

    @staticmethod
    def _delivery_row(delivery) -> QWidget:
        holder = QWidget()
        holder.setStyleSheet(theme.qss("background: transparent;"))
        column = QVBoxLayout(holder)
        column.setContentsMargins(theme.px(0), theme.px(4), theme.px(0), theme.px(3))
        column.setSpacing(theme.px(1))

        top = QLabel(f"{delivery.scu:,} SCU  ·  {delivery.cargo.upper()}")
        top.setFont(theme.font(8, QFont.Weight.DemiBold))
        top.setStyleSheet(theme.qss("color: rgba(232,240,248,0.95); background: transparent;"))
        bottom = QLabel(f"{', '.join(delivery.pickups) or '?'}  →  {delivery.destination}")
        bottom.setWordWrap(True)
        bottom.setFont(theme.font(7))
        bottom.setStyleSheet(theme.qss("color: rgba(130,148,166,0.95); background: transparent;"))
        column.addWidget(top)
        column.addWidget(bottom)
        return holder

    # ------------------------------------------------------------------
    def _update_footer(self) -> None:
        if self._tab == "planner":
            self.footer.setText(self.planner.footer_text())
            self._update_status()
            return
        if self._tab == "contracts":
            captured = _date_text(self._board.captured) if self._board.captured else "CACHED"
            parts = ["COMMUNITY SNAPSHOT", captured, "NOT LIVE"]
            if self._mission_error:
                parts.append("OFFLINE")
            self.footer.setText("  ·  ".join(parts))
        else:
            parts = ["UEX API 2.0"]
            cid = self._commodity_id
            filtered = (self._auto_load or self._major_only or self._stock_only)
            if cid is not None:
                if cid == TOP_COMMODITY:
                    updated = self.data.top_routes_updated()
                    if updated:
                        parts.append("UPDATED " + time.strftime(
                            "%H:%M", time.localtime(updated)))
                    if "top" in self._workers and not self._routes:
                        done, total = self._top_progress
                        parts.append(f"SCAN {done}/{total}" if total else "SCANNING")
                    elif filtered:
                        parts.append(
                            f"TOP {self._displayed_routes:,}/{self._shown_routes:,} MATCH")
                    else:
                        parts.append(
                            f"TOP {self._displayed_routes:,}/{len(self._routes):,}")
                    parts.append("AUTO 5M")
                else:
                    updated = self.data.routes_updated(cid)
                    if updated:
                        parts.append("UPDATED " + time.strftime(
                            "%H:%M", time.localtime(updated)))
                    if filtered:
                        parts.append(f"{self._shown_routes}/{len(self._routes)} ROUTES")
                    else:
                        parts.append(f"{len(self._routes)} ROUTES")
                    parts.append("AUTO 5M")
            else:
                parts.append("SELECT A COMMODITY")
            if ((self._commodity_id == TOP_COMMODITY and self._top_error)
                    or self._route_error):
                parts.append("OFFLINE")
            self.footer.setText("  ·  ".join(parts))
        self._update_status()

    def _update_status(self) -> None:
        if self._tab == "planner":
            self._set_status(self.planner.status_text(),
                             self.planner.status_colour())
            return
        if self._tab == "contracts":
            if "missions" in self._workers:
                self._set_status("UPDATING", theme.AMBER.name())
            elif not self._missions:
                self._set_status("NO DATA", theme.BAD.name())
            elif self._mission_error:
                self._set_status("CACHED", theme.AMBER.name())
            else:
                self._set_status("SNAPSHOT", theme.AMBER.name())
            return

        if self._commodity_id == TOP_COMMODITY and "top" in self._workers:
            done, total = self._top_progress
            label = f"SYNC {done}/{total}" if total else "SYNCING"
            self._set_status(label, theme.AMBER.name())
        elif "routes" in self._workers or "commodities" in self._workers:
            self._set_status("SYNCING", theme.AMBER.name())
        elif self._commodity_id == TOP_COMMODITY:
            if self._top_error and not self._routes:
                self._set_status("OFFLINE", theme.BAD.name())
            elif self._routes:
                self._set_status("LIVE", theme.GOOD.name())
            else:
                self._set_status("IDLE")
        elif self._route_error or self._commodity_error:
            self._set_status("OFFLINE", theme.BAD.name())
        elif self._routes:
            self._set_status("LIVE", theme.GOOD.name())
        else:
            self._set_status("IDLE")

    def _on_planner_state(self) -> None:
        self._update_footer()
        self._update_status()

    def _set_status(self, text: str, colour: str = "rgba(130,148,166,0.9)") -> None:
        self.status.setText(text)
        self.status.setStyleSheet(theme.qss(f"color: {colour}; background: rgba(255,255,255,0.04);"
                                  "border-radius: 5px; padding: 2px 6px;"))

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._tab == "planner":
            self.planner.refresh()
            return
        if self._tab == "contracts":
            self._fetch_missions(force=True)
        else:
            if not self.commodity.count():
                self._fetch_commodities(force=True)
            elif self._commodity_id == TOP_COMMODITY:
                self._fetch_top_routes(force=True)
            elif self._commodity_id is not None:
                self._fetch_routes(self._commodity_id, force=True)

    def refresh_if_stale(self) -> None:
        if self._tab == "planner":
            self.planner.activate()
            return
        if not self.commodity.count():
            self._fetch_commodities()
        elif self._commodity_id == TOP_COMMODITY:
            self._fetch_top_routes()
        elif self._commodity_id is not None:
            self._fetch_routes(self._commodity_id)
        if not self._missions_attempted:
            self._fetch_missions()

    def _fetch_top_routes(self, force: bool = False) -> None:
        if "top" in self._workers:
            return
        worker = _TopRoutesWorker(self.data, force=force)
        worker.progress.connect(self._on_top_progress)
        worker.result.connect(self._on_top_routes_done)
        worker.finished.connect(lambda: self._on_worker_finished("top"))
        self._workers["top"] = worker
        self._top_progress = (0, max(0, self.commodity.count() - 1))
        self._top_error = ""
        self._update_status()
        worker.start()

    def _fetch_commodities(self, force: bool = False) -> None:
        if "commodities" in self._workers:
            return
        worker = _CommoditiesWorker(self.data, force=force)
        worker.result.connect(self._on_commodities_done)
        worker.finished.connect(lambda: self._on_worker_finished("commodities"))
        self._workers["commodities"] = worker
        self._update_status()
        worker.start()

    def _fetch_routes(self, commodity_id: int, force: bool = False) -> None:
        if "routes" in self._workers:
            return
        worker = _RoutesWorker(self.data, commodity_id, force=force)
        worker.result.connect(self._on_routes_done)
        worker.finished.connect(lambda: self._on_worker_finished("routes"))
        self._workers["routes"] = worker
        self._update_status()
        worker.start()

    def _fetch_missions(self, force: bool = False) -> None:
        if "missions" in self._workers:
            return
        self._missions_attempted = True
        worker = _MissionsWorker(self.data, force=force)
        worker.result.connect(self._on_missions_done)
        worker.finished.connect(lambda: self._on_worker_finished("missions"))
        self._workers["missions"] = worker
        self._update_status()
        worker.start()

    # ------------------------------------------------------------------
    def _on_top_progress(self, done: int, total: int) -> None:
        self._top_progress = (int(done), int(total))
        if self._commodity_id == TOP_COMMODITY and not self._routes:
            self._rebuild_route_list()
        self._update_footer()

    def _on_top_routes_done(self, routes: list, error: str) -> None:
        self._top_error = error
        if self._commodity_id == TOP_COMMODITY:
            self._routes = list(routes)
            self._rebuild_route_list()
        self._update_footer()
        self._update_status()

    def _on_commodities_done(self, items: list, error: str) -> None:
        self._commodity_error = error
        if items:
            self._populate_commodities(items)
            if self._commodity_id == TOP_COMMODITY:
                self._fetch_top_routes()
            elif (self._commodity_id is not None
                  and not self.data.has_routes(self._commodity_id)):
                self._fetch_routes(self._commodity_id)
        self._update_footer()

    def _on_routes_done(self, commodity_id: int, routes: list, error: str) -> None:
        if commodity_id == self._commodity_id:
            self._route_error = error
            self._routes = list(routes)
            self._rebuild_route_list()
        self._update_footer()

    def _on_missions_done(self, board, error: str) -> None:
        self._mission_error = error
        if board is not None and board.missions:
            self._board = board
            self._missions = list(board.missions)
            self._rebuild_contract_list()
        self._update_footer()

    def _on_worker_finished(self, key: str) -> None:
        worker = self._workers.pop(key, None)
        if worker is not None:
            worker.deleteLater()
        if key in ("routes", "top"):
            self._rebuild_route_list()
        self._update_footer()

    # ------------------------------------------------------------------
    def _auto_refresh(self) -> None:
        if not self.isVisible() or self._tab != "routes":
            return
        if self._commodity_id is None:
            return
        if self._commodity_id == TOP_COMMODITY:
            if "top" not in self._workers:
                self._fetch_top_routes()
            return
        if "routes" not in self._workers:
            self._fetch_routes(self._commodity_id, force=True)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._auto_timer.start()

    def hideEvent(self, event):  # noqa: N802
        self._auto_timer.stop()
        super().hideEvent(event)

    def shutdown(self, timeout_ms: int = 30000) -> None:
        """Stop background workers before the application exits."""
        self._auto_timer.stop()
        self.planner.shutdown(timeout_ms)
        for worker in list(self._workers.values()):
            try:
                worker.requestInterruption()
            except RuntimeError:
                continue
            if worker.isRunning():
                worker.wait(timeout_ms)
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
        self._workers.clear()

    # ------------------------------------------------------------------
    def place_default(self, anchor: QWidget | None = None) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        if anchor is not None and anchor.isVisible():
            x = anchor.x() + anchor.width() + 12
            if x + self.width() > geo.right() - 8:
                x = anchor.x() - self.width() - 12
            if x < geo.left() + 8:
                x = anchor.x()
            y = min(anchor.y(), geo.bottom() - self.height() - 8)
            self.move(max(geo.left() + 8, x), max(geo.top() + 8, y))
        else:
            self.move(geo.center().x() - self.width() // 2, geo.top() + 132)

    def clamp_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.geometry().center()) \
            or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(self.x(), geo.left() - 20), geo.right() - self.width() + 20)
        y = min(max(self.y(), geo.top()), geo.bottom() - 60)
        self.move(x, y)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 34:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._drag_from is not None:
            self._drag_from = None
            self.clamp_to_screen()

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        theme.paint_card(p, QRectF(0, 0, w, h), radius=14,
                         fill=QColor(10, 14, 20, 250))
        p.setPen(QPen(QColor(79, 209, 224, 90), 1.0))
        p.drawLine(16, 1, w - 16, 1)
