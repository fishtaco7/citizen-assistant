"""Manual stash notes for loot and supplies left at other locations."""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPalette, QPen)
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QCompleter, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ..inventory import InventoryNote, InventoryStore
from ..stations import clean_terminal_name, station_names
from . import theme

WIDTH = 476
HEIGHT = 630
ROW_HEIGHT = 70

GREEN = QColor(94, 214, 138)
AMBER = QColor(255, 176, 32)
RED = QColor(232, 106, 106)


def _when(timestamp: float) -> str:
    if not timestamp:
        return ""
    then = time.localtime(timestamp)
    now = time.localtime()
    if (then.tm_year, then.tm_yday) == (now.tm_year, now.tm_yday):
        return time.strftime("%H:%M", then)
    if then.tm_year == now.tm_year:
        return time.strftime("%b %d", then)
    return time.strftime("%Y-%m-%d", then)


def _line_edit(placeholder: str, max_length: int) -> QLineEdit:
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setMaxLength(max_length)
    field.setFixedHeight(theme.px(28))
    field.setFont(theme.font(9))
    field.setStyleSheet(theme.qss("""
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
    palette = field.palette()
    palette.setColor(QPalette.ColorRole.Text, QColor(232, 240, 248))
    palette.setColor(QPalette.ColorRole.PlaceholderText,
                     QColor(120, 137, 155))
    field.setPalette(palette)
    return field


class _StationCombo(QComboBox):
    """Editable station picker with type-ahead completion."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(18)
        self.setFixedHeight(theme.px(28))
        self.setFont(theme.font(9))
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.addItems(station_names())
        self.setCurrentIndex(-1)
        self.clearEditText()
        self.setStyleSheet(theme.qss("""
            QComboBox {
                color: #e8f0f8; background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10); border-radius: 7px;
                padding: 2px 26px 2px 9px;
            }
            QComboBox:focus, QComboBox:on {
                border-color: rgba(79,209,224,0.65);
                background: rgba(79,209,224,0.07);
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                color: #e8f0f8; background: #10161f;
                border: 1px solid rgba(79,209,224,0.35);
                selection-background-color: rgba(79,209,224,0.25);
                selection-color: #ffffff; outline: none;
            }
        """))
        line = self.lineEdit()
        line.setPlaceholderText("Search a station…")
        line.setStyleSheet(theme.qss("background: transparent; border: none;"
                           "color: #e8f0f8; padding: 0 2px;"))
        line.setTextMargins(0, 0, 18, 0)
        palette = line.palette()
        palette.setColor(QPalette.ColorRole.Text, QColor(232, 240, 248))
        palette.setColor(QPalette.ColorRole.PlaceholderText,
                         QColor(120, 137, 155))
        line.setPalette(palette)
        completer = QCompleter(self.model(), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setMaxVisibleItems(14)
        popup = completer.popup()
        popup.setFont(theme.font(9))
        popup.setStyleSheet(theme.qss("""
            QListView {
                color: #e8f0f8; background: #10161f;
                border: 1px solid rgba(79,209,224,0.35);
                selection-background-color: rgba(79,209,224,0.25);
                selection-color: #ffffff; outline: none;
            }
        """))
        self.setCompleter(completer)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(150, 166, 182), 1.3))
        x = w - 13
        y = h // 2
        p.drawLine(x - 3, y - 2, x, y + 1)
        p.drawLine(x, y + 1, x + 3, y - 2)


def _button(text: str, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setFixedHeight(theme.px(28))
    if primary:
        button.setStyleSheet(theme.qss("""
            QPushButton {
                color: #071117; background: #4fd1e0;
                border: 1px solid #4fd1e0; border-radius: 7px;
                font-size: 8px; font-weight: 700; letter-spacing: 1.0px;
                padding: 0 10px;
            }
            QPushButton:hover { background: #7fe0ec; border-color: #7fe0ec; }
        """))
    else:
        button.setStyleSheet(theme.qss("""
            QPushButton {
                color: rgba(168,184,200,0.95); background: rgba(79,209,224,0.06);
                border: 1px solid rgba(79,209,224,0.18); border-radius: 7px;
                font-size: 8px; font-weight: 600; letter-spacing: 1.0px;
                padding: 0 10px;
            }
            QPushButton:hover {
                background: rgba(79,209,224,0.14); color: #e6f7fa;
            }
        """))
    return button


class _NoteRow(QWidget):
    edit_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, note: InventoryNote, parent=None):
        super().__init__(parent)
        self.note = note
        self._hover = False
        self._confirm_delete = False
        self.setFixedHeight(theme.px(ROW_HEIGHT))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            f"{note.item} at {note.location}\n"
            f"Click to edit · ✕ to delete")

        self.delete_btn = QPushButton("✕", self)
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.delete_btn.setFixedSize(theme.px(20), theme.px(20))
        self.delete_btn.setToolTip("Delete this note")
        self.delete_btn.clicked.connect(self._delete_clicked)
        self._style_delete(False)

        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.timeout.connect(self._reset_delete)

    # ------------------------------------------------------------------
    def _style_delete(self, danger: bool) -> None:
        color = "#ffb020" if danger else "rgba(150,164,180,0.8)"
        hover = "rgba(232,106,106,0.22)" if danger else "rgba(255,255,255,0.08)"
        self.delete_btn.setStyleSheet(theme.qss(f"""
            QPushButton {{
                color: {color}; background: transparent;
                border: none; border-radius: 10px; font-size: 11px;
            }}
            QPushButton:hover {{ background: {hover}; color: #ffd9d9; }}
        """))

    def _reset_delete(self) -> None:
        self._confirm_delete = False
        self.delete_btn.setText("✕")
        self.delete_btn.setToolTip("Delete this note")
        self._style_delete(False)

    def _delete_clicked(self) -> None:
        if not self._confirm_delete:
            self._confirm_delete = True
            self.delete_btn.setText("!")
            self.delete_btn.setToolTip("Click again to delete")
            self._style_delete(True)
            self._confirm_timer.start(2200)
            return
        self.delete_requested.emit(self.note.id)

    # ------------------------------------------------------------------
    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()

    def resizeEvent(self, event):  # noqa: N802
        self.delete_btn.move(self.width() - 28, (self.height() - 20) // 2)
        super().resizeEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.edit_requested.emit(self.note.id)

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        note = self.note
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 10))
            p.drawRoundedRect(QRectF(0, 1, w, h - 3), 6, 6)

        right = 30
        left = w - right - 10

        p.setFont(theme.pfont(7, QFont.Weight.Bold, spacing=1.0))
        p.setPen(theme.ACCENT)
        location = note.location.upper() or "UNKNOWN LOCATION"
        p.drawText(QRectF(4, 3, left - 86, 15),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       location, Qt.TextElideMode.ElideRight, left - 86))

        p.setFont(theme.pfont(7))
        p.setPen(theme.DIM)
        p.drawText(QRectF(w - right - 82, 3, 82, 15),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   _when(note.updated))

        p.setFont(theme.pfont(10, QFont.Weight.DemiBold))
        p.setPen(theme.TEXT if self._hover else QColor(218, 229, 240))
        p.drawText(QRectF(4, 19, left - 64, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       note.item or "Loot", Qt.TextElideMode.ElideRight, left - 64))

        if note.quantity:
            p.setFont(theme.pfont(8, QFont.Weight.DemiBold))
            p.setPen(AMBER)
            p.drawText(QRectF(w - right - 64, 19, 64, 18),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"× {note.quantity}")

        p.setFont(theme.pfont(8))
        p.setPen(theme.MUTED)
        detail = note.note or "No note"
        if not note.note:
            p.setPen(QColor(96, 112, 128))
        p.drawText(QRectF(4, 38, left, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(
                       detail, Qt.TextElideMode.ElideRight, left))

        theme.divider(p, 0, w, h - 1)


# ---------------------------------------------------------------------------
class InventoryPanel(QWidget):
    close_requested = Signal()

    def __init__(self, store: InventoryStore | None = None, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(theme.px(WIDTH), theme.px(HEIGHT))

        self.store = store or InventoryStore()
        self._editing_id: str | None = None
        self._sort = "RECENT"
        self._shown = 0
        self._drag_from = None

        self._msg_timer = QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(lambda: self.form_note.setText(""))

        self._build()
        self._rebuild_list()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(16), theme.px(12), theme.px(16), theme.px(10))
        root.setSpacing(theme.px(6))

        head = QHBoxLayout()
        head.setSpacing(theme.px(8))
        title = QLabel("INVENTORY")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        title.setStyleSheet(theme.qss(
            "color: rgba(168,184,200,0.95); background: transparent;"))

        self.status = QLabel("LOCAL")
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
            "Remember loot and supplies left at other stations. Pick a station "
            "and note what is there. Manual notes only — this does not read the game.")
        intro.setWordWrap(True)
        intro.setFont(theme.font(8))
        intro.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.92); background: transparent;"))
        root.addWidget(intro)

        self.form_title = QLabel("ADD A STASH NOTE")
        self.form_title.setFont(
            theme.font(7, QFont.Weight.Bold, spacing=1.2))
        self.form_title.setStyleSheet(theme.qss(
            "color: rgba(136,152,170,0.98); background: transparent;"))
        root.addWidget(self.form_title)

        first = QHBoxLayout()
        first.setSpacing(theme.px(6))
        self.location = _StationCombo()
        self.quantity = _line_edit("Qty", 24)
        self.quantity.setFixedWidth(theme.px(66))
        first.addWidget(self.location, 1)
        first.addWidget(self.quantity)
        root.addLayout(first)

        second = QHBoxLayout()
        second.setSpacing(theme.px(6))
        self.item_edit = _line_edit("Item or loot — e.g. FS-9 LMG", 120)
        self.submit_btn = _button("ADD", primary=True)
        self.submit_btn.setFixedWidth(theme.px(66))
        self.submit_btn.clicked.connect(self._submit)
        self.item_edit.returnPressed.connect(self._submit)
        self.location.lineEdit().returnPressed.connect(
            lambda: self.item_edit.setFocus())
        self.quantity.returnPressed.connect(lambda: self.item_edit.setFocus())
        second.addWidget(self.item_edit, 1)
        second.addWidget(self.submit_btn)
        root.addLayout(second)

        third = QHBoxLayout()
        third.setSpacing(theme.px(6))
        self.note_edit = _line_edit("Optional note — container, locker, hangar…", 220)
        self.note_edit.returnPressed.connect(self._submit)
        self.cancel_btn = _button("CANCEL")
        self.cancel_btn.setFixedWidth(theme.px(66))
        self.cancel_btn.clicked.connect(self._cancel_edit)
        self.cancel_btn.hide()
        third.addWidget(self.note_edit, 1)
        third.addWidget(self.cancel_btn)
        root.addLayout(third)

        self.form_note = QLabel(" ")
        self.form_note.setFixedHeight(theme.px(14))
        self.form_note.setFont(theme.font(7))
        self.form_note.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.9); background: transparent;"))
        root.addWidget(self.form_note)

        tools = QHBoxLayout()
        tools.setSpacing(theme.px(5))
        self.search = _line_edit("Search item, location or note…", 120)
        self.search.setFixedHeight(theme.px(26))
        self.search.textChanged.connect(self._rebuild_list)
        tools.addWidget(self.search, 1)

        self._sort_group = QButtonGroup(self)
        self._sort_group.setExclusive(True)
        self._sort_buttons: dict[str, QPushButton] = {}
        for key, label in (("RECENT", "RECENT"), ("LOCATION", "LOCATION")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == self._sort)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedHeight(theme.px(24))
            button.setStyleSheet(theme.qss(self._chip_qss()))
            button.clicked.connect(lambda _=False, k=key: self._set_sort(k))
            self._sort_group.addButton(button)
            self._sort_buttons[key] = button
            tools.addWidget(button)
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
        self.footer.setFont(
            theme.font(6, QFont.Weight.Medium, spacing=0.4))
        self.footer.setStyleSheet(theme.qss(
            "color: rgba(112,128,144,0.95); background: transparent;"))
        root.addWidget(self.footer)

    # ------------------------------------------------------------------
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
            QPushButton:checked {
                color: #4fd1e0; background: rgba(79,209,224,0.13);
                border-color: rgba(79,209,224,0.5);
            }
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

    @staticmethod
    def _empty_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(theme.font(9))
        label.setStyleSheet(theme.qss("color: rgba(130,148,166,0.9); padding: 26px 8px;"
                            "background: transparent;"))
        return label

    # ------------------------------------------------------------------
    def focus_first(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        line = self.location.lineEdit()
        line.setFocus(Qt.FocusReason.ShortcutFocusReason)
        line.selectAll()

    def _set_sort(self, key: str) -> None:
        self._sort = key
        button = self._sort_buttons.get(key)
        if button is not None:
            button.setChecked(True)
        self._rebuild_list()

    def _say(self, text: str, error: bool = False) -> None:
        self.form_note.setText(text)
        self.form_note.setStyleSheet(theme.qss(
            f"color: {'#e86a6a' if error else '#5ed68a'}; background: transparent;"))
        self._msg_timer.start(3800)

    def _submit(self) -> None:
        raw_location = self.location.currentText().strip()
        item = self.item_edit.text().strip()
        if not raw_location:
            self._say("Enter the station where the loot is stored.", True)
            self.location.lineEdit().setFocus()
            return
        location = clean_terminal_name(raw_location) or raw_location
        if not item:
            self._say("Enter the item or loot you want to remember.", True)
            self.item_edit.setFocus()
            return

        quantity = self.quantity.text().strip()
        note = self.note_edit.text().strip()
        if self._editing_id:
            updated = self.store.update(
                self._editing_id, location, item, quantity, note)
            if updated is None:
                self._say("That note no longer exists.", True)
            elif self.store.error:
                self._say(self.store.error, True)
            else:
                self._say(f"Updated {updated.item} at {updated.location}.")
        else:
            added = self.store.add(location, item, quantity, note)
            if self.store.error:
                self._say(self.store.error, True)
            else:
                self._say(f"Saved {added.item} at {added.location}.")
        self._clear_form()
        self._rebuild_list()

    def _begin_edit(self, note_id: str) -> None:
        note = self.store.get(note_id)
        if note is None:
            return
        self._editing_id = note.id
        self.form_title.setText("EDIT STASH NOTE")
        self.submit_btn.setText("SAVE")
        self.cancel_btn.show()
        if self.location.findText(note.location) >= 0:
            self.location.setCurrentText(note.location)
        else:
            self.location.setEditText(note.location)
        self.quantity.setText(note.quantity)
        self.item_edit.setText(note.item)
        self.note_edit.setText(note.note)
        self._say("Editing note — Esc cancels.")
        line = self.location.lineEdit()
        line.setFocus()
        line.selectAll()

    def _cancel_edit(self) -> None:
        self._clear_form()
        self.form_note.setText("")
        self._msg_timer.stop()

    def _clear_form(self) -> None:
        self._editing_id = None
        self.form_title.setText("ADD A STASH NOTE")
        self.submit_btn.setText("ADD")
        self.cancel_btn.hide()
        self.location.setCurrentIndex(-1)
        self.location.clearEditText()
        self.quantity.clear()
        self.item_edit.clear()
        self.note_edit.clear()

    def _delete_note(self, note_id: str) -> None:
        if self.store.delete(note_id):
            if self._editing_id == note_id:
                self._clear_form()
            if self.store.error:
                self._say(self.store.error, True)
            else:
                self._say("Note removed.")
            self._rebuild_list()

    # ------------------------------------------------------------------
    @staticmethod
    def _clear(layout: QVBoxLayout) -> None:
        while layout.count() > 1:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Hide and detach immediately so a stale empty-state label
                # cannot paint over the first real row for a frame.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _rebuild_list(self) -> None:
        if not hasattr(self, "list_layout"):
            return
        self._clear(self.list_layout)
        query = self.search.text().strip().casefold() if hasattr(self, "search") else ""
        notes = self.store.all()
        if query:
            notes = [
                note for note in notes
                if query in " ".join((note.location, note.item,
                                      note.quantity, note.note)).casefold()
            ]

        if self._sort == "LOCATION":
            notes.sort(key=lambda note: (
                note.location.casefold(), note.item.casefold(), -note.updated))
        else:
            notes.sort(key=lambda note: -note.updated)

        self._shown = len(notes)
        if not notes:
            if not self.store.notes:
                text = ("No stash notes yet.\n\n"
                        "Add loot or supplies you left somewhere and it will "
                        "stay here between sessions.")
            elif query:
                text = f"No notes match “{self.search.text().strip()}”."
            else:
                text = "No notes to show."
            self.list_layout.insertWidget(0, self._empty_label(text))
        else:
            for note in notes:
                row = _NoteRow(note)
                row.edit_requested.connect(self._begin_edit)
                row.delete_requested.connect(self._delete_note)
                self.list_layout.insertWidget(
                    self.list_layout.count() - 1, row)

        self._update_footer()

    def _update_footer(self) -> None:
        total = len(self.store.notes)
        shown = self._shown
        noun = "NOTE" if total == 1 else "NOTES"
        count = (f"{shown}/{total} {noun}" if self.search.text().strip()
                 else f"{total} {noun}")
        parts = ["MANUAL STASH LOG", count]
        if self.store.error:
            parts.append("FILE ERROR")
            self.status.setText("ERROR")
            self.status.setStyleSheet(theme.qss(
                "color: rgba(232,106,106,0.95); background: transparent;"))
        else:
            self.status.setText("LOCAL")
            self.status.setStyleSheet(theme.qss(
                "color: rgba(94,214,138,0.95); background: transparent;"))
        self.footer.setText("  ·  ".join(parts))

    # ------------------------------------------------------------------
    def place_default(self, anchor: QWidget | None = None) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        if anchor is not None and anchor.isVisible():
            x = anchor.x() + anchor.width() + 12
            if x + self.width() > geo.right() - 8:
                x = anchor.x() - self.width() - 12
            if x < geo.left() + 8:
                x = anchor.x()
            y = min(anchor.y(), geo.bottom() - self.height() - 8)
            self.move(max(geo.left() + 8, x), max(geo.top() + 8, y))
        else:
            self.move(geo.center().x() - self.width() // 2, geo.top() + 132)

    def clamp_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.geometry().center()) \
            or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(self.x(), geo.left() - 20), geo.right() - self.width() + 20)
        y = min(max(self.y(), geo.top()), geo.bottom() - 60)
        self.move(x, y)

    # ------------------------------------------------------------------
    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if self._editing_id:
                self._cancel_edit()
            else:
                self.close_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

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
