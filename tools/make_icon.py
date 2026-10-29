"""Render the overlay icon to app.ico (shortcut + window icon).

    python -m tools.make_icon

Produces a multi-size ICO (16-256 px) with PNG-compressed entries, which
Windows 10/11 read fine. Re-run after changing ``app.main._tray_icon``.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.main import _tray_icon  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def _png(size: int) -> bytes:
    pix = _tray_icon(size).pixmap(size, size)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pix.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()
    return data


def main() -> int:
    app = QApplication(sys.argv[:1])  # noqa: F841 - QPixmap needs an app
    images = [(size, _png(size)) for size in SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    body = b""
    for size, data in images:
        # 0 means 256 in the ICO directory.
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
        body += data

    out = ROOT / "app.ico"
    out.write_bytes(header + entries + body)
    print(f"wrote {out} ({out.stat().st_size:,} bytes, "
          f"{len(images)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
