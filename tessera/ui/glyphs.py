"""Icons for a platform whose icon theme is empty."""

from __future__ import annotations

import re

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

#: Stroke width in the 24-unit box every glyph is drawn in.
STROKE = 2.0
BOX = 24.0

_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="#000000" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
)

#: name -> the shapes inside the box. Aliases are resolved in `icon`.
SVG: dict[str, str] = {
    "go-home":
        '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>'
        '<path d="M10 20v-6h4v6"/>',
    "call-start":
        '<path d="M6 3h3l2 5-2.5 1.5a11 11 0 0 0 5 5L15 12l5 2v3a2 2 0 0 1-2.2 2'
        'A16 16 0 0 1 4 5.2A2 2 0 0 1 6 3z"/>',
    "mail-message":
        '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    "folder-pictures":
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<circle cx="8.5" cy="9.5" r="1.6"/><path d="M4 18l5-5 4 4 3-3 4 4"/>',
    "view-list-icons":
        '<rect x="3" y="3" width="7" height="7" rx="1.5"/>'
        '<rect x="14" y="3" width="7" height="7" rx="1.5"/>'
        '<rect x="3" y="14" width="7" height="7" rx="1.5"/>'
        '<rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "settings-configure":
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1'
        'M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
    "notifications":
        '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2.5H4.5z"/>'
        '<path d="M10 21a2.2 2.2 0 0 0 4 0"/>',
    "notifications-disabled":
        '<path d="M6 16V11a6 6 0 0 1 9.5-4.9"/><path d="M18 12v4l1.5 2.5H7"/>'
        '<path d="M10 21a2.2 2.2 0 0 0 4 0"/><path d="M3 3l18 18"/>',
    # A page with an arrow leaving it: sending a file, in the same line-art
    # vocabulary as the rest.
    "document-send":
        '<path d="M13 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11'
        'a1.5 1.5 0 0 0 1.5-1.5V9z"/>'
        '<path d="M13 3v6h6"/>'
        '<path d="M12 18v-6"/><path d="M9.5 14.5 12 12l2.5 2.5"/>',
    "process-stop":
        '<circle cx="12" cy="12" r="9"/><path d="M7.5 12h9"/>',
    "edit-paste":
        '<path d="M9 4h6v3H9z"/>'
        '<path d="M8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7'
        'a2 2 0 0 0-2-2h-2"/><path d="M8 12h8M8 16h5"/>',
    "smartphone":
        '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/>'
        '<path d="M10.8 5.2h2.4"/>',
    "camera-photo":
        '<path d="M3 8.5h3.5L8 6h8l1.5 2.5H21v10.5H3z"/>'
        '<circle cx="12" cy="13.5" r="3.2"/>',
    "camera-web":
        '<circle cx="12" cy="10" r="6"/><circle cx="12" cy="10" r="2"/>'
        '<path d="M6 20h12"/>',
    "audio-headphones":
        '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/>'
        '<rect x="2.5" y="13.5" width="4.5" height="7" rx="2"/>'
        '<rect x="17" y="13.5" width="4.5" height="7" rx="2"/>',
    "audio-volume-high":
        '<path d="M4 9.5h3.5L12 5v14L7.5 14.5H4z"/>'
        '<path d="M15.5 9a4.5 4.5 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
    "audio-volume-low":
        '<path d="M4 9.5h3.5L12 5v14L7.5 14.5H4z"/>'
        '<path d="M15.5 9a4.5 4.5 0 0 1 0 6"/>',
    "audio-volume-muted":
        '<path d="M4 9.5h3.5L12 5v14L7.5 14.5H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
    "view-refresh":
        '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v5h-5"/>',
    "media-playback-start": '<path d="M8 5l11 7-11 7z" fill="#000000"/>',
    "media-playback-pause":
        '<path d="M9 5v14M15 5v14" stroke-width="3"/>',
    "media-skip-forward":
        '<path d="M6 5l9 7-9 7z" fill="#000000"/><path d="M18 5v14"/>',
    "media-skip-backward":
        '<path d="M18 5l-9 7 9 7z" fill="#000000"/><path d="M6 5v14"/>',
    "network-bluetooth-activated":
        '<path d="M7 7l10 10-5 5V2l5 5L7 17"/>',
    "network-wireless-hotspot":
        '<circle cx="12" cy="16" r="2"/>'
        '<path d="M8.5 12.5a5 5 0 0 1 7 0M5.5 9.5a9 9 0 0 1 13 0"/>',
    "network-mobile-available":
        '<path d="M4 20h16"/><path d="M8 20v-4M12 20v-8M16 20v-12"/>',
    "dialog-information":
        '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.6v.8"/>',
}

#: Names that mean the same picture here.
ALIASES: dict[str, str] = {
    "smartphone-symbolic": "smartphone",
    "camera-photo-symbolic": "camera-photo",
    "camera-video": "camera-photo",
    "phone-ringing": "notifications",
    "audio-volume-medium": "audio-volume-low",
    "network-wireless": "network-wireless-100",
    "network-wireless-connected": "network-wireless-100",
    "network-bluetooth": "network-bluetooth-activated",
    "battery": "battery-100",
    "applications-all": "view-list-icons",
    "network-mobile": "network-mobile-available",
    "notification-disabled": "notifications-disabled",
}

_WIRELESS = re.compile(r"^network-wireless-(\d+)(?:-.*)?$")
_MOBILE = re.compile(r"^network-mobile-(\d+)(?:-(\w+))?$")
_BATTERY = re.compile(r"^battery-(\d+)(-charging)?$")


