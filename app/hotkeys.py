"""Global hotkeys via RegisterHotKey (Windows only).

RegisterHotKey needs a thread with a message pump, so the bindings run on a
dedicated QThread. Emitting a Qt signal from there is safe - Qt queues the
delivery onto the GUI thread automatically.

macOS has no permission-free equivalent (CGEventTap needs Accessibility
approval), so on macOS this class is a no-op and ``main`` installs application
shortcuts for F3/F4 instead. The tray menu always works.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QThread, Signal

WINDOWS = sys.platform == "win32"

if WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    MOD_NOREPEAT = 0x4000
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


class HotkeyThread(QThread):
    """One signal per registered hotkey id."""

    pressed = Signal(int)

    def __init__(self, bindings: dict[int, int], parent=None):
        super().__init__(parent)
        self._bindings = dict(bindings)   # id -> virtual key
        self._thread_id = 0
        self._failed: list[int] = []
        self._stopping = False

    @property
    def failed(self) -> list[int]:
        return list(self._failed)

    @property
    def supported(self) -> bool:
        return WINDOWS

    def run(self) -> None:  # noqa: D102
        if not WINDOWS:
            return
        # Publish the thread id first: stop() can then always reach us with
        # WM_QUIT.  If it ran before the id existed it set _stopping instead,
        # which we check right away so a fast quit cannot leave this thread
        # parked in GetMessageW (and the process alive) forever.
        self._thread_id = kernel32.GetCurrentThreadId()
        if self._stopping:
            return
        registered: list[int] = []
        for hk_id, vk in self._bindings.items():
            if user32.RegisterHotKey(None, hk_id, MOD_NOREPEAT, vk):
                registered.append(hk_id)
            else:
                self._failed.append(hk_id)

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.pressed.emit(int(msg.wParam))

        for hk_id in registered:
            user32.UnregisterHotKey(None, hk_id)

    def stop(self) -> None:
        self._stopping = True
        if WINDOWS and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


def vk_name(vk: int) -> str:
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    return f"VK_{vk:02X}"
