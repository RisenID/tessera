"""The application icon: the mosaic tile Linux installs."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

from ..core import platform

#: Every size a shortcut, taskbar or Alt-Tab can ask for.
SIZES = (16, 24, 32, 48, 64, 128, 256)

FILE = f"{platform.APP_ID}.svg"


def source() -> Path | None:
    """The SVG: bundled in a frozen build, or in the checkout."""
    bundle = getattr(sys, "_MEIPASS", None)
    base = Path(bundle) / "icons" if bundle else Path(__file__).resolve().parents[2] / "packaging" / "icons"
    path = base / FILE
    return path if path.is_file() else None


def tile(size: int) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer

    path = source()
    if path is None:
        # Installed on Linux: the icon theme has it.
        return QIcon.fromTheme(platform.APP_ID).pixmap(size, size)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(path)).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def icon() -> QIcon:
    result = QIcon()
    for size in SIZES:
        result.addPixmap(tile(size))
    return result
