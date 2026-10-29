"""Spoken ore announcements.

Uses QtTextToSpeech so the same code speaks through SAPI on Windows and
AVSpeechSynthesizer on macOS, with no extra dependency (Qt Speech ships in
PySide6-Addons). If no engine is available the class degrades to a no-op.

Announcements are made by the controller only when a *new* signature locks, so
a rock that stays on screen is spoken once, not four times a second.
"""
from __future__ import annotations

import re

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except Exception:          # Qt built without the Speech module
    QTextToSpeech = None    # type: ignore[assignment]


class Voice:
    def __init__(self) -> None:
        self._engine = None
        self._enabled = True
        self._volume = 0.8
        if QTextToSpeech is None:
            return
        try:
            self._engine = QTextToSpeech()
            self._engine.setVolume(self._volume)
        except Exception:
            self._engine = None

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        if self._engine is None:
            return False
        return self._engine.state() != QTextToSpeech.State.Error

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self.stop()

    @property
    def volume(self) -> float:
        return self._volume

    def set_volume(self, value: float) -> None:
        """0.0 - 1.0, applied to the speech engine immediately."""
        self._volume = max(0.0, min(1.0, float(value)))
        if self._engine is not None:
            try:
                self._engine.setVolume(self._volume)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def say(self, text: str) -> bool:
        """Speak one short phrase, replacing anything still being spoken."""
        if not (self._enabled and text and self.available):
            return False
        cleaned = re.sub(r"[^\w\s,.'\-]", " ", str(text)).strip()
        if not cleaned:
            return False
        try:
            self._engine.stop()
            self._engine.say(cleaned)
            return True
        except Exception:
            return False

    def stop(self) -> None:
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass


def spoken_text(label: str, count: int = 1) -> str:
    """'Borase', 3 -> 'Borase, times 3'."""
    text = (label or "").strip()
    if not text:
        return ""
    if count and count > 1:
        return f"{text}, times {count}"
    return text
