"""UEX: open the player marketplace in the browser, already searched.

The marketplace board is rendered server-side behind Cloudflare, so there is no
public JSON feed for it: an in-app list could only ever show a partial page of
commodity ads. A search box that opens the real page is smaller, always current
and cannot break when UEX changes their backend.
"""
from __future__ import annotations

import os
import webbrowser

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from . import theme
from ..marketplace import search_url

WIDTH = 400
HEIGHT = 150

GREEN = QColor(94, 214, 138)
AMBER = QColor(255, 176, 32)


class UexPanel(QWidget):
    close_requested = Signal()

    def __init__(self, parent=None):
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
        root.setContentsMargins(theme.px(16), theme.px(12), theme.px(16), theme.px(10))
        root.setSpacing(theme.px(7))

        head = QHBoxLayout()
        head.setSpacing(theme.px(8))
        title = QLabel("UEX")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        title.setStyleSheet(theme.qss(
            "color: rgba(168,184,200,0.95); background: transparent;"))

        self.status = QLabel("READY")
        self.status.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.0))
        self.status.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.9); background: rgba(255,255,255,0.04);"
            "border-radius: 5px; padding: 2px 6px;"))

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
        head.addWidget(self.status)
        head.addWidget(close)
        root.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(theme.px(8))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the marketplace — p6, quantanium…")
        self.search.setMaxLength(60)
        self.search.setFixedHeight(theme.px(28))
        self.search.setFont(theme.font(9))
        self.search.setStyleSheet(theme.qss("""
            QLineEdit {
                color: #e8f0f8; background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10); border-radius: 7px;
                padding: 2px 9px; font-size: 9px;
                selection-background-color: rgba(79,209,224,0.35);
            }
            QLineEdit:focus {
                border-color: rgba(79,209,224,0.65);
                background: rgba(79,209,224,0.07);
            }
        """))
        palette = self.search.palette()
        palette.setColor(QPalette.ColorRole.Text, QColor(232, 240, 248))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 137, 155))
        self.search.setPalette(palette)
        self.search.returnPressed.connect(self.open_search)

        self.open_btn = QPushButton("OPEN")
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.open_btn.setFixedWidth(theme.px(74))
        self.open_btn.setFixedHeight(theme.px(28))
        self.open_btn.setToolTip("Open these results on uexcorp.space")
        self.open_btn.setStyleSheet(theme.qss("""
            QPushButton {
                color: #e6f7fa; background: rgba(79,209,224,0.18);
                border: 1px solid rgba(79,209,224,0.55); border-radius: 7px;
                font-size: 8px; font-weight: 700;
            }
            QPushButton:hover { background: rgba(79,209,224,0.30); }
        """))
        self.open_btn.clicked.connect(self.open_search)

        row.addWidget(self.search, 1)
        row.addWidget(self.open_btn)
        root.addLayout(row)

        self.footer = QLabel(
            "Opens the full board in your browser · weapons and commodities")
        self.footer.setFont(theme.font(7, QFont.Weight.Medium, spacing=0.6))
        self.footer.setStyleSheet(theme.qss(
            "color: rgba(102,118,134,0.95); background: transparent;"))
        root.addWidget(self.footer)

    # ------------------------------------------------------------------
    def open_search(self) -> None:
        term = self.search.text().strip()
        url = search_url(term)
        try:
            webbrowser.open(url)
        except OSError:
            starter = getattr(os, "startfile", None)     # Windows only
            if starter is None:
                self._set_status("NO BROWSER", AMBER)
                return
            starter(url)
        self._set_status("OPENED", GREEN)
        self.footer.setText(url)

    def _set_status(self, text: str, color: QColor) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(theme.qss(
            f"color: rgba({color.red()},{color.green()},{color.blue()},0.95);"
            "background: rgba(255,255,255,0.04);"
            "border-radius: 5px; padding: 2px 6px;"))

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

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        theme.paint_card(p, QRectF(0, 0, w, h), radius=14,
                         fill=QColor(10, 14, 20, 250))
        p.setPen(QPen(QColor(79, 209, 224, 90), 1.0))
        p.drawLine(16, 1, w - 16, 1)
