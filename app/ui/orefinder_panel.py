"""Ore finder: where to mine a given ore, live from scminer.rocks."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..orefinder import Hotspot, Ore, LiveData
from . import theme

WIDTH = 430
HEIGHT = 560
ROW_HEIGHT = 46

CAT_COLOR = {
    "Ship": QColor(79, 209, 224),
    "ROC": QColor(255, 176, 32),
    "FPS": QColor(199, 125, 255),
}


class _Fetch(QThread):
    """Network work kept off the UI thread."""

    done = Signal(str)
    failed = Signal(str, str)

    def __init__(self, data: LiveData, key: str):
        super().__init__()
        self._data = data
        self._key = key

    def run(self) -> None:
        try:
            if self._key == "index":
                self._data.fetch_index()
            else:
                self._data.fetch_detail(self._key)
            self.done.emit(self._key)
        except Exception as exc:  # surface to the panel, never crash the app
            self.failed.emit(self._key, str(exc))


class _OreRow(QWidget):
    def __init__(self, ore: Ore, on_click, parent=None):
        super().__init__(parent)
        self.ore = ore
        self._on_click = on_click
        self._hover = False
        self._active = False
        self.setFixedHeight(theme.px(ROW_HEIGHT))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{ore.category} deposit · {ore.hotspot_count} known hotspots")

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self.update()

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self._on_click(self.ore)

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ore = self.ore
        color = CAT_COLOR.get(ore.category, theme.MUTED)

        if self._hover or self._active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 20 if self._active else 10))
            p.drawRoundedRect(QRectF(0, 1, w, h - 2), 6, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color.red(), color.green(), color.blue(),
                          220 if self._active else 90))
        p.drawRoundedRect(QRectF(0, 8, 2, h - 16), 1, 1)

        # Name + best spot
        p.setFont(theme.pfont(10, QFont.Weight.DemiBold))
        p.setPen(theme.TEXT if (self._hover or self._active) else QColor(214, 226, 238))
        p.drawText(QRectF(12, 5, w - 150, 17), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter, ore.name.upper())

        p.setFont(theme.pfont(8))
        p.setPen(theme.DIM)
        top = ore.top_hotspot
        sub = f"{top.name} · {top.abundance:g}%" if top else "no hotspot data"
        p.drawText(QRectF(12, 22, w - 150, 16), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(sub, Qt.TextElideMode.ElideRight,
                                                     w - 160))

        # Price + signature
        price = f"{ore.price:,}" if ore.price else "MARKET"
        p.setFont(theme.pfont(9, QFont.Weight.DemiBold))
        p.setPen(theme.TEXT)
        p.drawText(QRectF(w - 130, 5, 118, 17),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, price)
        p.setFont(theme.pfont(7, QFont.Weight.Medium, spacing=0.6))
        p.setPen(theme.DIM)
        tail = (f"RS {ore.signature:,}" if ore.signature
                else ("VOLATILE" if ore.volatile else ""))
        p.drawText(QRectF(w - 130, 22, 118, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tail)

        theme.divider(p, 12, w - 12, h - 1)


class _SpotRow(QWidget):
    def __init__(self, spot: Hotspot, parent=None):
        super().__init__(parent)
        self.spot = spot
        self.setFixedHeight(theme.px(52 if spot.gear else 42))

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        spot = self.spot

        p.setFont(theme.pfont(7, QFont.Weight.Bold))
        p.setPen(theme.ACCENT)
        p.drawText(QRectF(0, 4, 22, 14), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter, f"{spot.rank:02d}")

        p.setFont(theme.pfont(9, QFont.Weight.DemiBold))
        p.setPen(theme.TEXT)
        p.drawText(QRectF(24, 3, w - 92, 16), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter, spot.name)

        p.setFont(theme.pfont(9, QFont.Weight.DemiBold))
        p.setPen(theme.ACCENT)
        p.drawText(QRectF(w - 66, 3, 66, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{spot.abundance:g}%")

        bits = [b for b in (spot.system, spot.kind, spot.hazard) if b]
        p.setFont(theme.pfont(7))
        p.setPen(theme.MUTED)
        p.drawText(QRectF(24, 19, w - 24, 14), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText("  ·  ".join(bits),
                                                     Qt.TextElideMode.ElideRight, w - 30))
        if spot.gear:
            p.setPen(QColor(214, 168, 74))
            p.drawText(QRectF(24, 33, w - 24, 14), Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(spot.gear,
                                                         Qt.TextElideMode.ElideRight,
                                                         w - 30))
        theme.divider(p, 0, w, h - 1)


class OreFinderPanel(QWidget):
    close_requested = Signal()

    def __init__(self, data: LiveData, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(theme.px(WIDTH), theme.px(HEIGHT))

        self._data = data
        self._filter = "ALL"
        self._current: Ore | None = None
        self._pending_focus: str = ""
        self._rows: list[_OreRow] = []
        self._workers: dict[str, _Fetch] = {}
        self._drag_from = None

        self._build()
        self._rebuild_list()
        self._update_footer()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(16), theme.px(12), theme.px(16), theme.px(10))
        root.setSpacing(theme.px(6))

        head = QHBoxLayout()
        head.setSpacing(theme.px(8))
        title = QLabel("ORE FINDER")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        title.setStyleSheet(theme.qss("color: rgba(168,184,200,0.95); background: transparent;"))

        self.status = QLabel("IDLE")
        self.status.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.0))
        self.status.setStyleSheet(theme.qss("color: rgba(130,148,166,0.9);"
                                  "background: rgba(255,255,255,0.04);"
                                  "border-radius: 5px; padding: 2px 6px;"))

        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.status)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.refresh_btn.setFixedSize(theme.px(22), theme.px(22))
        self.refresh_btn.setToolTip("Re-sync from scminer.rocks")
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

        # Filter chips --------------------------------------------------
        chips = QHBoxLayout()
        chips.setSpacing(theme.px(5))
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        for key in ("ALL", "SHIP", "ROC", "FPS"):
            chip = QPushButton(key)
            chip.setCheckable(True)
            chip.setChecked(key == "ALL")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.setFixedHeight(theme.px(22))
            chip.setStyleSheet(theme.qss(self._chip_qss()))
            chip.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            self._chip_group.addButton(chip)
            chips.addWidget(chip)
        chips.addStretch(1)
        root.addLayout(chips)

        # Pages ---------------------------------------------------------
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_list_page())
        self.stack.addWidget(self._build_detail_page())
        root.addWidget(self.stack, 1)

        self.footer = QLabel("")
        self.footer.setFont(theme.font(7, QFont.Weight.Medium, spacing=0.8))
        self.footer.setStyleSheet(theme.qss("color: rgba(102,118,134,0.95); background: transparent;"))
        root.addWidget(self.footer)

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(theme.qss("background: transparent;"))
        outer = QVBoxLayout(page)
        outer.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        outer.setSpacing(theme.px(2))

        caps = QWidget()
        caps.setStyleSheet(theme.qss("background: transparent;"))
        cap_row = QHBoxLayout(caps)
        cap_row.setContentsMargins(theme.px(12), theme.px(2), theme.px(16), theme.px(2))
        cap_row.setSpacing(theme.px(4))
        left = QLabel("ORE  ·  TOP HOTSPOT")
        right = QLabel("aUEC / SCU  ·  SIGNATURE")
        for label in (left, right):
            label.setFont(theme.font(7, QFont.Weight.Medium, spacing=1.0))
            label.setStyleSheet(theme.qss("color: rgba(88,103,120,0.95); background: transparent;"))
        cap_row.addWidget(left)
        cap_row.addStretch(1)
        cap_row.addWidget(right)
        outer.addWidget(caps)

        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_scroll.setStyleSheet(theme.qss(self._scroll_qss()))

        self.list_host = QWidget()
        self.list_host.setStyleSheet(theme.qss("background: transparent;"))
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(4), theme.px(0))
        self.list_layout.setSpacing(theme.px(0))
        self.list_layout.addStretch(1)
        self.list_scroll.setWidget(self.list_host)

        outer.addWidget(self.list_scroll, 1)
        return page

    def _build_detail_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(theme.qss("background: transparent;"))
        layout = QVBoxLayout(page)
        layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        layout.setSpacing(theme.px(4))

        back = QPushButton("‹  ALL ORES")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        back.setFixedHeight(theme.px(24))
        back.setStyleSheet(theme.qss("""
            QPushButton { color: rgba(130,148,166,0.95); background: transparent;
                          border: none; text-align: left;                           font-size: 8px; font-weight: 600; letter-spacing: 1.1px; }
            QPushButton:hover { color: #4fd1e0; }
        """))
        back.clicked.connect(self._show_list)
        layout.addWidget(back)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_scroll.setStyleSheet(theme.qss(self._scroll_qss()))

        self.detail_host = QWidget()
        self.detail_host.setStyleSheet(theme.qss("background: transparent;"))
        self.detail_layout = QVBoxLayout(self.detail_host)
        self.detail_layout.setContentsMargins(theme.px(0), theme.px(0), theme.px(6), theme.px(0))
        self.detail_layout.setSpacing(theme.px(2))
        self.detail_scroll.setWidget(self.detail_host)
        layout.addWidget(self.detail_scroll, 1)
        return page

    # ------------------------------------------------------------------
    @staticmethod
    def _chip_qss() -> str:
        return """
            QPushButton {
                color: rgba(130,148,166,0.9); background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.06); border-radius: 5px;
                font-size: 7px; font-weight: 600;
                letter-spacing: 1.0px; padding: 0 9px;
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
            QScrollBar:vertical {
                background: transparent; width: 7px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(79,209,224,0.22); border-radius: 3px; min-height: 26px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(79,209,224,0.42); }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
            QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
        """

    # ------------------------------------------------------------------
    def _set_filter(self, key: str) -> None:
        self._filter = key
        self.stack.setCurrentIndex(0)
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []

        wanted = [ore for ore in self._data.ores
                  if self._filter == "ALL" or ore.category.upper() == self._filter]
        if not self._data.loaded:
            self.list_layout.insertWidget(0, self._empty_label(
                "Syncing ore data from scminer.rocks…" if self._workers
                else "No ore data yet. Press ↻ to sync."))
        for ore in wanted:
            row = _OreRow(ore, self._select_ore)
            row.set_active(self._current is not None and ore.slug == self._current.slug)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self._rows.append(row)

    @staticmethod
    def _empty_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(theme.font(9))
        label.setStyleSheet(theme.qss("color: rgba(130,148,166,0.9); padding: 24px 8px;"
                            "background: transparent;"))
        return label

    # ------------------------------------------------------------------
    def _select_ore(self, ore: Ore) -> None:
        self._current = ore
        self._pending_focus = ""
        for row in self._rows:
            row.set_active(row.ore.slug == ore.slug)
        self.stack.setCurrentIndex(1)
        self.detail_scroll.verticalScrollBar().setValue(0)
        if self._data.needs_detail(ore):
            self._start_fetch(ore.slug)
        self._fill_detail(ore)

    def _show_list(self) -> None:
        self.stack.setCurrentIndex(0)

    def focus_ore(self, name: str) -> bool:
        ore = self._data.find(name)
        if ore is None:
            if not self._data.loaded:
                self._pending_focus = name
            self._show_list()
            return False
        self._select_ore(ore)
        return True

    # ------------------------------------------------------------------
    def _clear_detail(self) -> None:
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _fill_detail(self, ore: Ore) -> None:
        self._clear_detail()
        loading = self._data.needs_detail(ore) and ore.slug in self._workers

        name = QLabel(ore.name.upper())
        name.setFont(theme.hud_font(20))
        name.setStyleSheet(theme.qss(f"color: {CAT_COLOR.get(ore.category, theme.TEXT).name()};"
                           "background: transparent;"))
        self.detail_layout.addWidget(name)

        meta_bits = [ore.category or "DEPOSIT"]
        if ore.price:
            meta_bits.append(f"{ore.price:,} aUEC / SCU")
        if ore.signature:
            meta_bits.append(f"RS {ore.signature:,}")
        elif ore.volatile:
            meta_bits.append("VOLATILE")
        meta = QLabel("  ·  ".join(meta_bits))
        meta.setFont(theme.font(8, QFont.Weight.Medium, spacing=0.4))
        meta.setStyleSheet(theme.qss("color: rgba(150,166,182,0.95); background: transparent;"))
        self.detail_layout.addWidget(meta)
        self.detail_layout.addSpacing(theme.px(6))

        if loading:
            self.detail_layout.addWidget(self._empty_label("Loading locations…"))
        elif not ore.has_detail:
            self.detail_layout.addWidget(self._empty_label(
                "Could not load this ore.\nPress ↻ to retry."))

        if ore.has_detail:
            self.detail_layout.addWidget(self._section(
                "TOP LOCATIONS", f"{len(ore.hotspots)} HOTSPOTS"))
            for spot in ore.hotspots:
                self.detail_layout.addWidget(_SpotRow(spot))

            if ore.refineries:
                self.detail_layout.addSpacing(theme.px(8))
                self.detail_layout.addWidget(self._section(
                    "REFINERY BONUS", f"BEST +{ore.refineries[0].bonus}%"))
                for refinery in ore.refineries:
                    self.detail_layout.addWidget(self._refinery_row(refinery))

            self.detail_layout.addSpacing(theme.px(8))
            gear = QLabel("")
            gear.setWordWrap(True)
            lines = []
            if ore.laser:
                lines.append(f"Laser: {ore.laser}")
            if ore.gadget:
                lines.append(f"Gadget: {ore.gadget}")
            if ore.vessel:
                lines.append(f"Ship: {ore.vessel}")
            gear.setText("\n".join(lines))
            gear.setFont(theme.font(8))
            gear.setStyleSheet(theme.qss("color: rgba(130,148,166,0.95); background: transparent;"))
            self.detail_layout.addWidget(gear)

            for note in ore.notes[:2]:
                label = QLabel(note)
                label.setWordWrap(True)
                label.setFont(theme.font(7))
                label.setStyleSheet(theme.qss("color: rgba(214,168,74,0.9);"
                                    "background: transparent; padding-top: 4px;"))
                self.detail_layout.addWidget(label)

        self.detail_layout.addStretch(1)

    def _section(self, left: str, right: str = "") -> QWidget:
        holder = QWidget()
        holder.setStyleSheet(theme.qss("background: transparent;"))
        row = QHBoxLayout(holder)
        row.setContentsMargins(theme.px(0), theme.px(10), theme.px(0), theme.px(4))
        row.setSpacing(theme.px(6))

        label = QLabel(left)
        label.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.5))
        label.setStyleSheet(theme.qss("color: rgba(136,152,170,0.98); background: transparent;"))
        row.addWidget(label)
        row.addStretch(1)
        if right:
            tail = QLabel(right)
            tail.setFont(theme.font(7, QFont.Weight.Medium, spacing=0.8))
            tail.setStyleSheet(theme.qss("color: rgba(102,118,134,0.95); background: transparent;"))
            row.addWidget(tail)
        return holder

    @staticmethod
    def _refinery_row(refinery) -> QWidget:
        holder = QWidget()
        holder.setStyleSheet(theme.qss("background: transparent;"))
        row = QHBoxLayout(holder)
        row.setContentsMargins(theme.px(0), theme.px(1), theme.px(0), theme.px(1))
        row.setSpacing(theme.px(6))

        name = QLabel(refinery.name)
        name.setFont(theme.font(8))
        name.setStyleSheet(theme.qss("color: rgba(198,212,226,0.95); background: transparent;"))

        bonus = QLabel(f"{refinery.bonus:+d}%")
        bonus.setFont(theme.font(8, QFont.Weight.DemiBold))
        colour = theme.GOOD.name() if refinery.bonus > 0 else (
            theme.BAD.name() if refinery.bonus < 0 else theme.MUTED.name())
        bonus.setStyleSheet(theme.qss(f"color: {colour}; background: transparent;"))

        row.addWidget(name, 1)
        row.addWidget(bonus)
        return holder

    # ------------------------------------------------------------------
    def _set_status(self, text: str, colour: str = "rgba(130,148,166,0.9)") -> None:
        self.status.setText(text)
        self.status.setStyleSheet(theme.qss(f"color: {colour}; background: rgba(255,255,255,0.04);"
                                  "border-radius: 5px; padding: 2px 6px;"))

    def _update_footer(self) -> None:
        data = self._data
        parts = ["SCMINER.ROCKS"]
        if data.patch:
            parts.append(f"PATCH {data.patch}")
        if data.loaded:
            parts.append(data.synced_text())
        self.footer.setText("  ·  ".join(parts))

        if data.error:
            self._set_status("OFFLINE", theme.AMBER.name())
        elif self._workers:
            self._set_status("SYNCING", theme.AMBER.name())
        elif data.loaded:
            self._set_status("LIVE", theme.GOOD.name())
        else:
            self._set_status("IDLE")

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self._start_fetch("index", force=True)

    def refresh_if_stale(self) -> None:
        if self._data.needs_index() or not self._data.loaded:
            self._start_fetch("index")

    def _start_fetch(self, key: str, force: bool = False) -> None:
        if key in self._workers:
            return
        if key == "index" and not force and not self._data.needs_index():
            return
        worker = _Fetch(self._data, key)
        worker.done.connect(self._on_fetch_done)
        worker.failed.connect(self._on_fetch_failed)
        worker.finished.connect(lambda k=key: self._on_worker_finished(k))
        self._workers[key] = worker
        self._update_footer()
        if key == "index":
            self.refresh_btn.setEnabled(False)
            if not self._data.loaded:
                self._rebuild_list()
        worker.start()

    def _on_worker_finished(self, key: str) -> None:
        worker = self._workers.pop(key, None)
        if worker is not None:
            worker.deleteLater()
        self.refresh_btn.setEnabled(True)
        if self._current is not None and key in ("index", self._current.slug):
            self._fill_detail(self._current)
        self._update_footer()

    def _on_fetch_done(self, key: str) -> None:
        self._data.error = ""
        if key == "index":
            self._rebuild_list()
            if self._pending_focus:
                name, self._pending_focus = self._pending_focus, ""
                self.focus_ore(name)
        self._update_footer()

    def _on_fetch_failed(self, key: str, message: str) -> None:
        self._data.error = message
        self._update_footer()

    # ------------------------------------------------------------------
    def place_default(self, anchor: QWidget | None = None) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        if anchor is not None and anchor.isVisible():
            x = anchor.x() - self.width() - 12
            if x < geo.left() + 8:
                x = anchor.x()
            y = min(anchor.y(), geo.bottom() - self.height() - 8)
            self.move(x, y)
        else:
            self.move(geo.center().x() - self.width() // 2, geo.top() + 110)

    def clamp_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.geometry().center()) \
            or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(self.x(), geo.left() - 20), geo.right() - self.width() + 20)
        y = min(max(self.y(), geo.top()), geo.bottom() - 60)
        self.move(x, y)

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

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        theme.paint_card(p, QRectF(0, 0, w, h), radius=14,
                         fill=QColor(10, 14, 20, 250))
        p.setPen(QPen(QColor(79, 209, 224, 90), 1.0))
        p.drawLine(16, 1, w - 16, 1)
