"""Screen region capture. mss is used because it is fast and dependency-light.

mss instances are not thread safe, so grabbers are created per thread.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


class ScreenGrabber:
    def __init__(self):
        self._sct = None

    def _ensure(self):
        if self._sct is None:
            import mss  # imported lazily so the module can be used headless
            factory = getattr(mss, "MSS", None) or mss.mss
            self._sct = factory()
        return self._sct

    def grab(self, region: dict) -> np.ndarray:
        """Grab a physical-pixel rect and return a BGR numpy array."""
        import numpy as np

        sct = self._ensure()
        shot = sct.grab({
            "left": int(region["left"]),
            "top": int(region["top"]),
            "width": max(1, int(region["width"])),
            "height": max(1, int(region["height"])),
        })
        frame = np.asarray(shot, dtype=np.uint8)  # BGRA
        return frame[:, :, :3].copy()

    def close(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None


def is_region_valid(region) -> bool:
    if not region:
        return False
    return (
        isinstance(region, dict)
        and region.get("width", 0) >= 8
        and region.get("height", 0) >= 8
    )
