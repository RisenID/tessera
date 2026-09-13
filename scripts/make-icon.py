#!/usr/bin/env python3
"""Draw the application icon, at every size Windows asks for."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRectF, Qt                          # noqa: E402
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap     # noqa: E402
from PySide6.QtWidgets import QApplication                     # noqa: E402

from tessera.ui import glyphs                                  # noqa: E402

#: Every size a Windows shortcut, taskbar or Alt-Tab can ask for.
SIZES = (16, 24, 32, 48, 64, 128, 256)

#: The accent Tessera uses when it has no desktop to ask.
BACKGROUND = "#3DAEE9"
FOREGROUND = "#FFFFFF"


def tile(size: int) -> QPixmap:
    """A rounded accent square with the phone glyph on it."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(BACKGROUND))
    radius = size * 0.22
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    inner = round(size * 0.62)
    phone = glyphs.icon("smartphone", max(32, inner)).pixmap(inner, inner)
    tinted = QPixmap(phone)
    mark = QPainter(tinted)
    mark.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    mark.fillRect(tinted.rect(), QColor(FOREGROUND))
    mark.end()
    painter.drawPixmap(
        round((size - inner) / 2), round((size - inner) / 2), tinted
    )
    painter.end()
    return pixmap


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "packaging/windows/tessera.ico")
    QApplication([])
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(tile(size))
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.suffix.lower() == ".ico":
        # Qt writes .ico through the plugin; where it is missing, fall back to
        # the largest PNG so the build still has something to embed.
        largest = tile(256)
        if not largest.save(str(target), "ICO"):
            fallback = target.with_suffix(".png")
            largest.save(str(fallback), "PNG")
            print(f"no ICO support in this Qt build; wrote {fallback}")
            return 0
    else:
        tile(512).save(str(target))
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
