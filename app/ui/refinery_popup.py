"""A small always-on-top card announcing a finished refinery order.

The tracker in :mod:`app.refinery` owns the timing; this window only renders the
job it reports and stays until the player closes it.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from . import theme

WIDTH = 360
GREEN = QColor(94, 214, 138)
MUTED = QColor(130, 148, 166)


def _clean(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in {"", "unknown", "none", "null"} else text


class RefineryPopup(QWidget):
    """One completed work order, closeable with the ✕ or Escape."""

    closed = Signal()
    _open = 0

    def __init__(self, job, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(theme.px(WIDTH))

        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(16), theme.px(12),
                                theme.px(12), theme.px(12))
        root.setSpacing(theme.px(3))

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(theme.px(6))

        title = QLabel("REFINERY ORDER COMPLETE")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.4))
        title.setStyleSheet(theme.qss(
            f"color: rgba({GREEN.red()},{GREEN.green()},{GREEN.blue()},1.0);"
            "background: transparent;"))
        head.addWidget(title, 1)

        close = QPushButton("✕")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setFixedSize(theme.px(20), theme.px(20))
        close.setToolTip("Close")
        close.setStyleSheet(theme.qss("""
            QPushButton { color: rgba(150,164,180,0.8); background: transparent;
                          border: none; font-size: 12px; }
            QPushButton:hover { color: #ffd9d9; }
        """))
        close.clicked.connect(self.close)
        head.addWidget(close)
        root.addLayout(head)

        station = QLabel(_clean(getattr(job, "station", "")) or "Refinery")
        station.setFont(theme.font(10, QFont.Weight.DemiBold))
        station.setWordWrap(True)
        station.setStyleSheet(theme.qss(
            "color: #e8f0f8; background: transparent;"))
        root.addWidget(station)

        bits: list[str] = []
        system = _clean(getattr(job, "system", ""))
        method = _clean(getattr(job, "processing_method", ""))
        commodity = _clean(getattr(job, "commodity", ""))
        try:
            quantity = float(getattr(job, "quantity_scu", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        if system:
            bits.append(system)
        if method:
            bits.append(method)
        if commodity:
            bits.append(f"{quantity:g} SCU {commodity}" if quantity
                        else commodity)

        detail = QLabel("  ·  ".join(bits) or "Work order finished")
        detail.setFont(theme.font(7))
        detail.setWordWrap(True)
        detail.setStyleSheet(theme.qss(
            f"color: rgba({MUTED.red()},{MUTED.green()},{MUTED.blue()},0.95);"
            "background: transparent;"))
        root.addWidget(detail)

        ready = QLabel("Ready to collect at the refinery terminal")
        ready.setFont(theme.font(7, QFont.Weight.Medium))
        ready.setStyleSheet(theme.qss(
            "color: rgba(200,212,226,0.9); background: transparent;"))
        root.addWidget(ready)

        self.adjustSize()
        self._place()

    # ------------------------------------------------------------------
    def _place(self) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        slot = RefineryPopup._open % 4
        RefineryPopup._open += 1
        x = geo.right() - self.width() - theme.px(24)
        y = (geo.top() + theme.px(96)
             + slot * (self.height() + theme.px(8)))
        self.move(max(geo.left() + theme.px(8), x),
                  max(geo.top() + theme.px(8), y))

    def closeEvent(self, event):  # noqa: N802
        RefineryPopup._open = max(0, RefineryPopup._open - 1)
        self.closed.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):  # noqa: N802
        painter, width, height = theme.paint(self)
        theme.paint_card(painter, QRectF(0, 0, width, height), radius=12,
                         fill=QColor(10, 14, 20, 248))
        painter.setPen(QPen(QColor(GREEN.red(), GREEN.green(),
                                   GREEN.blue(), 120), 1.0))
        painter.drawLine(14, 1, width - 14, 1)
