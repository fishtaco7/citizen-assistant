"""Live combat feed rendered from the headless Game.log tracker."""
from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPen)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ..combat import CombatEvent, CombatTracker
from . import theme

WIDTH = 500
HEIGHT = 600
ROW_HEIGHT = 64
MAX_ROWS = 200
MAX_EVENTS = 260

GREEN = QColor(94, 214, 138)
AMBER = QColor(255, 176, 32)
RED = QColor(232, 106, 106)
BLUE = QColor(79, 209, 224)


def _clock(timestamp: float) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(timestamp)))
    except (TypeError, ValueError, OSError):
        return "--:--:--"


def _status_text(event: CombatEvent) -> str:
    if event.kind == "player_kill":
        return "KILL"
    if event.status == "Soft-Dead":
        return "SOFT-DEAD"
    if event.status == "Destroyed":
        return "DESTROYED"
    if event.kind == "weapon_impact":
        return "HIT"
    if event.kind == "vehicle_damage":
        return "DAMAGE"
    return (event.status or "EVENT").upper()


def _status_color(event: CombatEvent) -> QColor:
    if event.status == "Soft-Dead":
        return AMBER
    if event.status == "Destroyed" or event.kind == "player_kill":
        return RED
    if event.kind in {"weapon_impact", "vehicle_damage"}:
        return BLUE
    return theme.MUTED


def _chip(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setCheckable(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setFixedHeight(theme.px(24))
    button.setStyleSheet(theme.qss("""
        QPushButton {
            color: rgba(130,148,166,0.9); background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.06); border-radius: 5px;
            font-size: 7px; font-weight: 600;
            letter-spacing: 0.8px; padding: 0 8px;
        }
        QPushButton:hover { color: #d6e2ee; border-color: rgba(79,209,224,0.35); }
        QPushButton:checked {
            color: #4fd1e0; background: rgba(79,209,224,0.13);
            border-color: rgba(79,209,224,0.5);
        }
    """))
    return button


class _Stat(QWidget):
    def __init__(self, label: str, color: QColor, parent=None):
        super().__init__(parent)
        self.label = label
        self.color = color
        self.value = 0
        self.setFixedHeight(theme.px(44))
        self.setMinimumWidth(theme.px(74))

    def set_value(self, value: int) -> None:
        value = int(value or 0)
        if value != self.value:
            self.value = value
            self.update()

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 7))
        p.drawRoundedRect(QRectF(0, 0, w, h), 7, 7)

        p.setFont(theme.pfont(13, QFont.Weight.Bold))
        p.setPen(self.color)
        p.drawText(QRectF(8, 2, w - 16, 23),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{self.value:,}")

        p.setFont(theme.pfont(6, QFont.Weight.Bold, spacing=1.0))
        p.setPen(theme.MUTED)
        p.drawText(QRectF(8, 24, w - 16, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.label)


class _CombatRow(QWidget):
    def __init__(self, event: CombatEvent, parent=None):
        super().__init__(parent)
        self.payload = event
        self.setFixedHeight(theme.px(ROW_HEIGHT))
        self.setToolTip(self._tooltip(event))

    @staticmethod
    def _tooltip(event: CombatEvent) -> str:
        lines = [
            f"{event.target_name or 'Unknown target'} — {_status_text(event)}",
            f"Attacker: {event.attacker or 'unknown'}",
            f"Weapon: {event.weapon or event.weapon_class or 'unknown'}",
            f"Kind: {event.kind}",
        ]
        if event.damage_type:
            lines.append(f"Damage: {event.damage_type}")
        if event.zone:
            lines.append(f"Zone: {event.zone}")
        if event.raw:
            lines.append("")
            lines.append(event.raw[:600])
        return "\n".join(lines)

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ev = self.payload
        color = _status_color(ev)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 210))
        p.drawRoundedRect(QRectF(0, 8, 2, h - 16), 1, 1)

        p.setFont(theme.pfont(7, QFont.Weight.Medium, spacing=0.4))
        p.setPen(theme.DIM)
        p.drawText(QRectF(12, 4, 80, 13),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   _clock(ev.timestamp))

        label = _status_text(ev)
        p.setFont(theme.pfont(7, QFont.Weight.Bold, spacing=0.8))
        badge_w = QFontMetrics(p.font()).horizontalAdvance(label) + 12
        badge_x = w - 12 - badge_w
        p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 150), 1))
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 28))
        p.drawRoundedRect(QRectF(badge_x, 4, badge_w, 14), 7, 7)
        p.setPen(color)
        p.drawText(QRectF(badge_x, 4, badge_w, 14),
                   Qt.AlignmentFlag.AlignCenter, label)

        p.setFont(theme.pfont(10, QFont.Weight.DemiBold))
        p.setPen(theme.TEXT)
        target = ev.target_name or "Unknown target"
        target = QFontMetrics(p.font()).elidedText(
            target, Qt.TextElideMode.ElideRight, w - 24)
        p.drawText(QRectF(12, 20, w - 24, 17),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   target)

        bits = []
        if ev.attacker:
            bits.append(f"by {ev.attacker}")
        else:
            bits.append("attacker unknown")
        if ev.weapon:
            bits.append(ev.weapon)
        elif ev.weapon_class:
            bits.append(ev.weapon_class)
        if ev.damage_type:
            bits.append(ev.damage_type)
        sub = "  ·  ".join(bits)
        p.setFont(theme.pfont(8))
        p.setPen(theme.MUTED)
        sub = QFontMetrics(p.font()).elidedText(
            sub, Qt.TextElideMode.ElideRight, w - 24)
        p.drawText(QRectF(12, 39, w - 24, 15),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   sub)

        theme.divider(p, 12, w - 12, h - 1)


