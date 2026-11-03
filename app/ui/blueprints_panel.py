"""BLUEPRINTS: placeholder until the acquisition data is sourced.

The unlock tracking, the popup and its mission/set progress all live elsewhere
and keep working; this tab only ever described *where* a blueprint comes from,
which needs a dataset SCMDB exposes only through its live search.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme

WIDTH = 380
HEIGHT = 168

ACCENT = QColor(79, 209, 224)


class BlueprintsPanel(QWidget):
    close_requested = Signal()

    def __init__(self, tracker=None, sets=None, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(theme.px(WIDTH), theme.px(HEIGHT))

        self._drag_from = None
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(18), theme.px(12), theme.px(18), theme.px(16))
        root.setSpacing(theme.px(8))

        head = QHBoxLayout()
        head.setSpacing(theme.px(8))
        title = QLabel("BLUEPRINTS")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        title.setStyleSheet(theme.qss(
            "color: rgba(168,184,200,0.95); background: transparent;"))

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

        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(close)
        root.addLayout(head)

        root.addStretch(1)

        coming = QLabel("COMING SOON")
        coming.setAlignment(Qt.AlignmentFlag.AlignCenter)
        coming.setFont(theme.font(12, QFont.Weight.Bold, spacing=2.0))
        coming.setStyleSheet(theme.qss(
            f"color: rgba({ACCENT.red()},{ACCENT.green()},{ACCENT.blue()},0.95);"
            "background: transparent;"))
        root.addWidget(coming)

        note = QLabel("Blueprint lookups and where each one drops are being "
                      "worked on. Unlocks are still tracked, and the popup "
                      "shows set progress.")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setWordWrap(True)
        note.setFont(theme.font(8))
        note.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.95); background: transparent;"))
        root.addWidget(note)

        root.addStretch(1)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Kept for the history importer, which refreshes the panel."""

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

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        theme.paint_card(p, QRectF(0, 0, w, h), radius=14,
                         fill=QColor(10, 14, 20, 250))
        p.setPen(QPen(QColor(79, 209, 224, 90), 1.0))
        p.drawLine(16, 1, w - 16, 1)
