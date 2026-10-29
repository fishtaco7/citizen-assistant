"""Synthetic scan crop used by --demo and the OCR self test."""
from __future__ import annotations

import numpy as np


def signature_crop(value: int = 7200, width: int = 190, height: int = 46) -> np.ndarray:
    """Render a dark HUD-ish strip containing a signature, like the game does."""
    import cv2

    img = np.full((height, width, 3), 22, dtype=np.uint8)
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 12, (height, width, 1), dtype=np.uint8)
    img = np.clip(img.astype(np.int16) + noise.astype(np.int16), 0, 255).astype(np.uint8)

    text = f"{value:,}"
    font = cv2.FONT_HERSHEY_DUPLEX
    scale = 1.05
    thickness = 2
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    x = max(6, (width - tw) // 2)
    y = (height + th) // 2
    cv2.putText(img, text, (x, y), font, scale, (240, 244, 248), thickness, cv2.LINE_AA)
    return img