class CombatPanel(QWidget):
    close_requested = Signal()

    def __init__(self, tracker: CombatTracker | None = None, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(theme.px(WIDTH), theme.px(HEIGHT))

        self._tracker = tracker
        self._events: deque[CombatEvent] = deque(maxlen=MAX_EVENTS)
        self._empty: QLabel | None = None
        self._drag_from = None
        self._status_key: tuple[str, str, str] | None = None
        self._footer_text = ""

        self._build()
        self._rebuild_feed()

        # Only poll the tracker while this window is actually on screen. The
        # feed still buffers events in memory while hidden, so opening the tab
        # shows the full recent history without doing widget work in the
        # background.
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(900)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._refresh_stats()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(16), theme.px(12), theme.px(16), theme.px(10))
        root.setSpacing(theme.px(6))

        head = QHBoxLayout()
        head.setSpacing(theme.px(8))
        title = QLabel("COMBAT")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        title.setStyleSheet(theme.qss(
            "color: rgba(168,184,200,0.95); background: transparent;"))

        self.status = QLabel("WAITING")
        self.status.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.0))

        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.status)

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
        head.addWidget(close)
        root.addLayout(head)

        intro = QLabel(
            "Read-only Game.log feed. Shows the victim, attacker, weapon and "
            "whether the target was soft-dead or destroyed.")
        intro.setWordWrap(True)
        intro.setFont(theme.font(8))
        intro.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.92); background: transparent;"))
        root.addWidget(intro)

        stats = QHBoxLayout()
        stats.setSpacing(theme.px(6))
        self.kills_stat = _Stat("KILLS", GREEN)
        self.deaths_stat = _Stat("DEATHS", RED)
        self.soft_dead_stat = _Stat("DOWNED", AMBER)
        self.destroyed_stat = _Stat("DESTROYED", RED)
        self.kills_stat.setToolTip("Actor-kill lines seen in Game.log")
        self.deaths_stat.setToolTip(
            "Non-NPC victims seen by the Game.log heuristic")
        self.soft_dead_stat.setToolTip("Ships in the Soft-Dead state")
        self.destroyed_stat.setToolTip("Ships fully destroyed")
        for tile in (self.kills_stat, self.deaths_stat,
                     self.soft_dead_stat, self.destroyed_stat):
            stats.addWidget(tile, 1)
        root.addLayout(stats)

        tools = QHBoxLayout()
        tools.setSpacing(theme.px(5))
        self.damage_btn = _chip("DAMAGE")
        self.damage_btn.setToolTip(
            "Include damage and weapon-impact lines as well")
        self.damage_btn.toggled.connect(lambda _=False: self._rebuild_feed())
        tools.addWidget(self.damage_btn)
        tools.addStretch(1)

        self.clear_btn = QPushButton("CLEAR")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_btn.setFixedHeight(theme.px(24))
        self.clear_btn.setToolTip("Clear this feed; session stats keep running")
        self.clear_btn.setStyleSheet(theme.qss(self._chip_qss()))
        self.clear_btn.clicked.connect(self._clear_feed)
        tools.addWidget(self.clear_btn)
        root.addLayout(tools)

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

        self.footer = QLabel("")
        self.footer.setFont(theme.font(6, QFont.Weight.Medium, spacing=0.4))
        self.footer.setStyleSheet(theme.qss(
            "color: rgba(112,128,144,0.95); background: transparent;"))
        root.addWidget(self.footer)

    @staticmethod
    def _chip_qss() -> str:
        return """
            QPushButton {
                color: rgba(130,148,166,0.9); background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.06); border-radius: 5px;
                font-size: 7px; font-weight: 600;
                letter-spacing: 0.8px; padding: 0 8px;
            }
            QPushButton:hover { color: #d6e2ee; border-color: rgba(79,209,224,0.35); }
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

    # ------------------------------------------------------------------
    def _include(self, event: CombatEvent) -> bool:
        if event.event_type in {"session_started", "session_reset"}:
            return False
        if self.damage_btn.isChecked():
            return True
        return (event.status in {"Soft-Dead", "Destroyed"}
                or event.kind == "player_kill")

    def add_event(self, event: CombatEvent) -> None:
        """Append one tracker payload; called on the GUI thread by a signal."""
        if event.event_type == "session_reset":
            self._events.clear()
            if self.isVisible():
                self._rebuild_feed()
            return
        if event.event_type == "session_started":
            return

        self._events.append(event)
        if not self.isVisible() or not self._include(event):
            return

        self._remove_empty()
        self.list_layout.insertWidget(0, _CombatRow(event))
        while self.list_layout.count() > MAX_ROWS + 1:
            item = self.list_layout.takeAt(MAX_ROWS)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._update_footer()

    def set_stats(self, stats) -> None:
        self.kills_stat.set_value(getattr(stats, "kills", 0))
        self.deaths_stat.set_value(getattr(stats, "deaths", 0))
        self.soft_dead_stat.set_value(
            getattr(stats, "soft_dead_targets", 0))
        self.destroyed_stat.set_value(
            getattr(stats, "destroyed_targets", 0))

    def _refresh_stats(self) -> None:
        if self._tracker is None:
            self._update_status("PREVIEW", theme.MUTED, "")
            self._update_footer()
            return
        try:
            stats = self._tracker.stats
        except Exception as exc:
            self._update_status("ERROR", RED, str(exc))
            return
        self.set_stats(stats)
        path = self._tracker.game_log_path
        if self._tracker.error:
            self._update_status("ERROR", RED, self._tracker.error)
        elif path:
            self._update_status("LIVE", GREEN, str(path))
        else:
            self._update_status("WAITING", AMBER, "Looking for Game.log…")
        self._update_footer()

    def _update_status(self, text: str, color: QColor, tooltip: str) -> None:
        key = (text, color.name(), tooltip)
        if key == self._status_key:
            return
        self._status_key = key
        self.status.setText(text)
        self.status.setToolTip(tooltip)
        self.status.setStyleSheet(theme.qss(
            f"color: {color.name()}; background: rgba(255,255,255,0.04);"
            "border-radius: 5px; padding: 2px 6px;"))

    def _clear_feed(self) -> None:
        self._events.clear()
        self._rebuild_feed()

    # ------------------------------------------------------------------
    def _clear_rows(self) -> None:
        self._empty = None
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _remove_empty(self) -> None:
        if self._empty is None:
            return
        self._empty.hide()
        self._empty.setParent(None)
        self._empty.deleteLater()
        self._empty = None

    @staticmethod
    def _empty_label() -> QLabel:
        label = QLabel(
            "No combat events yet.\n\n"
            "Start the game and this tab will read kills, soft-deaths and "
            "ship destructions from Game.log. Nothing is injected or written.")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(theme.font(9))
        label.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.9); padding: 26px 8px;"
            "background: transparent;"))
        return label

    def _rebuild_feed(self) -> None:
        if not hasattr(self, "list_layout"):
            return
        self._clear_rows()
        events = [event for event in self._events if self._include(event)]
        if not events:
            self._empty = self._empty_label()
            self.list_layout.insertWidget(0, self._empty)
        else:
            for event in reversed(events[-MAX_ROWS:]):
                self.list_layout.insertWidget(
                    self.list_layout.count() - 1, _CombatRow(event))
        self._update_footer()

    def _update_footer(self) -> None:
        shown = sum(
            1 for i in range(self.list_layout.count())
            if isinstance(self.list_layout.itemAt(i).widget(), _CombatRow))
        noun = "EVENT" if shown == 1 else "EVENTS"
        bits = [f"{shown} {noun}", "NEWEST FIRST", "READ-ONLY GAME.LOG"]
        if self.damage_btn.isChecked():
            bits.insert(1, "DAMAGE ON")
        text = "  ·  ".join(bits)
        if text != self._footer_text:
            self._footer_text = text
            self.footer.setText(text)

    # ------------------------------------------------------------------
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        # Rebuild from the buffered deque so events that arrived while the tab
        # was hidden appear immediately, then resume live stats polling.
        self._rebuild_feed()
        self._refresh_stats()
        self._stats_timer.start()

    def hideEvent(self, event):  # noqa: N802
        self._stats_timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    def place_default(self, anchor: QWidget | None = None) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        if anchor is not None and anchor.isVisible():
            x = anchor.x()
            y = anchor.y() + anchor.height() + 12
            if y + self.height() > geo.bottom() - 8:
                y = max(geo.top() + 8, geo.bottom() - self.height() - 8)
            if x + self.width() > geo.right() - 8:
                x = max(geo.left() + 8, geo.right() - self.width() - 8)
            self.move(max(geo.left() + 8, x), y)
        else:
            self.move(geo.center().x() - self.width() // 2, geo.top() + 110)

    def clamp_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.geometry().center()) \
            or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(self.x(), geo.left() - 20), geo.right() - self.width() + 20)
        y = min(max(self.y(), geo.top()), geo.bottom() - 60)
        self.move(x, y)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if (event.button() == Qt.MouseButton.LeftButton
                and event.position().y() < 34):
            self._drag_from = (event.globalPosition().toPoint()
                               - self.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        if (self._drag_from is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._drag_from is not None:
            self._drag_from = None
            self.clamp_to_screen()

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        theme.paint_card(p, QRectF(0, 0, w, h), radius=14,
                         fill=QColor(10, 14, 20, 250))
        p.setPen(QPen(QColor(79, 209, 224, 90), 1.0))
        p.drawLine(16, 1, w - 16, 1)
