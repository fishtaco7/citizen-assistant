"""The F3 command bar that slides down from the top of the screen."""
from __future__ import annotations

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QPoint,
                            QPropertyAnimation, QRectF, QSize, Qt, Signal)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme

BAR_HEIGHT = 54
WIDTH_RATIO = 0.5        # half the monitor width
MIN_WIDTH = 760
TOP_MARGIN = 8


class _Brand(QWidget):
    """Small diamond mark + wordmark."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(theme.px(196), theme.px(BAR_HEIGHT - 14))

    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy, r = 13.0, h / 2.0, 8.0
        path = QPainterPath()
        path.moveTo(cx, cy - r)
        path.lineTo(cx + r, cy)
        path.lineTo(cx, cy + r)
        path.lineTo(cx - r, cy)
        path.closeSubpath()
        p.setPen(QPen(theme.ACCENT, 1.4))
        p.setBrush(QColor(79, 209, 224, 38))
        p.drawPath(path)

        p.setPen(theme.TEXT)
        p.setFont(theme.pfont(7, QFont.Weight.Bold, spacing=1.2))
        p.drawText(QRectF(28, 0, 164, h),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "CITIZEN ASSISTANT")


class NavBar(QWidget):
    tool_requested = Signal(str)
    settings_requested = Signal()
    close_requested = Signal()

    def __init__(self, tools: list[dict], parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._tools = tools
        self._buttons: dict[str, QPushButton] = {}
        self._shown = False
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(190)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.px(18), theme.px(0), theme.px(18), theme.px(0))
        outer.setSpacing(theme.px(0))

        row = QHBoxLayout()
        row.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        row.setSpacing(theme.px(4))
        outer.addLayout(row)

        row.addWidget(_Brand())
        row.addSpacing(theme.px(10))

        sep = QWidget()
        sep.setFixedSize(theme.px(1), theme.px(20))
        sep.setStyleSheet(theme.qss("background: rgba(79,209,224,0.25);"))
        row.addWidget(sep)
        row.addSpacing(theme.px(10))

        for tool in self._tools:
            btn = QPushButton(tool["label"])
            btn.setObjectName("tool")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCheckable(bool(tool.get("checkable", True)))
            btn.setEnabled(bool(tool.get("enabled", True)))
            btn.setToolTip(tool.get("hint", ""))
            btn.setStyleSheet(theme.qss(self._button_qss()))
            btn.clicked.connect(lambda _=False, tid=tool["id"]: self._on_tool(tid))
            if not tool.get("enabled", True):
                btn.setObjectName("toolDisabled")
                btn.setStyleSheet(theme.qss(self._button_qss()))
            row.addWidget(btn)
            self._buttons[tool["id"]] = btn

        row.addStretch(1)

        gear = QPushButton("⚙")
        gear.setObjectName("gear")
        gear.setCursor(Qt.CursorShape.PointingHandCursor)
        gear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        gear.setFixedSize(theme.px(26), theme.px(26))
        gear.setToolTip("Settings")
        gear.setStyleSheet(theme.qss("""
            QPushButton#gear {
                color: rgba(168,184,200,0.85); background: transparent;
                border: none; border-radius: 13px; font-size: 13px;
            }
            QPushButton#gear:hover { background: rgba(79,209,224,0.14); color: #e6f7fa; }
        """))
        gear.clicked.connect(self.settings_requested.emit)
        row.addWidget(gear)
        row.addSpacing(theme.px(10))

        hint = QLabel("F3  TOGGLE   ·   F4  HIDE ALL")
        hint.setFont(theme.font(8, QFont.Weight.Medium, spacing=0.8))
        hint.setStyleSheet(theme.qss("color: rgba(130,148,166,0.85);"))
        self._hint = hint
        row.addWidget(hint)
        row.addSpacing(theme.px(14))

        close = QPushButton("✕")
        close.setObjectName("close")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setFixedSize(theme.px(26), theme.px(26))
        close.setStyleSheet(theme.qss("""
            QPushButton#close {
                color: rgba(180,196,212,0.85); background: transparent;
                border: none; border-radius: 13px; font-size: 13px;
            }
            QPushButton#close:hover { background: rgba(232,106,106,0.22); color: #ffd9d9; }
        """))
        close.clicked.connect(self.close_requested.emit)
        row.addWidget(close)

        self.setFixedHeight(theme.px(BAR_HEIGHT))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _button_qss(self) -> str:
        return """
        QPushButton#tool {
            color: rgba(168,184,200,0.92);
            background: transparent;
            border: none;
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 10px; font-weight: 600;
            letter-spacing: 1.4px;
        }
        QPushButton#tool:hover { background: rgba(79,209,224,0.10); color: #e6f7fa; }
        QPushButton#tool:checked { background: rgba(79,209,224,0.16); color: #4fd1e0; }
        QPushButton#toolDisabled {
            color: rgba(110,124,138,0.55);
            background: transparent; border: none; border-radius: 8px;
            padding: 8px 16px;
            font-size: 10px; font-weight: 600;
            letter-spacing: 1.4px;
        }
        """

    # ------------------------------------------------------------------
    def _on_tool(self, tool_id: str) -> None:
        tool = next((t for t in self._tools if t["id"] == tool_id), None)
        if tool is None or not tool.get("enabled", True):
            return
        self.tool_requested.emit(tool_id)

    def set_active(self, tool_id: str | None) -> None:
        for tid, btn in self._buttons.items():
            btn.setChecked(tid == tool_id)

    # ------------------------------------------------------------------
    def _screen_geometry(self):
        screen = QGuiApplication.screenAt(self.geometry().center()) \
            if self.isVisible() else QGuiApplication.primaryScreen()
        return (screen or QGuiApplication.primaryScreen()).geometry()

    def place(self) -> None:
        stopped = self._anim.state() == QAbstractAnimation.State.Stopped
        if self._shown or stopped:
            self.move(self._target_pos())

    def _target_pos(self) -> QPoint:
        geo = self._screen_geometry()
        # Drop the keyboard hint on narrow displays so the tool labels keep
        # their full width instead of being squeezed by the layout.
        self._hint.setVisible(geo.width() >= theme.px(1200))
        self.layout().activate()
        # sizeHint() is the width the row actually needs; on macOS the UI font
        # is a little wider than Segoe UI, so a fixed 760px clipped the labels.
        # The result is already in screen (logical) pixels, so it must not be
        # scaled a second time.
        needed = self.layout().sizeHint().width() + theme.px(24)
        width = min(max(theme.px(MIN_WIDTH), int(geo.width() * WIDTH_RATIO), needed),
                    geo.width() - theme.px(40))
        self.setFixedWidth(width)
        return QPoint(geo.x() + (geo.width() - width) // 2,
                      geo.y() + theme.px(TOP_MARGIN))

    def slide_in(self) -> None:
        if self._shown:
            return
        geo = self._screen_geometry()
        end = self._target_pos()
        start = QPoint(end.x(), geo.y() - self.height())
        self.move(start)
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()
        self._shown = True

    def slide_out(self) -> None:
        if not self._shown:
            return
        geo = self._screen_geometry()
        start = self.pos()
        end = QPoint(self.x(), geo.y() - self.height())
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.finished.connect(self._after_hide)
        self._anim.start()
        self._shown = False

    def _after_hide(self) -> None:
        try:
            self._anim.finished.disconnect(self._after_hide)
        except RuntimeError:
            pass
        if not self._shown:
            self.hide()

    def toggle(self) -> None:
        self.slide_out() if self._shown else self.slide_in()

    @property
    def shown(self) -> bool:
        return self._shown

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        theme.paint_card(p, QRectF(0, 0, w, h), radius=13, bottom_only=False,
                         glow=True)
        # Accent hairline along the bottom edge.
        p.setPen(QPen(QColor(79, 209, 224, 120), 1.0))
        p.drawLine(14, h - 1, w - 14, h - 1)
