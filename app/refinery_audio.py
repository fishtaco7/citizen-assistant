"""Tiny, headless audio alert worker for completed refinery orders.

The overlay should not depend on Qt for this alert, and it must not block the
timer thread while an operating-system speech engine starts.  ``RefineryAudio``
therefore owns one daemon worker and a small queue.

Priority:

1. OS speech (``say`` on macOS, SAPI through PowerShell on Windows,
   ``spd-say``/``espeak`` on Linux) saying "Refinery order complete".
2. An optional local WAV file supplied by the host.
3. A system beep as a last resort.

Everything is local; no network request is made.
"""
from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Sequence

DEFAULT_PHRASE = "Refinery order complete"


class RefineryAudio:
    """Queue-backed audio notifier; ``notify`` never blocks the caller."""

    _SENTINEL = object()

    def __init__(
            self,
            enabled: bool = True,
            phrase: str = DEFAULT_PHRASE,
            wav_path: str | Path | None = None,
            *,
            platform: str | None = None,
            command_runner: Callable[..., object] | None = None,
            timeout: float = 15.0):
        self._enabled = bool(enabled)
        self.phrase = phrase or DEFAULT_PHRASE
        self.wav_path = Path(wav_path).expanduser() if wav_path else None
        self._platform = platform or sys.platform
        self._runner = command_runner or subprocess.run
        self._timeout = max(1.0, float(timeout))
        self._queue: queue.Queue = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self.last_error = ""

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self.stop(wait=False)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop.is_set())

    @property
    def available(self) -> bool:
        return self.enabled and self._speech_command() is not None

    def start(self) -> "RefineryAudio":
        with self._lock:
            if self.running:
                return self
            previous = self._thread
        if (previous is not None and previous.is_alive()
                and previous is not threading.current_thread()):
            # A non-blocking stop may still be draining its sentinel.
            previous.join(1.0)
        with self._lock:
            if self.running:
                return self
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="sc-refinery-audio", daemon=True)
            self._thread.start()
        return self

    def notify(self, job: object | None = None) -> bool:
        """Queue one audible alert.  Returns False when disabled."""
        if not self.enabled:
            return False
        self.start()
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            # Keep the newest alert rather than growing without bound.
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except (queue.Empty, ValueError):
                pass
            try:
                self._queue.put_nowait(job)
                return True
            except queue.Full:
                return False

    def stop(self, wait: bool = True, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            try:
                self._queue.put_nowait(self._SENTINEL)
            except queue.Full:
                # Make room for the sentinel; the worker must always be able
                # to observe shutdown even when a burst filled the queue.
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except (queue.Empty, ValueError):
                    pass
                try:
                    self._queue.put_nowait(self._SENTINEL)
                except queue.Full:
                    pass
        if (wait and thread is not None
                and thread is not threading.current_thread()):
            thread.join(timeout)
        if thread is None or not thread.is_alive():
            self._thread = None

    def close(self) -> None:
        self._enabled = False
        self.stop(wait=True)

    # ------------------------------------------------------------------
    def _run(self) -> None:
        current = threading.current_thread()
        try:
            while True:
                item = self._queue.get()
                try:
                    if item is self._SENTINEL:
                        return
                    self._play(item)
                except Exception as exc:       # never kill the worker
                    self.last_error = str(exc)
                finally:
                    self._queue.task_done()
        finally:
            with self._lock:
                if self._thread is current:
                    self._thread = None

    def _play(self, job: object | None) -> bool:
        command = self._speech_command()
        if command and self._run_command(command):
            return True

        wav_command = self._wav_command()
        if wav_command and self._run_command(wav_command):
            return True

        return self._beep()

    def _run_command(self, command: Sequence[str]) -> bool:
        options = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "timeout": self._timeout,
            "check": False,
        }
        # powershell.exe is a console program: launched from pythonw without
        # CREATE_NO_WINDOW it flashes a black console window on every
        # notification.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flags and self._platform == "win32":
            options["creationflags"] = flags
        try:
            result = self._runner(list(command), **options)
        except Exception as exc:
            self.last_error = str(exc)
            return False
        return getattr(result, "returncode", 0) == 0

    # ------------------------------------------------------------------
    def _speech_command(self) -> list[str] | None:
        phrase = self.phrase
        if self._platform == "win32":
            powershell = shutil.which("powershell") or shutil.which("pwsh")
            if powershell is None:
                return None
            escaped = phrase.replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                f".Speak('{escaped}')"
            )
            return [powershell, "-NoProfile", "-NonInteractive",
                    "-Command", script]

        if self._platform == "darwin":
            say = shutil.which("say")
            return [say, phrase] if say else None

        for name in ("spd-say", "espeak-ng", "espeak"):
            path = shutil.which(name)
            if path:
                return [path, phrase]
        return None

    def _wav_command(self) -> list[str] | None:
        if self.wav_path is None:
            return None
        try:
            if not self.wav_path.is_file():
                return None
        except OSError:
            return None

        if self._platform == "darwin":
            player = shutil.which("afplay")
            return [player, str(self.wav_path)] if player else None
        if self._platform == "win32":
            powershell = shutil.which("powershell") or shutil.which("pwsh")
            if powershell is None:
                return None
            escaped = str(self.wav_path).replace("'", "''")
            return [
                powershell, "-NoProfile", "-NonInteractive", "-Command",
                f"(New-Object Media.SoundPlayer '{escaped}').PlaySync()",
            ]
        for name in ("paplay", "aplay", "ffplay"):
            player = shutil.which(name)
            if player is None:
                continue
            if name == "ffplay":
                return [player, "-nodisp", "-autoexit", str(self.wav_path)]
            return [player, str(self.wav_path)]
        return None

    def _beep(self) -> bool:
        if self._platform == "win32":
            try:
                import winsound  # type: ignore[import-not-found]
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                return True
            except Exception as exc:
                self.last_error = str(exc)
                return False

        if self._platform == "darwin":
            osascript = shutil.which("osascript")
            if osascript:
                return self._run_command([osascript, "-e", "beep 2"])
            return False

        # Linux terminal bell is harmless when no sound server is available.
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
            return True
        except Exception:
            return False


__all__ = ["DEFAULT_PHRASE", "RefineryAudio"]
