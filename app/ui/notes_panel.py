"""NOTES: a small always-on-top notepad that saves itself."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QPalette, QPen)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QTextEdit,
                               QVBoxLayout, QWidget)

from . import theme
from ..notes import NotesStore

WIDTH = 420
HEIGHT = 480
SAVE_DELAY_MS = 700

GREEN = QColor(94, 214, 138)
AMBER = QColor(255, 176, 32)
RED = QColor(232, 106, 106)


class NotesPanel(QWidget):
    close_requested = Signal()

    def __init__(self, store: NotesStore | None = None, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(theme.px(WIDTH), theme.px(HEIGHT))

        self._store = store or NotesStore()
        self._drag_from = None

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.flush)

        self._build()
        self._load()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(16), theme.px(12), theme.px(16), theme.px(10))
        root.setSpacing(theme.px(6))

        head = QHBoxLayout()
        head.setSpacing(theme.px(8))
        title = QLabel("NOTES")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        title.setStyleSheet(theme.qss(
            "color: rgba(168,184,200,0.95); background: transparent;"))

        self.status = QLabel("SAVED")
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
        close.clicked.connect(self._on_close)

        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.status)
        head.addWidget(close)
        root.addLayout(head)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setPlaceholderText(
            "Anything worth remembering — routes, buyers, spawn rules, prices…")
        self.editor.setFont(theme.font(10))
        self.editor.setStyleSheet(theme.qss("""
            QTextEdit {
                color: #e8f0f8; background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.10); border-radius: 7px;
                padding: 6px 8px; font-size: 10px;
                selection-background-color: rgba(79,209,224,0.35);
            }
            QTextEdit:focus {
                border-color: rgba(79,209,224,0.65);
                background: rgba(79,209,224,0.07);
            }
            QScrollBar:vertical { background: transparent; width: 6px; }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.18); border-radius: 3px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0; }
        """))
        palette = self.editor.palette()
        palette.setColor(QPalette.ColorRole.Text, QColor(232, 240, 248))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 137, 155))
        self.editor.setPalette(palette)
        self.editor.textChanged.connect(self._on_changed)
        root.addWidget(self.editor, 1)

        self.footer = QLabel("")
        self.footer.setFont(theme.font(7, QFont.Weight.Medium, spacing=0.8))
        self.footer.setStyleSheet(theme.qss(
            "color: rgba(102,118,134,0.95); background: transparent;"))
        self.footer.setToolTip(f"Saved to {self._store.path}")
        root.addWidget(self.footer)

    # ------------------------------------------------------------------
    def _load(self) -> None:
        text = self._store.load()
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)
        self._set_status("SAVED", theme.MUTED)
        self._update_footer()

    def _on_changed(self) -> None:
        self._set_status("SAVING…", AMBER)
        self._update_footer()
        self._save_timer.start()

    def flush(self) -> None:
        """Write the current text now (timer, close, hide or quit)."""
        if self._save_timer.isActive():
            self._save_timer.stop()
        try:
            self._store.save(self.editor.toPlainText())
        except OSError as exc:
            self._set_status("NOT SAVED", RED)
            self.footer.setText(f"Could not save: {exc}")
            return
        self._set_status("SAVED", GREEN)
        self._update_footer()

    def _on_close(self) -> None:
        self.flush()
        self.close_requested.emit()

    def _set_status(self, text: str, color: QColor) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(theme.qss(
            f"color: rgba({color.red()},{color.green()},{color.blue()},0.95);"
            "background: rgba(255,255,255,0.04);"
            "border-radius: 5px; padding: 2px 6px;"))

    def _update_footer(self) -> None:
        chars = len(self.editor.toPlainText())
        self.footer.setText(
            f"{chars} CHARACTERS · AUTOSAVES · {self._store.path.name}")

    # ------------------------------------------------------------------
    def place_default(self, anchor: QWidget | None = None) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        if anchor is not None and anchor.isVisible():
            x = anchor.x() + anchor.width() + 12
            y = anchor.y()
            if x + self.width() > geo.right() - 8:
                x = max(geo.left() + 8, geo.right() - self.width() - 8)
            if y + self.height() > geo.bottom() - 8:
                y = max(geo.top() + 8, geo.bottom() - self.height() - 8)
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
    def hideEvent(self, event):  # noqa: N802
        # Hiding is the last moment the text is certainly still in memory.
        self.flush()
        super().hideEvent(event)

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
