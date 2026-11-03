"""The popup shown when a new blueprint unlocks.

Deliberately small and quiet: it sits in a corner, never takes focus, and goes
away on its own after a minute. Clicking it dismisses it immediately.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import theme

WIDTH = 420
HEIGHT = 132
SHOW_MS = 60_000
MARGIN = 28

ACCENT = QColor(79, 209, 224)
AMBER = QColor(255, 176, 32)


class BlueprintToast(QWidget):
    """One card, reused: showing a new blueprint replaces the previous one."""

    def __init__(self, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(theme.px(WIDTH), theme.px(HEIGHT))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SHOW_MS)
        self._timer.timeout.connect(self.hide)

        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(20), theme.px(16), theme.px(20), theme.px(16))
        root.setSpacing(theme.px(2))

        caption = QLabel("NEW BLUEPRINT")
        caption.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.6))
        caption.setStyleSheet(theme.qss(
            f"color: rgba({ACCENT.red()},{ACCENT.green()},{ACCENT.blue()},0.95);"
            "background: transparent;"))
        root.addWidget(caption)

        self.name = QLabel("")
        self.name.setFont(theme.font(11, QFont.Weight.DemiBold))
        self.name.setWordWrap(True)
        self.name.setStyleSheet(theme.qss(
            "color: #e8f0f8; background: transparent;"))
        root.addWidget(self.name, 1)

        row = QHBoxLayout()
        row.setSpacing(theme.px(6))
        self.progress = QLabel("")
        self.progress.setFont(theme.font(8, QFont.Weight.Medium, spacing=0.4))
        self.progress.setStyleSheet(theme.qss(
            f"color: rgba({AMBER.red()},{AMBER.green()},{AMBER.blue()},0.95);"
            "background: transparent;"))
        row.addWidget(self.progress, 1)
        self.hint = QLabel("click to dismiss")
        self.hint.setFont(theme.font(7))
        self.hint.setStyleSheet(theme.qss(
            "color: rgba(102,118,134,0.9); background: transparent;"))
        row.addWidget(self.hint)
        root.addLayout(row)

    # ------------------------------------------------------------------
    def show_blueprint(self, name: str, progress: str = "") -> None:
        self.name.setText(name.strip() or "Unknown blueprint")
        self.progress.setText(progress.strip())
        self.progress.setVisible(bool(progress.strip()))
        self._place()
        self.show()
        self.raise_()
        self._timer.start()

    def _place(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.right() - self.width() - theme.px(MARGIN)
        y = geo.bottom() - self.height() - theme.px(MARGIN)
        self.move(max(geo.left() + theme.px(8), x),
                  max(geo.top() + theme.px(8), y))

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.hide()
            event.accept()

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        theme.paint_card(p, QRectF(0, 0, w, h), radius=12,
                         fill=QColor(10, 14, 20, 246))
        p.setPen(QPen(QColor(AMBER.red(), AMBER.green(), AMBER.blue(), 120), 1.2))
        p.drawRoundedRect(QRectF(0.6, 0.6, w - 1.2, h - 1.2), 12, 12)
