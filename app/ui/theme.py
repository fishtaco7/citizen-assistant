"""Shared colours, fonts and painting helpers for the overlay widgets.

The whole UI is designed at 100%.  ``set_scale()`` applies the user's UI scale
factor (75-150%) to the design metrics so the same widget code renders at any
size on every platform: Qt's own ``QT_SCALE_FACTOR`` is a no-op for widget
geometry on macOS, so the scale is applied here instead.

Widget code uses ``px()`` for fixed pixel sizes, ``qss()`` for style sheets and
``font()`` for fonts.  Custom ``paintEvent`` code keeps using design
coordinates: ``paint()`` returns a painter pre-scaled by the UI scale together
with the design-space width/height, and ``pfont()`` returns a design-size font
(the painter transform scales it).
"""
from __future__ import annotations

import re
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
UI_FAMILY = "Helvetica Neue" if IS_MAC else "Segoe UI"

# Star Citizen HUD-inspired palette.
BG        = QColor(9, 13, 19, 236)
BG_SOFT   = QColor(16, 22, 31, 220)
BORDER    = QColor(79, 209, 224, 64)
BORDER_HI = QColor(79, 209, 224, 150)
ACCENT    = QColor(79, 209, 224)
ACCENT_D  = QColor(38, 122, 136)
AMBER     = QColor(255, 176, 32)
TEXT      = QColor(232, 240, 248)
MUTED     = QColor(130, 148, 166)
DIM       = QColor(88, 103, 120)
GOOD      = QColor(94, 214, 138)
BAD       = QColor(232, 106, 106)

RARITY = {
    5: QColor(255, 176, 32),   # legendary
    4: QColor(199, 125, 255),  # epic
    3: QColor(79, 209, 224),   # rare
    2: QColor(144, 190, 109),  # uncommon
    1: QColor(141, 153, 174),  # common
    0: QColor(255, 176, 32),   # ground deposit
}

RARITY_NAME = {5: "LEGENDARY", 4: "EPIC", 3: "RARE", 2: "UNCOMMON", 1: "COMMON", 0: "DEPOSIT"}

# ---------------------------------------------------------------------------
# UI scale
# ---------------------------------------------------------------------------
_SCALE = 1.0


def set_scale(value: float) -> float:
    """Set the global UI scale (0.75 - 1.5) and return the clamped value."""
    global _SCALE
    try:
        level = float(value)
    except (TypeError, ValueError):
        level = 1.0
    _SCALE = max(0.75, min(1.5, level))
    return _SCALE


def scale() -> float:
    return _SCALE


def px(value) -> int:
    """Scale one design pixel metric to the current UI scale."""
    return int(round(float(value) * _SCALE))


def px_f(value) -> float:
    """Like ``px`` but keeps fractional precision (for painter geometry)."""
    return float(value) * _SCALE


_QSS_PX = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)px")


def qss(text: str) -> str:
    """Scale every ``Npx`` value inside a Qt style sheet."""
    if _SCALE == 1.0 or not text:
        return text
    return _QSS_PX.sub(
        lambda m: f"{max(1, int(round(float(m.group(1)) * _SCALE)))}px", text)


def paint(widget) -> tuple[QPainter, int, int]:
    """Painter for a custom-painted widget plus its design-space size.

    The painter is pre-scaled, so paint code keeps using the 100% design
    coordinates while the widget itself is ``scale`` times larger.
    """
    painter = QPainter(widget)
    if _SCALE != 1.0:
        painter.scale(_SCALE, _SCALE)
    return (painter,
            max(1, int(round(widget.width() / _SCALE))),
            max(1, int(round(widget.height() / _SCALE))))


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
def _font(size: float, weight: QFont.Weight, family: str,
          spacing: float, factor: float) -> QFont:
    f = QFont(family)
    f.setPointSizeF(max(1.0, float(size) * factor))
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing * factor)
    return f


def font(size: int, weight: QFont.Weight = QFont.Weight.Normal,
         family: str = UI_FAMILY, spacing: float = 0.0) -> QFont:
    """Scaled UI font, for labels, buttons and other real widgets."""
    return _font(size, weight, family, spacing, _SCALE)


def pfont(size: int, weight: QFont.Weight = QFont.Weight.Normal,
          spacing: float = 0.0) -> QFont:
    """Design-size font for paint code running inside ``paint()``."""
    return _font(size, weight, UI_FAMILY, spacing, 1.0)


def _hud_family() -> str:
    names = (("Avenir Next Condensed", "Helvetica Neue")
             if IS_MAC else
             ("Bahnschrift SemiCondensed", "Bahnschrift", "Segoe UI Semibold"))
    for name in names:
        if name == "Segoe UI Semibold" or QFont(name).exactMatch():
            return name
    return UI_FAMILY


def hud_font(size: int, weight: QFont.Weight = QFont.Weight.DemiBold) -> QFont:
    """Condensed technical face used for the big readouts (scaled)."""
    return _font(size, weight, _hud_family(), 0.0, _SCALE)


def phud_font(size: int, weight: QFont.Weight = QFont.Weight.DemiBold) -> QFont:
    """Condensed technical face at design size (for ``paint()`` code)."""
    return _font(size, weight, _hud_family(), 0.0, 1.0)


def rarity_color(rarity: int) -> QColor:
    return RARITY.get(rarity, RARITY[1])


def card_path(rect: QRectF, radius: float = 14.0,
              bottom_only: bool = False) -> QPainterPath:
    path = QPainterPath()
    if bottom_only:
        path.addRoundedRect(QRectF(rect.x(), rect.y() - radius, rect.width(),
                                   rect.height() + radius), radius, radius)
    else:
        path.addRoundedRect(rect, radius, radius)
    return path


def paint_card(painter: QPainter, rect: QRectF, radius: float = 14.0,
               fill: QColor = BG, border: QColor = BORDER,
               bottom_only: bool = False, glow: bool = False) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = card_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, bottom_only)

    if glow:
        grad = QLinearGradient(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
        grad.setColorAt(0.0, QColor(31, 52, 66, 240))
        grad.setColorAt(1.0, QColor(9, 13, 19, 240))
        painter.fillPath(path, grad)
    else:
        painter.fillPath(path, fill)

    painter.setPen(QPen(border, 1.0))
    painter.drawPath(path)


def divider(painter: QPainter, x1: float, x2: float, y: float,
            color: QColor | None = None) -> None:
    painter.setPen(QPen(color or QColor(79, 209, 224, 40), 1.0))
    painter.drawLine(QPointF(x1, y), QPointF(x2, y))
