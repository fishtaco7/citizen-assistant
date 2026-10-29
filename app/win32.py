"""Native window helpers for the overlay.

On Windows this is ctypes against user32: topmost, click-through, capture
exclusion and physical-pixel monitor enumeration.

On macOS the same public functions are implemented with Qt attributes, because
Win32 has no equivalent there. Two Windows-only behaviours are intentionally
no-ops:

* ``exclude_from_capture`` - macOS would need ScreenCaptureKit /
  ``NSWindow.sharingType``; for the demo build the windows stay visible in
  screenshots, which is friendlier while developing.
* ``click_through`` - Qt can ignore mouse events per widget, but the scanner and
  ore finder need the mouse to be draggable, so this is only applied when asked.

Call sites pass the QWidget (not a raw handle); on Windows it is converted to an
HWND internally.
"""
from __future__ import annotations

import sys

WINDOWS = sys.platform == "win32"

if WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    GWL_EXSTYLE = -20

    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOPMOST = 0x00000008

    WDA_NONE = 0x0
    WDA_MONITOR = 0x1
    WDA_EXCLUDEFROMCAPTURE = 0x11

    HWND_TOPMOST = wintypes.HWND(-1)
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040

    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", ctypes.c_wchar * 32),
        ]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )

    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC, ctypes.POINTER(RECT), MONITORENUMPROC, wintypes.LPARAM,
    ]
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL


def _hwnd(target) -> int:
    """Accept either a QWidget or an already-native handle."""
    return int(target.winId()) if hasattr(target, "winId") else int(target)


def _widget(target):
    return target if hasattr(target, "winId") else None


if WINDOWS:

    def hwnd_of(widget) -> int:
        """Return the native HWND for a QWidget."""
        return int(widget.winId())

    def _ex_style(hwnd: int) -> int:
        return int(user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE))

    def _set_ex_style(hwnd: int, style: int) -> None:
        user32.SetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE, ctypes.c_long(style))

    def _set_topmost(hwnd: int) -> None:
        user32.SetWindowPos(
            wintypes.HWND(hwnd), HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def make_overlay_window(target, click_through: bool = False) -> None:
        """Turn a normal Qt window into a non-activating always-on-top overlay.

        WS_EX_NOACTIVATE keeps the game focused when you click the overlay, and
        WS_EX_TOOLWINDOW keeps it out of the alt-tab list.
        """
        hwnd = _hwnd(target)
        style = _ex_style(hwnd)
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if click_through:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        _set_ex_style(hwnd, style)
        _set_topmost(hwnd)

    def make_focusable_window(target, click_through: bool = False) -> None:
        """Always-on-top tool window that *can* take keyboard focus.

        Used for panels with text input: explicitly clears WS_EX_NOACTIVATE.
        """
        hwnd = _hwnd(target)
        style = _ex_style(hwnd)
        style &= ~WS_EX_NOACTIVATE
        style |= WS_EX_TOOLWINDOW
        if click_through:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        _set_ex_style(hwnd, style)
        _set_topmost(hwnd)

    def exclude_from_capture(target) -> bool:
        """Hide the window from screen capture (OBS, screenshots, our own OCR).

        ``WDA_EXCLUDEFROMCAPTURE`` needs Windows 10 2004 or newer.  On older
        builds the window cannot be removed from a capture, so fall back to
        ``WDA_MONITOR``: the window still shows on screen but is rendered black
        in every capture, which keeps it out of the OCR pipeline and out of
        streams.  ``WDA_NONE`` would leave it fully visible instead.
        """
        hwnd = wintypes.HWND(_hwnd(target))
        ok = user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        if not ok:
            ok = user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR)
        return bool(ok)

    def allow_capture(target) -> None:
        user32.SetWindowDisplayAffinity(wintypes.HWND(_hwnd(target)), WDA_NONE)

    def keep_on_top(target) -> None:
        _set_topmost(_hwnd(target))

    def monitors() -> list[dict]:
        """Physical-pixel rects for every monitor, plus its device name."""
        found: list[dict] = []

        def _cb(hmon, hdc, lprc, data):
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcMonitor
                found.append({
                    "name": info.szDevice,
                    "left": r.left,
                    "top": r.top,
                    "width": r.right - r.left,
                    "height": r.bottom - r.top,
                })
            return True

        user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
        return found

else:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    def hwnd_of(widget) -> int:
        return int(widget.winId())

    def make_overlay_window(target, click_through: bool = False) -> None:
        """Qt equivalent: float above normal windows, never take focus."""
        w = _widget(target)
        if w is None:
            return
        w.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if click_through:
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def make_focusable_window(target, click_through: bool = False) -> None:
        w = _widget(target)
        if w is None:
            return
        w.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        if click_through:
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def exclude_from_capture(target) -> bool:
        # Not supported in the macOS demo backend; see the module docstring.
        return False

    def allow_capture(target) -> None:
        return None

    def keep_on_top(target) -> None:
        # macOS floats these windows via WindowStaysOnTopHint and keeps the
        # level without help. Calling raise_() here (as the Win32 backend does
        # with SetWindowPos) activates the app every couple of seconds, which
        # stole keyboard focus from whatever the user was typing in.
        return None

    def monitors() -> list[dict]:
        """Physical-pixel rects, derived from Qt screens.

        Qt reports logical points; multiplying by the backing scale gives the
        pixel size mss uses. Mixed-DPI multi-monitor layouts are approximated
        by positioning each screen in the same scaled space.
        """
        found: list[dict] = []
        for screen in QGuiApplication.screens():
            geo = screen.geometry()
            dpr = float(screen.devicePixelRatio() or 1.0)
            found.append({
                "name": screen.name(),
                "left": int(round(geo.x() * dpr)),
                "top": int(round(geo.y() * dpr)),
                "width": int(round(geo.width() * dpr)),
                "height": int(round(geo.height() * dpr)),
            })
        return found
