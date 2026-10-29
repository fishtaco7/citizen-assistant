"""Signature OCR: tight crop -> normalise -> RapidOCR recognition only.

The user calibrates a rectangle that contains nothing but the signature, so
text *detection* is unnecessary and actively harmful (it splits the number
into boxes and drops digits). Recognition-only mode on a normalised strip
reads 4-5 digit signatures reliably.

    raw "7,200"   -> 7200   (0.82)
    raw "10,710"  -> 10710  (0.85)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np

# Glyphs OCR commonly confuses with digits in an all-caps HUD font.
_SUBST = str.maketrans({
    "O": "0", "o": "0", "D": "0", "Q": "0",
    "l": "1", "I": "1", "|": "1", "!": "1", "]": "1", "[": "1",
    "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2",
    "g": "9", "q": "9", "A": "4", "T": "7",
})

_TOKEN = re.compile(r"[0-9][0-9\s.,'’]*")

REC_HEIGHT = 48
MAX_REC_WIDTH = 640


@dataclass
class OCRResult:
    value: int | None = None
    raw: str = ""
    confidence: float = 0.0
    variants_tried: int = 0
    crop: np.ndarray | None = None       # BGR crop, for the debug preview
    candidates: list[tuple[int, float]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.value is not None


class SignatureOCR:
    def __init__(self, min_value: int = 2500, max_value: int = 60000):
        self.min_value = min_value
        self.max_value = max_value
        self._engine = None

    # ------------------------------------------------------------------
    def _ensure_engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            # Recognition only: no text detection, no angle classifier.
            self._engine = RapidOCR(use_text_det=False, use_angle_cls=False)
        return self._engine

    # ------------------------------------------------------------------
    @staticmethod
    def _bright_threshold(gray: np.ndarray) -> int:
        mean, std = float(gray.mean()), float(gray.std())
        return int(max(70, min(235, mean + 1.1 * std)))

    @classmethod
    def _tight(cls, gray: np.ndarray) -> np.ndarray:
        """Trim empty border so the recogniser sees the number, not the HUD."""
        thr = cls._bright_threshold(gray)
        mask = gray > thr
        if mask.sum() < max(12, gray.size * 0.01):
            return gray
        cols = np.where(mask.any(axis=0))[0]
        rows = np.where(mask.any(axis=1))[0]
        if not len(cols) or not len(rows):
            return gray
        pad_x = max(2, int((cols[-1] - cols[0]) * 0.06))
        pad_y = max(2, int((rows[-1] - rows[0]) * 0.10))
        x0 = max(0, int(cols[0]) - pad_x)
        x1 = min(gray.shape[1], int(cols[-1]) + pad_x + 1)
        y0 = max(0, int(rows[0]) - pad_y)
        y1 = min(gray.shape[0], int(rows[-1]) + pad_y + 1)
        return gray[y0:y1, x0:x1]

    @classmethod
    def _normalise(cls, gray: np.ndarray, binary: bool = False) -> np.ndarray | None:
        if gray.shape[0] < 5 or gray.shape[1] < 5:
            return None
        if binary:
            gray = cv2.threshold(gray, 0, 255,
                                 cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        scale = REC_HEIGHT / gray.shape[0]
        if gray.shape[1] * scale > MAX_REC_WIDTH:
            scale = MAX_REC_WIDTH / gray.shape[1]
        resized = cv2.resize(gray, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_CUBIC)
        if resized.shape[1] < 8:
            return None
        return cv2.cvtColor(resized, cv2.COLOR_GRAY2BGR)

    def _variants(self, crop: np.ndarray) -> list[np.ndarray]:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        tightened = self._tight(gray)
        out = []
        for binary in (False, True):
            image = self._normalise(tightened, binary=binary)
            if image is not None:
                out.append(image)
        return out

    # ------------------------------------------------------------------
    def _run(self, image: np.ndarray) -> list[tuple[str, float]]:
        engine = self._ensure_engine()
        try:
            # rapidocr-onnxruntime reads the det/cls flags from the constructor;
            # passing them per call trips an AttributeError in its kwargs branch
            # (it expects a detector that recognition-only mode never builds).
            out = engine(image)
        except TypeError:
            # Newer rapidocr releases only accept the flags per call.
            try:
                out = engine(image, use_det=False, use_cls=False)
            except Exception:
                return []
        except Exception:
            return []
        result = out[0] if isinstance(out, tuple) else out
        tokens: list[tuple[str, float]] = []
        for item in result or []:
            try:
                if len(item) >= 3:          # [box, text, score]
                    text, score = item[1], item[2]
                elif len(item) == 2:        # recognition-only: [text, score]
                    text, score = item[0], item[1]
                else:
                    continue
                tokens.append((str(text), float(score)))
            except (IndexError, TypeError, ValueError):
                continue
        return tokens

    def _numbers(self, tokens: list[tuple[str, float]]) -> list[tuple[int, float]]:
        found: list[tuple[int, float]] = []
        for text, score in tokens:
            cleaned = text.strip()
            if not cleaned:
                continue
            if any(ch.isdigit() for ch in cleaned):
                cleaned = cleaned.translate(_SUBST)
            for tok in _TOKEN.findall(cleaned):
                digits = re.sub(r"[^0-9]", "", tok)
                if not 3 <= len(digits) <= 6:
                    continue
                value = int(digits)
                if self.min_value <= value <= self.max_value:
                    found.append((value, score))
        return found

    # ------------------------------------------------------------------
    def read(self, crop: np.ndarray, keep_crop: bool = True) -> OCRResult:
        result = OCRResult(crop=crop.copy() if (keep_crop and crop is not None) else None)
        if crop is None or crop.size == 0:
            return result

        best: tuple[int, float] | None = None
        seen: list[tuple[int, float]] = []

        for idx, variant in enumerate(self._variants(crop), start=1):
            tokens = self._run(variant)
            result.raw = " | ".join(t for t, _ in tokens)[:160]
            result.variants_tried = idx
            numbers = self._numbers(tokens)
            seen.extend(numbers)
            for value, score in numbers:
                if best is None or (score, -value) > (best[1], -best[0]):
                    best = (value, score)
            if best is not None and best[1] >= 0.90:
                break

        result.candidates = sorted(seen, key=lambda t: -t[1])[:6]
        if best is not None:
            result.value, result.confidence = best
        return result
