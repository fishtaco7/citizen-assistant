"""UEX: search the player marketplace for items on sale."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QThread, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QPalette, QPen)
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from . import theme
from ..marketplace import Ad, MarketplaceData, open_ad

WIDTH = 470
HEIGHT = 560
MAX_ROWS = 200

GREEN = QColor(94, 214, 138)
BLUE = QColor(79, 209, 224)
AMBER = QColor(255, 176, 32)


class _Fetch(QThread):
    """Network work kept off the UI thread."""

    done = Signal()
    failed = Signal(str)

    def __init__(self, data: MarketplaceData, parent=None):
        super().__init__(parent)
        self._data = data

    def run(self) -> None:  # noqa: D102
        try:
            self._data.fetch()
            self.done.emit()
        except Exception as exc:      # surface it in the panel, never crash
            self.failed.emit(str(exc))


class _AdRow(QWidget):
    def __init__(self, ad: Ad, on_open, parent=None):
        super().__init__(parent)
        self.ad = ad
        self._on_open = on_open
        self._hover = False

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(theme.px(46))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{ad.name}\n{ad.side} · {ad.price:,.0f} aUEC"
                        + (f"\n{ad.seller}" if ad.seller else "")
                        + "\nDouble-click to open UEX")
        self._paint_background()

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.px(10), theme.px(4), theme.px(10), theme.px(4))
        row.setSpacing(theme.px(8))

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(theme.px(1))

        name = QLabel(ad.name)
        name.setFont(theme.font(9, QFont.Weight.DemiBold))
        name.setStyleSheet(theme.qss("color: #e8f0f8; background: transparent;"))
        text.addWidget(name)

        bits = []
        if ad.quantity:
            bits.append(f"×{ad.quantity:,}")
        if ad.seller:
            bits.append(ad.seller)
        if ad.age_text:
            bits.append(ad.age_text)
        meta = QLabel("  ·  ".join(bits) or " ")
        meta.setFont(theme.font(7))
        meta.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.95); background: transparent;"))
        text.addWidget(meta)

        row.addLayout(text, 1)

        price = QLabel(f"{ad.price:,.0f}" if ad.price else "—")
        price.setFont(theme.font(10, QFont.Weight.Bold))
        price.setAlignment(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter)
        color = GREEN if ad.side == "SELL" else BLUE
        price.setStyleSheet(theme.qss(
            f"color: rgba({color.red()},{color.green()},{color.blue()},0.98);"
            "background: transparent;"))
        row.addWidget(price)

    def _paint_background(self) -> None:
        fill = ("rgba(79,209,224,0.10)" if self._hover
                else "rgba(255,255,255,0.03)")
        self.setStyleSheet(theme.qss(
            f"background: {fill}; border-radius: 7px;"))

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self._paint_background()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self._paint_background()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_open(self.ad)
            event.accept()


class UexPanel(QWidget):
    close_requested = Signal()

    def __init__(self, data: MarketplaceData | None = None, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(theme.px(WIDTH), theme.px(HEIGHT))

        self._data = data or MarketplaceData()
        self._side = "SELL"
        self._query = ""
        self._worker: _Fetch | None = None
        self._rows: list[_AdRow] = []
        self._drag_from = None

        self._build()
        self._rebuild()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(16), theme.px(12), theme.px(16), theme.px(10))
        root.setSpacing(theme.px(6))

        head = QHBoxLayout()
        head.setSpacing(theme.px(8))
        title = QLabel("UEX")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        title.setStyleSheet(theme.qss(
            "color: rgba(168,184,200,0.95); background: transparent;"))

        self.status = QLabel("IDLE")
        self.status.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.0))
        self.status.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.9); background: rgba(255,255,255,0.04);"
            "border-radius: 5px; padding: 2px 6px;"))

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.refresh_btn.setFixedSize(theme.px(22), theme.px(22))
        self.refresh_btn.setToolTip("Re-sync the marketplace board")
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

        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.status)
        head.addWidget(self.refresh_btn)
        head.addWidget(close)
        root.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search items…")
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
        self.search.textChanged.connect(self._on_query)
        root.addWidget(self.search)

        chips = QHBoxLayout()
        chips.setSpacing(theme.px(5))
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        for key in ("SELL", "BUY"):
            chip = QPushButton(key)
            chip.setCheckable(True)
            chip.setChecked(key == self._side)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.setFixedHeight(theme.px(22))
            chip.setStyleSheet(theme.qss(self._chip_qss()))
            chip.clicked.connect(lambda _=False, k=key: self._set_side(k))
            self._chip_group.addButton(chip)
            chips.addWidget(chip)
        chips.addStretch(1)
        self.total = QLabel("")
        self.total.setFont(theme.font(7))
        self.total.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.9); background: transparent;"))
        chips.addWidget(self.total)
        root.addLayout(chips)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(theme.qss(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 6px; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,0.18);"
            "border-radius: 3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            "{ height: 0; }"))

        self.host = QWidget()
        self.host.setStyleSheet(theme.qss("background: transparent;"))
        self.list_layout = QVBoxLayout(self.host)
        self.list_layout.setContentsMargins(theme.px(2), theme.px(2),
                                            theme.px(2), theme.px(2))
        self.list_layout.setSpacing(theme.px(3))
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.host)
        root.addWidget(self.scroll, 1)

        self.footer = QLabel("")
        self.footer.setFont(theme.font(7, QFont.Weight.Medium, spacing=0.8))
        self.footer.setStyleSheet(theme.qss(
            "color: rgba(102,118,134,0.95); background: transparent;"))
        root.addWidget(self.footer)

    def _chip_qss(self) -> str:
        return """
        QPushButton {
            color: rgba(150,166,182,0.95); background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 6px;
            padding: 1px 10px; font-size: 8px; font-weight: 600;
        }
        QPushButton:hover { background: rgba(255,255,255,0.08); }
        QPushButton:checked {
            color: #e6f7fa; background: rgba(79,209,224,0.18);
            border-color: rgba(79,209,224,0.55);
        }
        """

    # ------------------------------------------------------------------
    def _set_side(self, side: str) -> None:
        self._side = side
        self._rebuild()

    def _on_query(self, text: str) -> None:
        self._query = text
        self._rebuild()

    def _set_status(self, text: str, color: QColor) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(theme.qss(
            f"color: rgba({color.red()},{color.green()},{color.blue()},0.95);"
            "background: rgba(255,255,255,0.04);"
            "border-radius: 5px; padding: 2px 6px;"))

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._set_status("LOADING", AMBER)
        self._worker = _Fetch(self._data, self)
        self._worker.done.connect(self._on_loaded)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_loaded(self) -> None:
        self._set_status("LIVE", GREEN)
        self._rebuild()

    def _on_failed(self, message: str) -> None:
        # Keep whatever the cache had: a stale board beats an empty window.
        self._set_status("OFFLINE", AMBER)
        self.error = message
        self._rebuild()

    def _clear_rows(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []

    def _rebuild(self) -> None:
        self._clear_rows()
        ads = self._data.search(self._query, self._side)
        shown = ads[:MAX_ROWS]

        if not shown:
            empty = QLabel(self._empty_text())
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setFont(theme.font(9))
            empty.setStyleSheet(theme.qss(
                "color: rgba(130,148,166,0.9); padding: 24px 8px;"
                "background: transparent;"))
            self.list_layout.insertWidget(0, empty)
        else:
            for ad in shown:
                row = _AdRow(ad, self._open_ad)
                self.list_layout.insertWidget(self.list_layout.count() - 1, row)
                self._rows.append(row)

        sell, buy = self._data.counts()
        self.total.setText(f"{sell} SELL · {buy} BUY")
        self.footer.setText(
            f"{len(ads)} ADS · NEWEST FIRST · UEX MARKETPLACE"
            + (f" · SHOWING {len(shown)}" if len(ads) > len(shown) else ""))

    def _empty_text(self) -> str:
        if not self._data.ads:
            return ("No marketplace data yet.\n\nPress ↻ to load the board.")
        if self._query.strip():
            return f"No {self._side.lower()} ads match “{self._query.strip()}”."
        return f"No live {self._side.lower()} ads right now."

    def _open_ad(self, ad: Ad) -> None:
        open_ad(ad)

    # ------------------------------------------------------------------
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if not self._data.ads:
            self._data.load_cached()
            self._rebuild()
        if not self._data.fresh:
            self.refresh()

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
    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        theme.paint_card(p, QRectF(0, 0, w, h), radius=14,
                         fill=QColor(10, 14, 20, 250))
        p.setPen(QPen(QColor(79, 209, 224, 90), 1.0))
        p.drawLine(16, 1, w - 16, 1)
