"""The application icon: the phone glyph on an accent square."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from . import glyphs

#: Every size a shortcut, taskbar or Alt-Tab can ask for.
SIZES = (16, 24, 32, 48, 64, 128, 256)

BACKGROUND = "#3DAEE9"
FOREGROUND = "#FFFFFF"


def tile(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(BACKGROUND))
    radius = size * 0.22
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    inner = round(size * 0.62)
    mark = glyphs.icon("smartphone", max(32, inner)).pixmap(inner, inner)
    tint = QPainter(mark)
    tint.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    tint.fillRect(mark.rect(), QColor(FOREGROUND))
    tint.end()
    painter.drawPixmap(round((size - inner) / 2), round((size - inner) / 2), mark)
    painter.end()
    return pixmap


def icon() -> QIcon:
    result = QIcon()
    for size in SIZES:
        result.addPixmap(tile(size))
    return result
