"""Full-screen drag-to-select tool for calibrating the capture region."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import theme


class RegionSelector(QWidget):
    selected = Signal(QRect)     # global, logical pixels
    cancelled = Signal()

    def __init__(self, hint: str = "Drag a box around the scan signature"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        union = QRect()
        for screen in QGuiApplication.screens():
            union = union.united(screen.geometry())
        self.setGeometry(union)

        self._hint = hint
        self._origin: QPoint | None = None
        self._rect = QRect()
        self._cursor = QPoint()

    # ------------------------------------------------------------------
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._rect = QRect(self._origin, self._origin)
            self.update()

    def mouseMoveEvent(self, event):  # noqa: N802
        self._cursor = event.position().toPoint()
        if self._origin is not None:
            self._rect = QRect(self._origin, self._cursor).normalized()
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        rect = QRect(self._origin, event.position().toPoint()).normalized()
        self._origin = None
        if rect.width() >= 8 and rect.height() >= 8:
            top_left = self.mapToGlobal(rect.topLeft())
            self.selected.emit(QRect(top_left, rect.size()))
            self.close()
        else:
            self._rect = QRect()
            self.update()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Dim everything except the current selection.
        p.fillRect(self.rect(), QColor(4, 7, 11, 150))
        if not self._rect.isNull():
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(self._rect, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            p.setPen(QPen(theme.ACCENT, 1.5))
            p.drawRect(self._rect)

            # Corner ticks
            p.setPen(QPen(theme.ACCENT, 3.0))
            r = self._rect
            tick = min(16, r.width() // 3, r.height() // 3)
            for start, end in (
                (r.topLeft(), r.topLeft() + QPoint(tick, 0)),
                (r.topLeft(), r.topLeft() + QPoint(0, tick)),
                (r.topRight(), r.topRight() + QPoint(-tick, 0)),
                (r.topRight(), r.topRight() + QPoint(0, tick)),
                (r.bottomLeft(), r.bottomLeft() + QPoint(tick, 0)),
                (r.bottomLeft(), r.bottomLeft() + QPoint(0, -tick)),
                (r.bottomRight(), r.bottomRight() + QPoint(-tick, 0)),
                (r.bottomRight(), r.bottomRight() + QPoint(0, -tick)),
            ):
                p.drawLine(start, end)

            label = f"{r.width()} Ã— {r.height()}"
            p.setFont(theme.pfont(9, QFont.Weight.DemiBold))
            p.setPen(theme.TEXT)
            p.drawText(r.adjusted(0, 0, 0, -6).translated(0, -18),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, label)

        # Instructions
        p.setFont(theme.pfont(10, QFont.Weight.DemiBold, spacing=1.0))
        p.setPen(theme.TEXT)
        title_rect = QRectF(0, 48, self.width(), 24)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignHCenter,
                   self._hint.upper())

        p.setFont(theme.pfont(9))
        p.setPen(theme.MUTED)
        p.drawText(QRectF(0, 74, self.width(), 20), Qt.AlignmentFlag.AlignHCenter,
                   "ESC to cancel  Â·  release to confirm  Â·  region is stored in physical pixels")

        # Crosshair
        if self._cursor:
            p.setPen(QPen(QColor(79, 209, 224, 170), 1.0, Qt.PenStyle.DashLine))
            c = self._cursor
            p.drawLine(QPoint(0, c.y()), QPoint(self.width(), c.y()))
            p.drawLine(QPoint(c.x(), 0), QPoint(c.x(), self.height()))
