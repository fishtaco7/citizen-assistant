"""BLUEPRINTS: search, then inspect a blueprint's source and crafting."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette, QPen
from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import theme
from .. import blueprint_calc
from ..blueprint_crafting import CraftingData
from ..blueprint_sets import BlueprintSets
from ..blueprints import BlueprintTracker

WIDTH = 540
HEIGHT = 640
MAX_ROWS = 250

GREEN = QColor(94, 214, 138)
AMBER = QColor(255, 176, 32)
MUTED = QColor(130, 148, 166)


def _key(value: object) -> str:
    return str(value or "").strip().casefold()


def _scroll_area() -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setStyleSheet(theme.qss(
        "QScrollArea { background: transparent; border: none; }"
        "QScrollBar:vertical { background: transparent; width: 6px; }"
        "QScrollBar::handle:vertical { background: rgba(255,255,255,0.18);"
        "border-radius: 3px; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
        "{ height: 0; }"))
    return area


class _Row(QWidget):
    def __init__(self, name: str, where: str, craft: str, owned: bool,
                 on_click, parent=None):
        super().__init__(parent)
        self._on_click = on_click
        self._name = name
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(theme.qss(
            "background: rgba(255,255,255,0.03); border-radius: 7px;"))
        self.setFixedHeight(theme.px(64))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{name}\n{where}\n{craft}\nClick for details")

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.px(10), theme.px(5), theme.px(10), theme.px(5))
        row.setSpacing(theme.px(8))

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(theme.px(0))

        title = QLabel(name)
        title.setFont(theme.font(9, QFont.Weight.DemiBold))
        title.setStyleSheet(theme.qss("color: #e8f0f8; background: transparent;"))
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

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click(self._name)
            event.accept()


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
        self._detail_name = ""
        self._detail_materials: list[dict] = []
        self._detail_spins: list[QSpinBox] = []
        self._detail_values: list[QLabel] = []
        self._tiers: list[dict] = []
        self._tier_index = 0
        self._tier_buttons: list[QPushButton] = []

        self._build()
        self._rebuild()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(16), theme.px(12), theme.px(16), theme.px(10))
        root.setSpacing(theme.px(6))

        head = QHBoxLayout()
        head.setSpacing(theme.px(8))
        self.title = QLabel("BLUEPRINTS")
        self.title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        self.title.setStyleSheet(theme.qss(
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

        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.status)
        head.addWidget(close)
        root.addLayout(head)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_list_page())
        self.stack.addWidget(self._build_detail_page())
        root.addWidget(self.stack, 1)

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.px(6))

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
        layout.addWidget(self.search)

        self.scroll = _scroll_area()
        self.host = QWidget()
        self.host.setStyleSheet(theme.qss("background: transparent;"))
        self.list_layout = QVBoxLayout(self.host)
        self.list_layout.setContentsMargins(theme.px(2), theme.px(2),
                                            theme.px(2), theme.px(2))
        self.list_layout.setSpacing(theme.px(3))
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.host)
        layout.addWidget(self.scroll, 1)

        self.footer = QLabel("")
        self.footer.setFont(theme.font(7, QFont.Weight.Medium, spacing=0.8))
        self.footer.setStyleSheet(theme.qss(
            "color: rgba(102,118,134,0.95); background: transparent;"))
        layout.addWidget(self.footer)
        return page

    def _build_detail_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.px(4))

        back = QPushButton("‹  BACK")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        back.setFixedHeight(theme.px(22))
        back.setStyleSheet(theme.qss("""
            QPushButton { color: rgba(168,184,200,0.9); background: transparent;
                          border: none; text-align: left; font-size: 8px;
                          font-weight: 600; }
            QPushButton:hover { color: #e6f7fa; }
        """))
        back.clicked.connect(self._show_list)
        layout.addWidget(back)

        self.detail_title = QLabel("")
        self.detail_title.setFont(theme.font(10, QFont.Weight.Bold))
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet(theme.qss(
            "color: #e8f0f8; background: transparent;"))
        layout.addWidget(self.detail_title)

        self.detail_where = QLabel("")
        self.detail_where.setFont(theme.font(7))
        self.detail_where.setWordWrap(True)
        self.detail_where.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.95); background: transparent;"))
        layout.addWidget(self.detail_where)

        self.tier_bar = QWidget()
        self.tier_bar.setStyleSheet(theme.qss("background: transparent;"))
        self.tier_layout = QHBoxLayout(self.tier_bar)
        self.tier_layout.setContentsMargins(0, theme.px(1), 0, theme.px(1))
        self.tier_layout.setSpacing(theme.px(4))
        self.tier_bar.hide()
        layout.addWidget(self.tier_bar)

        self.material_scroll = _scroll_area()
        self.material_host = QWidget()
        self.material_host.setStyleSheet(theme.qss("background: transparent;"))
        self.material_layout = QVBoxLayout(self.material_host)
        self.material_layout.setContentsMargins(theme.px(2), theme.px(2),
                                                theme.px(2), theme.px(2))
        self.material_layout.setSpacing(theme.px(3))
        self.material_layout.addStretch(1)
        self.material_scroll.setWidget(self.material_host)
        layout.addWidget(self.material_scroll, 1)

        self.result_label = QLabel("")
        self.result_label.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.2))
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.95); background: transparent;"))

        # STAT / BASE / CRAFTED, the way the crafting sites lay it out. One row
        # per property, so a weapon with five stats reads as a list.
        self.table_host = QWidget()
        self.table_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,
                                     True)
        self.table_host.setStyleSheet(theme.qss(
            "background: rgba(255,255,255,0.03); border-radius: 8px;"))
        table = QVBoxLayout(self.table_host)
        table.setContentsMargins(theme.px(12), theme.px(8),
                                 theme.px(12), theme.px(8))
        table.setSpacing(theme.px(2))
        table.addWidget(self.result_label)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(0)
        for text, width, align in (
                ("STAT", 0, Qt.AlignmentFlag.AlignLeft),
                ("BASE", 52, Qt.AlignmentFlag.AlignRight),
                ("CRAFTED", 78, Qt.AlignmentFlag.AlignRight)):
            label = QLabel(text)
            label.setFont(theme.font(6, QFont.Weight.Bold, spacing=1.2))
            label.setStyleSheet(theme.qss(
                "color: rgba(110,126,142,0.95); background: transparent;"))
            label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            if width:
                label.setFixedWidth(theme.px(width))
            header.addWidget(label, 0 if width else 1)
        table.addLayout(header)

        rule = QFrame()
        rule.setFixedHeight(theme.px(1))
        rule.setStyleSheet(theme.qss("background: rgba(255,255,255,0.08);"))
        table.addWidget(rule)

        self.stats_host = QWidget()
        self.stats_host.setStyleSheet(theme.qss("background: transparent;"))
        self.stats_layout = QVBoxLayout(self.stats_host)
        self.stats_layout.setContentsMargins(0, theme.px(3), 0, 0)
        self.stats_layout.setSpacing(theme.px(2))
        table.addWidget(self.stats_host)
        layout.addWidget(self.table_host)
        return page

    def _clear_result_rows(self) -> None:
        while self.stats_layout.count():
            item = self.stats_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _result_row(self, name: str, value: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        label = QLabel(name)
        label.setFont(theme.font(8))
        label.setStyleSheet(theme.qss(
            "color: rgba(200,212,226,0.95); background: transparent;"))
        layout.addWidget(label, 1)

        base = QLabel("—")
        base.setFont(theme.font(8))
        base.setFixedWidth(theme.px(52))
        base.setAlignment(Qt.AlignmentFlag.AlignRight
                          | Qt.AlignmentFlag.AlignVCenter)
        base.setStyleSheet(theme.qss(
            "color: rgba(96,110,126,0.95); background: transparent;"))
        layout.addWidget(base)

        amount = QLabel(value)
        amount.setFont(theme.font(8, QFont.Weight.DemiBold))
        amount.setFixedWidth(theme.px(78))
        amount.setAlignment(Qt.AlignmentFlag.AlignRight
                            | Qt.AlignmentFlag.AlignVCenter)
        amount.setStyleSheet(theme.qss(
            f"color: rgba({GREEN.red()},{GREEN.green()},{GREEN.blue()},1.0);"
            "background: transparent;"))
        layout.addWidget(amount)
        return row

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
                _Row(name, where, craft, key in records, self._show_detail))
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
    def _show_list(self) -> None:
        self.stack.setCurrentIndex(0)
        self.title.setText("BLUEPRINTS")

    def _show_detail(self, name: str) -> None:
        self._detail_name = name
        self._detail_materials = []
        self._detail_spins = []
        self._detail_values = []
        self._clear_result_rows()
        self._clear_parts()

        self.title.setText("BLUEPRINT")
        self.detail_title.setText(name)

        records = {_key(record.item_name): record
                   for record in self._tracker.blueprints}
        unlocked = [record.item_name for record in records.values()]
        self.detail_where.setText(self._where(name, unlocked,
                                             records.get(_key(name))))

        entry = self._crafting.get(name)
        self._tiers = (entry or {}).get("tiers") or []
        self._tier_index = 0
        self._build_tier_bar()

        if not self._tiers or not any(
                tier.get("materials") for tier in self._tiers):
            self._clear_result_rows()
            self.table_host.hide()
            self.result_label.setText("No recipe data for this blueprint.")
            self.stack.setCurrentIndex(1)
            return

        self.stack.setCurrentIndex(1)
        self._select_tier(0)

    def _clear_parts(self) -> None:
        while self.material_layout.count() > 1:
            item = self.material_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_tier_bar(self) -> None:
        while self.tier_layout.count():
            item = self.tier_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._tier_buttons = []
        self.tier_bar.setVisible(len(self._tiers) > 1)
        if len(self._tiers) <= 1:
            return

        for index in range(len(self._tiers)):
            button = QPushButton(f"TIER {index + 1}")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedHeight(theme.px(20))
            button.setStyleSheet(theme.qss("""
                QPushButton {
                    color: rgba(168,184,200,0.9); background: transparent;
                    border: 1px solid rgba(255,255,255,0.14);
                    border-radius: 5px; padding: 1px 10px;
                    font-size: 8px; font-weight: 600;
                }
                QPushButton:hover { color: #e6f7fa; }
                QPushButton:checked {
                    color: #061017; background: rgba(79,209,224,0.85);
                    border-color: rgba(79,209,224,0.9);
                }
            """))
            button.clicked.connect(
                lambda _checked=False, target=index: self._select_tier(target))
            self.tier_layout.addWidget(button)
            self._tier_buttons.append(button)
        self.tier_layout.addStretch(1)

    def _select_tier(self, index: int) -> None:
        if not (0 <= index < len(self._tiers)):
            return
        self._tier_index = index
        for position, button in enumerate(self._tier_buttons):
            button.setChecked(position == index)

        tier = self._tiers[index]
        # Copies, so switching materials does not rewrite the loaded data.
        materials = [dict(material)
                     for material in (tier.get("materials") or [])]
        self._detail_materials = materials
        self._detail_spins = []
        self._detail_values = []
        self._clear_parts()
        self._clear_result_rows()
        if not materials:
            self.table_host.hide()
            self.result_label.setText("No recipe data for this tier.")
            return
        for material in materials:
            self.material_layout.insertWidget(
                self.material_layout.count() - 1,
                self._material_row(material))

        self.table_host.show()
        self._recalculate()

    def _material_row(self, material: dict) -> QWidget:
        card = QWidget()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(theme.qss(
            "background: rgba(255,255,255,0.03); border-radius: 8px;"))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(theme.px(12), theme.px(8),
                                  theme.px(12), theme.px(8))
        layout.setSpacing(theme.px(3))

        quantity = float(material.get("quantity") or 0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(theme.px(4))
        slot = QLabel(str(material.get("slot") or "PART").upper())
        slot.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.2))
        slot.setStyleSheet(theme.qss(
            "color: rgba(168,184,200,0.95); background: transparent;"))
        head.addWidget(slot, 1)
        layout.addLayout(head)

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(theme.px(6))

        options = [option for option in (material.get("options") or [])
                   if isinstance(option, dict)]
        if not options:
            options = [{"name": material.get("name"),
                        "quantity": material.get("quantity"),
                        "minQuality": material.get("minQuality")}]

        picker = None
        if len(options) > 1:
            picker = QComboBox()
            picker.setFont(theme.font(9, QFont.Weight.DemiBold))
            picker.setFixedHeight(theme.px(21))
            picker.setCursor(Qt.CursorShape.PointingHandCursor)
            picker.setStyleSheet(theme.qss("""
                QComboBox {
                    color: #e8f0f8; background: rgba(255,255,255,0.05);
                    border: 1px solid rgba(255,255,255,0.14);
                    border-radius: 4px; padding: 0px 6px;
                }
                QComboBox::drop-down { border: none; width: 14px; }
                QComboBox QAbstractItemView {
                    color: #e8f0f8; background: #0b1118;
                    border: 1px solid rgba(255,255,255,0.12);
                    selection-background-color: rgba(79,209,224,0.25);
                }
            """))
            for option in options:
                picker.addItem(str(option.get("name") or "—"))
            line.addWidget(picker, 1)
        else:
            name = QLabel(str(material.get("name") or ""))
            name.setFont(theme.font(9, QFont.Weight.DemiBold))
            name.setStyleSheet(theme.qss(
                "color: #e8f0f8; background: transparent;"))
            line.addWidget(name, 1)

        amount = QLabel(f"{quantity:g} SCU")
        amount.setFont(theme.font(7))
        amount.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.95); background: transparent;"))
        line.addWidget(amount)
        layout.addLayout(line)

        quality = QHBoxLayout()
        quality.setContentsMargins(0, 0, 0, 0)
        quality.setSpacing(theme.px(8))
        caption = QLabel("QUALITY")
        caption.setFont(theme.font(7, QFont.Weight.Bold, spacing=1.0))
        caption.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.95); background: transparent;"))
        caption.setFixedWidth(theme.px(50))
        quality.addWidget(caption)

        floor = int(material.get("minQuality") or 0)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(floor, 1000)
        slider.setSingleStep(1)
        slider.setPageStep(25)
        slider.setValue(1000)
        slider.setFixedHeight(theme.px(16))
        slider.setStyleSheet(theme.qss("""
            QSlider { background: transparent; }
            QSlider::groove:horizontal {
                height: 2px; background: rgba(255,255,255,0.16);
                border-radius: 1px;
            }
            QSlider::sub-page:horizontal {
                background: rgba(79,209,224,0.55); border-radius: 1px;
            }
            QSlider::handle:horizontal {
                background: #e8f4ff; width: 10px; margin: -5px 0;
                border-radius: 5px; border: none;
            }
        """))
        quality.addWidget(slider, 1)

        spin = QSpinBox()
        spin.setRange(floor, 1000)
        spin.setSingleStep(5)
        spin.setValue(1000)
        spin.setFixedWidth(theme.px(56))
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setFont(theme.font(8))
        spin.setStyleSheet(theme.qss("""
            QSpinBox { color: #e8f0f8; background: rgba(255,255,255,0.05);
                       border: 1px solid rgba(255,255,255,0.14);
                       border-radius: 5px; padding: 1px 3px; }
        """))
        quality.addWidget(spin)
        layout.addLayout(quality)

        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        spin.valueChanged.connect(self._recalculate)
        self._detail_spins.append(spin)

        if picker is not None:
            def apply(index: int) -> None:
                option = options[index]
                material["name"] = str(option.get("name") or "")
                material["quantity"] = float(option.get("quantity") or 0)
                floor_value = int(option.get("minQuality") or 0)
                material["minQuality"] = floor_value
                amount.setText(f"{material['quantity']:g} SCU")
                slider.setRange(floor_value, 1000)
                spin.setRange(floor_value, 1000)
                if spin.value() < floor_value:
                    spin.setValue(floor_value)
                self._recalculate()

            picker.currentIndexChanged.connect(apply)

        effect = QLabel("")
        effect.setFont(theme.font(7, QFont.Weight.DemiBold))
        effect.setWordWrap(True)
        effect.setStyleSheet(theme.qss(
            f"color: rgba({GREEN.red()},{GREEN.green()},{GREEN.blue()},0.95);"
            "background: rgba(94,214,138,0.08);"
            "border: 1px solid rgba(94,214,138,0.22);"
            "border-radius: 4px; padding: 2px 6px;"))
        effect.hide()
        layout.addWidget(effect)
        self._detail_values.append(effect)
        return card

    @staticmethod
    def _format(stats: dict) -> str:
        parts = []
        for entry in sorted(stats.values(), key=lambda item: item["name"]):
            if entry.get("percent") is None:
                parts.append(f"{entry['name']} {entry.get('delta', 0):+.0f}")
            else:
                parts.append(f"{entry['name']} {entry['percent']:+.2f}%")
        return "  ·  ".join(parts)

    def _recalculate(self) -> None:
        if not self._detail_materials:
            return
        qualities = [spin.value() for spin in self._detail_spins]

        # Per-part effect, so each slot explains itself the way the crafting
        # sites show it.
        for index, label in enumerate(self._detail_values):
            if index >= len(self._detail_materials):
                break
            single = blueprint_calc.changes([self._detail_materials[index]],
                                            [qualities[index]])
            text = self._format(single)
            label.setText(text)
            label.setToolTip(text)
            label.setVisible(bool(text))

        stats = blueprint_calc.changes(self._detail_materials, qualities)
        quality = blueprint_calc.average_quality(qualities,
                                                 self._detail_materials)
        self._clear_result_rows()
        self.result_label.setText(f"CRAFT QUALITY  {quality:,.0f}")
        if not stats:
            self.result_label.setText(
                f"CRAFT QUALITY  {quality:,.0f}  ·  no stat modifiers")
            return
        for entry in sorted(stats.values(), key=lambda item: item["name"]):
            if entry.get("percent") is None:
                value = f"{entry.get('delta', 0.0):+.0f}"
            else:
                value = f"{entry['percent']:+.2f}%"
            self.stats_layout.addWidget(
                self._result_row(entry["name"], value))

    # ------------------------------------------------------------------
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self.stack.currentIndex() == 0:
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
