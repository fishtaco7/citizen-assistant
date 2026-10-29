"""Draw a known signature in a window so the capture path can be tested live.

    python -m tools.signage 10710
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget


class Signage(QWidget):
    def __init__(self, text: str):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.text = text
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(420, 120)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 22, 28))
        p.setPen(QColor(240, 244, 248))
        p.setFont(QFont("Bahnschrift SemiCondensed", 46, QFont.Weight.DemiBold))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)
        p.setPen(QColor(79, 209, 224))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else "10,710"
    app = QApplication(sys.argv[:1])
    w = Signage(text)
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio()
    pos = (300, 760)
    w.move(*pos)
    w.show()

    def report():
        geo = w.geometry()
        print(json.dumps({
            "left": int(geo.x() * dpr),
            "top": int(geo.y() * dpr),
            "width": int(geo.width() * dpr),
            "height": int(geo.height() * dpr),
        }), flush=True)

    QTimer.singleShot(400, report)
    QTimer.singleShot(45000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
