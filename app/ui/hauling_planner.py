"""HAULING PLANNER tab: PPM-ranked routes with a non-blocking worker."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QIntValidator,
                           QPainter)
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ..planner import (PlannerConfig, PlannerDataController, PlannerResult,
                       PlannerRoute, build_plan)
from . import theme

GREEN = QColor(94, 214, 138)
AMBER = QColor(255, 176, 32)
RED = QColor(232, 106, 106)
ROW_HEIGHT = 90


def _money(value: float) -> str:
    value = float(value or 0)
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:,.0f}"


class _PlannerWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)
    progress = Signal(str, int, int)

    def __init__(self, config: PlannerConfig, force_refresh: bool,
                 controller_factory: Callable[[], PlannerDataController],
                 parent=None):
        super().__init__(parent)
        self.config = config
        self.force_refresh = force_refresh
        self.controller_factory = controller_factory

    def run(self) -> None:  # noqa: D102
        try:
            result = build_plan(
                self.config,
                controller=self.controller_factory(),
                force_refresh=self.force_refresh,
                progress=lambda text, done, total: self.progress.emit(
                    text, done, total),
                cancelled=self.isInterruptionRequested,
            )
            self.ready.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc) or "planner failed")


class _PlannerRow(QWidget):
    def __init__(self, route: PlannerRoute, parent=None):
        super().__init__(parent)
        self.route = route
        self._hover = False
        self.setFixedHeight(theme.px(ROW_HEIGHT))
        self.setToolTip(
            f"{route.commodity}: {route.buy_station} → {route.sell_station}\n"
            f"Path: {route.route_text}\n"
            f"PPM: {route.ppm:,.0f} aUEC/min\n"
            f"Gross profit: {route.gross_profit:,.0f} aUEC\n"
            f"Investment: {route.investment:,.0f} aUEC\n"
            f"Duration: {route.duration_minutes:.1f} min\n"
            f"Load: {route.load_minutes:.1f} min · QT: "
            f"{route.qt_minutes:.1f} min · Jump: {route.jump_minutes:.1f} min"
            f" · Atmo: {route.atmo_minutes:.1f} min\n"
            f"Auto-load: {'yes' if route.auto_load else 'manual'}\n"
            f"High gravity: {'yes' if route.high_gravity else 'no'}")

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        route = self.route
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 10))
            p.drawRoundedRect(QRectF(0, 1, w, h - 3), 6, 6)

        p.setFont(theme.pfont(7, QFont.Weight.Bold, spacing=1.0))
        p.setPen(theme.ACCENT)
        p.drawText(QRectF(4, 3, w - 126, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       route.commodity.upper(), Qt.TextElideMode.ElideRight,
                       w - 126))

        p.setFont(theme.pfont(10, QFont.Weight.Bold))
        p.setPen(GREEN)
        ppm_text = f"{_money(route.ppm)}"
        p.drawText(QRectF(w - 122, 2, 118, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   ppm_text)
        p.setFont(theme.pfont(6, QFont.Weight.Medium, spacing=0.5))
        p.setPen(QColor(100, 180, 130))
        p.drawText(QRectF(w - 122, 17, 118, 11),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "aUEC / MIN")

        p.setFont(theme.pfont(10, QFont.Weight.DemiBold))
        p.setPen(theme.TEXT if self._hover else QColor(218, 229, 240))
        route_text = f"{route.buy_station}  →  {route.sell_station}"
        p.drawText(QRectF(4, 29, w - 10, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       route_text, Qt.TextElideMode.ElideRight, w - 10))

        p.setFont(theme.pfont(7))
        p.setPen(theme.MUTED)
        metrics = (f"PROFIT {_money(route.gross_profit)}   "
                   f"INVEST {_money(route.investment)}   "
                   f"{route.trip_scu} SCU   {route.duration_minutes:.1f} MIN")
        p.drawText(QRectF(4, 49, w - 10, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       metrics, Qt.TextElideMode.ElideRight, w - 10))

        p.setFont(theme.pfont(6, spacing=0.3))
        p.setPen(QColor(110, 128, 145))
        path_text = route.route_text
        p.drawText(QRectF(4, 65, w - 150, 15),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       path_text, Qt.TextElideMode.ElideRight, w - 150))

        p.setFont(theme.pfont(6, QFont.Weight.Bold, spacing=0.7))
        badge = "AUTO" if route.auto_load else "MANUAL"
        p.setPen(GREEN if route.auto_load else AMBER)
        p.drawText(QRectF(w - 145, 65, 66, 15),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   badge)
        gravity = "LOW-G" if not route.high_gravity else "ATMO"
        p.setPen(GREEN if not route.high_gravity else QColor(150, 132, 105))
        p.drawText(QRectF(w - 76, 65, 72, 15),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   gravity)

        theme.divider(p, 0, w, h - 1)


class PlannerPage(QWidget):
    state_changed = Signal()

    def __init__(self, controller_factory=None, parent=None):
        super().__init__(parent)
        self._controller_factory = (controller_factory
                                    or PlannerDataController)
        self._capacity = 32
        self._auto = False
        self._lowg = False
        self._sort = "ppm"
        self._routes: list[PlannerRoute] = []
        self._result: PlannerResult | None = None
        self._error = ""
        self._progress = ("", 0, 0)
        self._running = False
        self._started = False
        self._dirty = False
        self._pending: tuple[bool, bool] | None = None
        self._worker: _PlannerWorker | None = None

        self._capital_timer = QTimer(self)
        self._capital_timer.setSingleShot(True)
        self._capital_timer.setInterval(350)
        self._capital_timer.timeout.connect(self._restart)

        self._build()
        self._rebuild_list()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        root.setSpacing(theme.px(4))

        controls = QHBoxLayout()
        controls.setSpacing(theme.px(5))
        cap_label = QLabel("CAPITAL")
        cap_label.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.0))
        cap_label.setStyleSheet(theme.qss(
            "color: rgba(112,128,144,0.95); background: transparent;"))
        controls.addWidget(cap_label)

        self.capital_edit = QLineEdit()
        self.capital_edit.setPlaceholderText("NO LIMIT")
        self.capital_edit.setMaxLength(10)
        self.capital_edit.setFixedSize(theme.px(82), theme.px(24))
        self.capital_edit.setFont(theme.font(8))
        self.capital_edit.setValidator(
            QIntValidator(0, 1_000_000_000, self))
        self.capital_edit.setToolTip(
            "Maximum aUEC to invest in one run. Blank means no limit.")
        self.capital_edit.setStyleSheet(theme.qss("""
            QLineEdit {
                color: #e8f0f8; background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10); border-radius: 5px;
                padding: 1px 6px; font-size: 8px;
            }
            QLineEdit:focus {
                border-color: rgba(79,209,224,0.65);
                background: rgba(79,209,224,0.07);
            }
        """))
        self.capital_edit.textChanged.connect(
            lambda _text: self._capital_timer.start())
        controls.addWidget(self.capital_edit)

        auec = QLabel("aUEC")
        auec.setFont(theme.font(7))
        auec.setStyleSheet(theme.qss(
            "color: rgba(112,128,144,0.95); background: transparent;"))
        controls.addWidget(auec)
        controls.addStretch(1)

        self.auto_button = QPushButton("AUTO-LOAD")
        self.auto_button.setCheckable(True)
        self.auto_button.setToolTip(
            "Only routes where both ends are a major station with freight "
            "elevators")
        self.auto_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.auto_button.setFixedHeight(theme.px(22))
        self.auto_button.setStyleSheet(theme.qss(self._chip_qss()))
        self.auto_button.clicked.connect(self._filters_changed)
        controls.addWidget(self.auto_button)

        self.lowg_button = QPushButton("LOW GRAVITY")
        self.lowg_button.setCheckable(True)
        self.lowg_button.setToolTip(
            "Only orbital stations and low-gravity moon locations; removes "
            "high-gravity cities and planet-side outposts")
        self.lowg_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lowg_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lowg_button.setFixedHeight(theme.px(22))
        self.lowg_button.setStyleSheet(theme.qss(self._chip_qss()))
        self.lowg_button.clicked.connect(self._filters_changed)
        controls.addWidget(self.lowg_button)

        self.refresh_button = QPushButton("REFRESH")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.refresh_button.setFixedHeight(theme.px(22))
        self.refresh_button.setToolTip("Re-sync all UEX route and station data")
        self.refresh_button.setStyleSheet(theme.qss(self._chip_qss()))
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_button)
        root.addLayout(controls)

        ranks = QHBoxLayout()
        ranks.setSpacing(theme.px(5))
        sort_label = QLabel("RANK BY")
        sort_label.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.0))
        sort_label.setStyleSheet(theme.qss(
            "color: rgba(112,128,144,0.95); background: transparent;"))
        ranks.addWidget(sort_label)

        self._sort_group = QButtonGroup(self)
        self._sort_group.setExclusive(True)
        self._sort_buttons: dict[str, QPushButton] = {}
        for key, label in (("ppm", "PPM"), ("profit", "PROFIT"),
                           ("time", "TIME"), ("investment", "INVEST")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == self._sort)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedHeight(theme.px(21))
            button.setStyleSheet(theme.qss(self._chip_qss()))
            button.clicked.connect(lambda _=False, k=key: self._set_sort(k))
            self._sort_group.addButton(button)
            self._sort_buttons[key] = button
            ranks.addWidget(button)
        ranks.addStretch(1)
        limit = QLabel("TOP 500")
        limit.setFont(theme.font(6, QFont.Weight.Medium, spacing=0.8))
        limit.setStyleSheet(theme.qss(
            "color: rgba(90,105,120,0.95); background: transparent;"))
        ranks.addWidget(limit)
        root.addLayout(ranks)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(theme.qss(self._scroll_qss()))
        self.host = QWidget()
        self.host.setStyleSheet(theme.qss("background: transparent;"))
        self.list_layout = QVBoxLayout(self.host)
        self.list_layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(4), theme.px(0))
        self.list_layout.setSpacing(theme.px(0))
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.host)
        root.addWidget(self.scroll, 1)

    @staticmethod
    def _chip_qss() -> str:
        return """
            QPushButton {
                color: rgba(130,148,166,0.9); background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.06); border-radius: 5px;
                font-size: 7px; font-weight: 600;
                letter-spacing: 0.7px; padding: 0 7px;
            }
            QPushButton:hover { color: #d6e2ee; border-color: rgba(79,209,224,0.35); }
            QPushButton:checked {
                color: #4fd1e0; background: rgba(79,209,224,0.13);
                border-color: rgba(79,209,224,0.5);
            }
        """

    @staticmethod
    def _scroll_qss() -> str:
        return """
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }
            QScrollBar::handle:vertical {
                background: rgba(79,209,224,0.22); border-radius: 3px;
                min-height: 26px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(79,209,224,0.42); }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
            QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
        """

    @staticmethod
    def _empty_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(theme.font(9))
        label.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.9); padding: 26px 8px;"
            "background: transparent;"))
        return label

    # ------------------------------------------------------------------
    @property
    def routes(self) -> list[PlannerRoute]:
        return list(self._routes)

    def set_capacity(self, capacity: int) -> None:
        capacity = max(1, min(3000, int(capacity)))
        if capacity == self._capacity:
            return
        self._capacity = capacity
        self._dirty = True
        if self._started and self.isVisible():
            self._restart()

    def activate(self) -> None:
        self._started = True
        if self._dirty:
            self._dirty = False
            self._start(False)
        elif not self._running and self._result is None:
            self._start(False)

    def refresh(self) -> None:
        self._start(True)

    # ------------------------------------------------------------------
    def _capital_value(self) -> float:
        text = self.capital_edit.text().strip()
        try:
            return max(0.0, float(text))
        except ValueError:
            return 0.0

    def _config(self) -> PlannerConfig:
        return PlannerConfig(
            cargo_capacity=self._capacity,
            max_trading_capital=self._capital_value(),
            filter_auto_load_only=self.auto_button.isChecked(),
            avoid_high_gravity=self.lowg_button.isChecked(),
            sort_by=self._sort,
        ).clean()

    def _filters_changed(self) -> None:
        self._restart()

    def _restart(self) -> None:
        if not self._started or not self.isVisible():
            self._dirty = True
            return
        self._start(False)

    def _start(self, force_refresh: bool) -> None:
        # Wait for the finished signal as well as the running flag: a result is
        # emitted just before QThread finishes, and starting a second worker in
        # that gap would lose track of the first one.
        if self._worker is not None:
            previous = self._pending or (False, False)
            self._pending = (force_refresh or previous[0], True)
            return
        self._started = True
        self._running = True
        self._error = ""
        self._progress = ("Planning", 0, 0)
        worker = _PlannerWorker(
            self._config(), force_refresh, self._controller_factory, self)
        worker.ready.connect(self._on_ready)
        worker.failed.connect(self._on_failed)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        if not self._routes:
            self._rebuild_list()
        self.state_changed.emit()
        worker.start()

    def _on_progress(self, text: str, done: int, total: int) -> None:
        self._progress = (text, int(done), int(total))
        self.state_changed.emit()

    def _on_ready(self, result: PlannerResult) -> None:
        self._running = False
        self._result = result
        self._routes = list(result.routes)
        self._error = result.error
        self._rebuild_list()
        self.state_changed.emit()

    def _on_failed(self, message: str) -> None:
        self._running = False
        self._error = message
        self._routes = []
        self._rebuild_list()
        self.state_changed.emit()

    def _on_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        pending = self._pending
        self._pending = None
        if pending is not None:
            force, _dirty = pending
            self._start(force)

    def _set_sort(self, key: str) -> None:
        if key == self._sort:
            return
        self._sort = key
        button = self._sort_buttons.get(key)
        if button is not None:
            button.setChecked(True)
        # Re-run the optimizer so the result is the top 500 by the selected
        # metric, not merely a re-order of the previous top 500.
        self._restart()
        self.state_changed.emit()

    # ------------------------------------------------------------------
    @staticmethod
    def _clear(layout: QVBoxLayout) -> None:
        while layout.count() > 1:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _rebuild_list(self) -> None:
        if not hasattr(self, "list_layout"):
            return
        self._clear(self.list_layout)
        if not self._routes:
            if self._running:
                text = "Optimizing routes…"
            elif self._error:
                text = ("Planner unavailable. Existing caches are empty; "
                        "press REFRESH to retry.")
            elif self.auto_button.isChecked() or self.lowg_button.isChecked() \
                    or self._capital_value() > 0:
                text = ("No routes match these constraints. Loosen the "
                        "filters or raise the capital cap.")
            else:
                text = ("No planner data yet. Press REFRESH to load the "
                        "market route cache.")
            self.list_layout.insertWidget(0, self._empty_label(text))
            return
        for route in self._routes:
            self.list_layout.insertWidget(
                self.list_layout.count() - 1, _PlannerRow(route))

    # ------------------------------------------------------------------
    def footer_text(self) -> str:
        if self._running:
            text, done, total = self._progress
            step = f"{text.upper()} {done}/{total}" if total else text.upper()
            return f"PLANNER  ·  {step}  ·  UEX API 2.0"
        error = self._error or (self._result.error if self._result else "")
        if error and not self._routes:
            return f"PLANNER  ·  OFFLINE  ·  {error[:70]}"
        if self._result is None:
            return "PLANNER  ·  WAITING  ·  UEX API 2.0"
        updated = self._result.updated_text
        state = "LIVE" if self._result.fresh and not error else "CACHED"
        suffix = "  ·  OFFLINE" if error else ""
        return (f"PLANNER  ·  {len(self._routes)} SHOWN  ·  "
                f"{self._result.total_rows:,} ROUTES  ·  {state} {updated}"
                f"{suffix}")

    def status_text(self) -> str:
        if self._running:
            return "PLANNING"
        if self._result is None:
            return "IDLE"
        if not self._routes and self._error:
            return "OFFLINE"
        if self._result.fresh and not self._error:
            return "LIVE"
        return "CACHED"

    def status_colour(self) -> str:
        if self._running:
            return theme.AMBER.name()
        if self._result is None:
            return "rgba(130,148,166,0.9)"
        if not self._routes and self._error:
            return theme.BAD.name()
        if self._result.fresh and not self._error:
            return theme.GOOD.name()
        return theme.AMBER.name()

    # ------------------------------------------------------------------
    def shutdown(self, timeout_ms: int = 30000) -> None:
        self._capital_timer.stop()
        self._pending = None
        self._started = False
        worker = self._worker
        if worker is None:
            return
        try:
            worker.requestInterruption()
            if worker.isRunning():
                worker.wait(timeout_ms)
            worker.deleteLater()
        except RuntimeError:
            pass
        self._worker = None
        self._running = False
