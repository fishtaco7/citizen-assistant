"""Spoken ore announcements.

Uses QtTextToSpeech so the same code speaks through SAPI on Windows and
AVSpeechSynthesizer on macOS, with no extra dependency (Qt Speech ships in
PySide6-Addons).

That plugin needs speech-dispatcher on Linux, which is often absent, so the
Linux path falls back to the same command-line speakers the refinery alerts use
(``espeak-ng`` or ``spd-say``). If neither exists the class degrades to a no-op.

Announcements are made by the controller only when a *new* signature locks, so
a rock that stays on screen is spoken once, not four times a second.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except Exception:          # Qt built without the Speech module
    QTextToSpeech = None    # type: ignore[assignment]

LINUX = sys.platform.startswith("linux")


def _linux_speaker() -> list[str] | None:
    """Command-line TTS prefix, or None when nothing is installed."""
    for name in ("espeak-ng", "espeak", "spd-say"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


class Voice:
    def __init__(self) -> None:
        self._engine = None
        self._cli: list[str] | None = None
        self._cli_process: subprocess.Popen | None = None
        self._enabled = True
        self._volume = 0.8

        if QTextToSpeech is not None:
            try:
                self._engine = QTextToSpeech()
                self._engine.setVolume(self._volume)
            except Exception:
                self._engine = None

        if LINUX and not self.available:
            # The Qt plugin is missing or failed: use a CLI speaker instead.
            self._engine = None
            self._cli = _linux_speaker()

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        if self._cli is not None:
            return True
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

        if self._cli is not None:
            return self._say_cli(cleaned)

        try:
            self._engine.stop()
            self._engine.say(cleaned)
            return True
        except Exception:
            return False

    def _say_cli(self, text: str) -> bool:
        argv = list(self._cli)
        if argv[0].endswith(("espeak-ng", "espeak")):
            argv += ["-a", str(int(self._volume * 200)), "-s", "165"]
        self.stop()
        try:
            self._cli_process = subprocess.Popen(
                argv + [text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            return True
        except OSError:
            return False

    def stop(self) -> None:
        if self._cli_process is not None:
            try:
                self._cli_process.terminate()
            except OSError:
                pass
            self._cli_process = None
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
