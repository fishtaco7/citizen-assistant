"""BLUEPRINTS: search what you own, where it drops and what it costs to craft."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import theme
from ..blueprint_crafting import CraftingData
from ..blueprint_sets import BlueprintSets
from ..blueprints import BlueprintTracker

WIDTH = 520
HEIGHT = 620
MAX_ROWS = 250

GREEN = QColor(94, 214, 138)
AMBER = QColor(255, 176, 32)


def _key(value: object) -> str:
    return str(value or "").strip().casefold()


class _Row(QWidget):
    def __init__(self, name: str, where: str, craft: str, owned: bool,
                 parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(theme.qss(
            "background: rgba(255,255,255,0.03); border-radius: 7px;"))
        self.setFixedHeight(theme.px(64))
        self.setToolTip(f"{name}\n{where}\n{craft}")

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.px(10), theme.px(5), theme.px(10), theme.px(5))
        row.setSpacing(theme.px(8))

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(theme.px(0))

        title = QLabel(name)
        title.setFont(theme.font(9, QFont.Weight.DemiBold))
        title.setStyleSheet(theme.qss(
            "color: #e8f0f8; background: transparent;"))
        text.addWidget(title)

        where_label = QLabel(where)
        where_label.setFont(theme.font(7))
        where_label.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.95); background: transparent;"))
        text.addWidget(where_label)

        craft_label = QLabel(craft)
        craft_label.setFont(theme.font(7))
        craft_label.setStyleSheet(theme.qss(
            f"color: rgba({AMBER.red()},{AMBER.green()},{AMBER.blue()},0.85);"
            "background: transparent;"))
        text.addWidget(craft_label)
        row.addLayout(text, 1)

        badge = QLabel("HAVE" if owned else "—")
        badge.setFont(theme.font(7, QFont.Weight.Bold, spacing=0.8))
        color = GREEN if owned else QColor(88, 103, 120)
        badge.setStyleSheet(theme.qss(
            f"color: rgba({color.red()},{color.green()},{color.blue()},0.95);"
            "background: transparent;"))
        row.addWidget(badge)

        for label in (title, where_label, craft_label):
            label.setMinimumWidth(0)


class BlueprintsPanel(QWidget):
    close_requested = Signal()

    def __init__(self, tracker: BlueprintTracker, sets: BlueprintSets,
                 crafting: CraftingData, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(theme.px(WIDTH), theme.px(HEIGHT))

        self._tracker = tracker
        self._sets = sets
        self._crafting = crafting
        self._query = ""
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
        title = QLabel("BLUEPRINTS")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        title.setStyleSheet(theme.qss(
            "color: rgba(168,184,200,0.95); background: transparent;"))

        self.status = QLabel("—")
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

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search blueprints, missions or materials…")
        self.search.setMaxLength(80)
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

    # ------------------------------------------------------------------
    def _on_query(self, text: str) -> None:
        self._query = text
        self._rebuild()

    def _clear_rows(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def refresh(self) -> None:
        """Re-read the registry and generated data after an import."""
        self._rebuild()

    def _rebuild(self) -> None:
        self._clear_rows()
        # Generated files may appear after launch; pick them up once.
        if not self._sets.loaded:
            self._sets.load()
        if not self._crafting.loaded:
            self._crafting.load()

        records = {_key(record.item_name): record
                   for record in self._tracker.blueprints}
        unlocked = [record.item_name for record in records.values()]

        names: dict[str, str] = {}
        for info in self._sets.catalogue():
            for blueprint in info.blueprints:
                names.setdefault(_key(blueprint), blueprint.strip())
        for blueprint in self._crafting.names():
            names.setdefault(_key(blueprint), blueprint.strip())
        for key, record in records.items():
            names.setdefault(key, record.item_name.strip())

        needle = self._query.strip().casefold()
        shown = 0
        for key in sorted(names, key=str.casefold):
            name = names[key]
            where = self._where(name, unlocked, records.get(key))
            craft = self._crafting.summary(name) or "No recipe data"
            if needle and needle not in f"{name} {where} {craft}".casefold():
                continue
            if shown >= MAX_ROWS:
                break
            self.list_layout.insertWidget(
                self.list_layout.count() - 1,
                _Row(name, where, craft, key in records))
            shown += 1

        total = len(names)
        self.status.setText(f"{len(records)} / {total} KNOWN")
        self.footer.setText(
            f"{len(records)} of {total} unlocked · {shown} shown")

    def _where(self, name: str, unlocked: list[str], record=None) -> str:
        set_name = self._sets.set_for(name)
        if set_name:
            have, total = self._sets.progress(set_name, unlocked)
            parts = [f"{have} of {total} · {set_name}" if total else set_name]
            requires = self._sets.requires_for(set_name)
            if requires:
                parts.append(requires)
            return "  ·  ".join(parts)
        if record is not None and record.first_seen_iso:
            return f"Unlocked {record.first_seen_iso[:10]}  ·  source unknown"
        return "Source not mapped"

    # ------------------------------------------------------------------
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._rebuild()

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
            self.move(geo.center().x() - self.width() // 2, geo.top() + 90)

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