def _render(body: str, size: int) -> QPixmap:
    """Rasterise one of the SVG bodies above at *size* pixels."""
    from PySide6.QtSvg import QSvgRenderer      # imported late: rarely needed

    document = f"{_HEAD}{body}</svg>".encode("utf-8")
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(document).render(painter)
    painter.end()
    return pixmap


def _pen(painter: QPainter, width: float, size: int) -> None:
    pen = QPen(QColor("#000000"))
    pen.setWidthF(width * size / BOX)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)


def _bars(level: int, size: int, label: str = "") -> QPixmap:
    """Signal strength as four rising bars, the filled ones solid."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / BOX
    _pen(painter, 1.6, size)
    filled = QColor("#000000")
    for index in range(4):
        height = (4.0 + index * 3.6) * scale
        x = (4.0 + index * 4.6) * scale
        width = 3.0 * scale
        rect = QRectF(x, size - height - 2 * scale, width, height)
        if index < level:
            painter.setBrush(filled)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 0.8 * scale, 0.8 * scale)
    if label:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        font = painter.font()
        font.setPixelSize(max(6, int(7 * scale)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 0, size, 9 * scale),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            label,
        )
    painter.end()
    return pixmap


def _wifi(level: int, size: int) -> QPixmap:
    """Wi-Fi as arcs over a dot, so it cannot be mistaken for the cell bars."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / BOX
    _pen(painter, 1.9, size)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # Three arcs and the dot: four steps, like the bars.
    for index, radius in enumerate((9.5, 6.5, 3.5)):
        if index >= max(0, level - 1):
            painter.setOpacity(0.28)
        rect = QRectF(
            (12 - radius) * scale, (15 - radius) * scale,
            2 * radius * scale, 2 * radius * scale,
        )
        painter.drawArc(rect, 35 * 16, 110 * 16)
    painter.setOpacity(1.0 if level > 0 else 0.28)
    painter.setBrush(QColor("#000000"))
    painter.drawEllipse(QRectF(10.6 * scale, 13.6 * scale, 2.8 * scale, 2.8 * scale))
    painter.end()
    return pixmap


def _battery(percent: int, charging: bool, size: int) -> QPixmap:
    """A battery outline filled in proportion, with a bolt when charging."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / BOX
    _pen(painter, 1.8, size)
    body = QRectF(2.5 * scale, 7.5 * scale, 17 * scale, 9 * scale)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(body, 1.6 * scale, 1.6 * scale)
    painter.setBrush(QColor("#000000"))
    painter.drawRoundedRect(
        QRectF(20 * scale, 10 * scale, 1.8 * scale, 4 * scale),
        0.6 * scale, 0.6 * scale,
    )
    inner = body.adjusted(1.6 * scale, 1.6 * scale, -1.6 * scale, -1.6 * scale)
    share = max(0.0, min(1.0, percent / 100)) * inner.width()
    if share > 0:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(
            QRectF(inner.left(), inner.top(), share, inner.height()),
            0.8 * scale, 0.8 * scale,
        )
    if charging:
        # A bolt punched out of the fill, so it reads at either end of the bar.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        points = [
            (12.6, 8.2), (9.2, 12.6), (11.4, 12.6), (10.6, 16.0),
            (14.2, 11.4), (12.0, 11.4),
        ]
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF

        painter.drawPolygon(
            QPolygonF([QPointF(x * scale, y * scale) for x, y in points])
        )
    painter.end()
    return pixmap


def available(name: str) -> bool:
    """Whether this module can draw *name*."""
    resolved = ALIASES.get(name, name)
    return bool(
        resolved in SVG
        or _WIRELESS.match(resolved)
        or _MOBILE.match(resolved)
        or _BATTERY.match(resolved)
    )


def icon(name: str, size: int = 32) -> QIcon:
    """Our own drawing of *name*, or a null icon when there is none."""
    resolved = ALIASES.get(name, name)

    if resolved in SVG:
        pixmap = _render(SVG[resolved], size)
    elif match := _WIRELESS.match(resolved):
        pixmap = _wifi(round(int(match.group(1)) / 25), size)
    elif match := _MOBILE.match(resolved):
        suffix = (match.group(2) or "").upper().replace("UMTS", "3G")
        pixmap = _bars(
            round(int(match.group(1)) / 25), size,
            {"5G": "5G", "LTE": "LTE", "3G": "3G", "EDGE": "E"}.get(suffix, ""),
        )
    elif match := _BATTERY.match(resolved):
        pixmap = _battery(int(match.group(1)), bool(match.group(2)), size)
    else:
        return QIcon()

    result = QIcon(pixmap)
    # A couple of sizes, so Qt scales down rather than up for tab strips.
    for extra in (16, 24, 48):
        if extra != size:
            result.addPixmap(_sized(resolved, extra))
    return result


def _sized(resolved: str, size: int) -> QPixmap:
    if resolved in SVG:
        return _render(SVG[resolved], size)
    if match := _WIRELESS.match(resolved):
        return _wifi(round(int(match.group(1)) / 25), size)
    if match := _MOBILE.match(resolved):
        suffix = (match.group(2) or "").upper().replace("UMTS", "3G")
        return _bars(
            round(int(match.group(1)) / 25), size,
            {"5G": "5G", "LTE": "LTE", "3G": "3G", "EDGE": "E"}.get(suffix, ""),
        )
    if match := _BATTERY.match(resolved):
        return _battery(int(match.group(1)), bool(match.group(2)), size)
    return QPixmap(QSize(size, size))
