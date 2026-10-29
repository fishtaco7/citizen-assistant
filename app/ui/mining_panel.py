"""Compact mining scanner: one headline verdict, nothing else."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPen)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from ..mining import Match
from . import theme

PANEL_WIDTH = 330
PANEL_HEIGHT = 158


class _Verdict(QWidget):
    """The mineral reading: name, count, signature and confidence."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.px(PANEL_HEIGHT - 50))
        self._value: int | None = None
        self._confidence = 0.0
        self._state = "idle"
        self._match: Match | None = None
        self._note = ""

    def set_scan(self, value: int | None, confidence: float, state: str,
                 match: Match | None, note: str = "") -> None:
        self._value = value
        self._confidence = max(0.0, min(1.0, confidence))
        self._state = state
        self._match = match
        self._note = note
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        lost = self._state == "lost"

        # SIGNATURE 7,200 ................ 82% MATCH
        p.setFont(theme.pfont(7, QFont.Weight.Bold, spacing=1.4))
        p.setPen(theme.DIM)
        p.drawText(QRectF(0, 0, 84, 15), Qt.AlignmentFlag.AlignLeft, "SIGNATURE")
        if self._value is not None:
            p.setFont(theme.pfont(9, QFont.Weight.DemiBold, spacing=0.5))
            p.setPen(theme.MUTED if lost else theme.TEXT)
            p.drawText(QRectF(82, 0, 120, 15), Qt.AlignmentFlag.AlignLeft,
                       f"{self._value:,}")

            p.setFont(theme.pfont(7, QFont.Weight.Medium, spacing=0.7))
            p.setPen(theme.DIM if lost else
                     (theme.GOOD if self._confidence >= 0.75 else theme.AMBER))
            p.drawText(QRectF(0, 0, w, 15),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{int(self._confidence * 100)}% MATCH")

        # Headline
        name, color, count = self._headline(lost)
        p.setFont(theme.phud_font(26))
        metrics = QFontMetrics(p.font())
        avail = w - (48 if count else 0)
        p.setPen(color)
        p.drawText(QRectF(0, 20, avail, 38),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   metrics.elidedText(name, Qt.TextElideMode.ElideRight, avail))
        if count:
            p.setFont(theme.phud_font(15))
            p.setPen(theme.DIM if lost else theme.TEXT)
            p.drawText(QRectF(0, 20, w, 38),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       count)

        # Detail
        p.setFont(theme.pfont(8, QFont.Weight.Medium, spacing=0.2))
        p.setPen(theme.DIM if lost else theme.MUTED)
        p.drawText(QRectF(0, 62, w, 15),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       self._detail(), Qt.TextElideMode.ElideRight, w))

        # Confidence meter
        bar_w = 88.0
        y = h - 10
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 16))
        p.drawRoundedRect(QRectF(0, y, bar_w, 3), 1.5, 1.5)
        if self._value is not None and not lost:
            p.setBrush(theme.GOOD if self._confidence >= 0.75 else theme.AMBER)
            p.drawRoundedRect(
                QRectF(0, y, max(3.0, bar_w * self._confidence), 3), 1.5, 1.5)

        if self._note:
            p.setFont(theme.pfont(7, QFont.Weight.Medium))
            p.setPen(theme.DIM)
            p.drawText(QRectF(bar_w + 8, y - 7, w - bar_w - 8, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(
                           self._note, Qt.TextElideMode.ElideRight,
                           int(w - bar_w - 8)))

    # ------------------------------------------------------------------
    def _headline(self, lost: bool) -> tuple[str, QColor, str]:
        if self._match is not None:
            name = self._match.label if self._match.is_ground else self._match.name
            color = theme.DIM if lost else theme.rarity_color(self._match.rarity)
            return name.upper(), color, f"×{self._match.count}"
        if self._value is not None:
            return "UNKNOWN", theme.DIM if lost else theme.AMBER, ""
        return "— — —", QColor(72, 86, 102), ""

    def _detail(self) -> str:
        if self._match is not None:
            if self._match.kind == "debris":
                return self._match.secondary or "HULL PANELS / SCRAP METAL"
            if self._match.is_ground:
                return "SIZE ONLY · MINERAL DEPENDS ON LOCATION"
            bits = [theme.RARITY_NAME.get(self._match.rarity, "")]
            if self._match.secondary:
                bits.append(self._match.secondary)
            if not self._match.exact:
                bits.append(f"off by {self._match.delta}")
            return "  ·  ".join(b for b in bits if b)
        if self._note:
            return self._note.upper()
        if self._value is not None:
            return "NO KNOWN MINERAL AT THIS SIGNATURE"
        return "WAITING FOR A SCAN"


class MiningPanel(QWidget):
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(theme.px(PANEL_WIDTH), theme.px(PANEL_HEIGHT))

        self._drag_from = None
        self._scan_sig: tuple | None = None
        self._state = ""
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(16), theme.px(12), theme.px(16), theme.px(12))
        root.setSpacing(theme.px(0))

        head = QHBoxLayout()
        head.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        head.setSpacing(theme.px(7))

        self._dot = QWidget()
        self._dot.setFixedSize(theme.px(7), theme.px(7))

        title = QLabel("MINING SCANNER")
        title.setFont(theme.font(8, QFont.Weight.Bold, spacing=1.6))
        title.setStyleSheet(theme.qss("color: rgba(168,184,200,0.95); background: transparent;"))

        self._state_label = QLabel("IDLE")
        self._state_label.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.0))
        self._state_label.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.9); background: rgba(255,255,255,0.04);"
            "border-radius: 5px; padding: 2px 5px;"))

        head.addWidget(self._dot)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._state_label)
        head.addSpacing(theme.px(2))

        close = QPushButton("✕")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setFixedSize(theme.px(18), theme.px(18))
        close.setStyleSheet(theme.qss("""
            QPushButton { color: rgba(150,164,180,0.8); background: transparent;
                          border: none; font-size: 11px; }
            QPushButton:hover { color: #ffd9d9; }
        """))
        close.clicked.connect(self.close_requested.emit)
        head.addWidget(close)
        root.addLayout(head)
        root.addSpacing(theme.px(6))

        self.verdict = _Verdict()
        root.addWidget(self.verdict)
        root.addStretch(1)

    # ------------------------------------------------------------------
    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        colors = {
            "idle": "rgba(130,148,166,0.9)",
            "reading": theme.AMBER.name(),
            "stable": theme.GOOD.name(),
            "paused": "rgba(130,148,166,0.9)",
            "error": theme.BAD.name(),
        }
        labels = {"idle": "IDLE", "reading": "READING", "stable": "LOCKED",
                  "paused": "PAUSED", "error": "ERROR"}
        self._state_label.setText(labels.get(state, state.upper()))
        self._state_label.setStyleSheet(theme.qss(
            f"color: {colors.get(state, '#8294a6')};"
            "background: rgba(255,255,255,0.04); border-radius: 5px; padding: 2px 5px;"))
        self._dot.setStyleSheet(theme.qss(
            f"background: {colors.get(state, '#8294a6')}; border-radius: 3px;"))

    def set_scan(self, value: int | None, confidence: float, state: str,
                 matches: list[Match], note: str = "") -> None:
        top = matches[0] if matches else None
        key = (value, round(float(confidence), 2), state,
               (top.kind, top.name, top.count, top.delta) if top else None, note)
        if key == self._scan_sig:
            return
        self._scan_sig = key
        self.verdict.set_scan(value, confidence, state, top, note)

    def set_scan_idle(self, note: str = "") -> None:
        self.set_scan(None, 0.0, "idle", [], note)

    def reset_cache(self) -> None:
        """Force the next update to repaint even if the value is identical."""
        self._scan_sig = None

    # ------------------------------------------------------------------
    def place_default(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(geo.right() - self.width() - 40, geo.top() + 110)

    def clamp_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.geometry().center()) \
            or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(self.x(), geo.left() - 20), geo.right() - self.width() + 20)
        y = min(max(self.y(), geo.top()), geo.bottom() - 60)
        self.move(x, y)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 40:
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

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        theme.paint_card(p, QRectF(0, 0, w, h), radius=12)
        p.setPen(QPen(QColor(79, 209, 224, 90), 1.0))
        p.drawLine(16, 1, w - 16, 1)
