"""Reusable UI pieces shared by every page."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import glyphs
from .theme import RADIUS, SPACE, Palette


#: Theme lookups and tints, by name: both are asked for on every status frame.
_THEMED: dict[tuple[str, ...], QIcon] = {}
_TINTED: dict[tuple[int, str, int], QIcon] = {}
_TINTABLE: dict[tuple[int, int], bool] = {}


def themed_icon(*names: str) -> QIcon:
    """The first of *names* this system can draw, symbolic for choice."""
    cached = _THEMED.get(names)
    if cached is not None:
        return cached
    icon = _themed_icon(names)
    _THEMED[names] = icon
    return icon


def _themed_icon(names: tuple[str, ...]) -> QIcon:
    for name in names:
        for candidate in (f"{name}-symbolic", name):
            icon = QIcon.fromTheme(candidate)
            if not icon.isNull():
                return icon
    for name in names:
        if glyphs.available(name):
            return glyphs.icon(name)
    return QIcon()


#: Above this share of opaque pixels an icon is filled rather than stroked.
_FILLED_COVERAGE = 0.30

#: How far apart the lightest and darkest parts can be, and how wide a spread
#: of hues is still one colour, before a filled icon counts as a picture.
_VALUE_SPREAD, _HUE_SPREAD = 0.45, 40


def _tintable(pixmap: QPixmap) -> bool:
    """Whether *pixmap* is line art or a flat shape rather than a picture."""
    image = pixmap.toImage()
    if image.isNull():
        return True

    opaque = 0
    lightest, darkest = 0.0, 1.0
    hues: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            if colour.alpha() < 160:
                continue
            opaque += 1
            value = colour.valueF()
            lightest, darkest = max(lightest, value), min(darkest, value)
            if colour.saturationF() > 0.25 and colour.hue() >= 0:
                hues.append(colour.hue())

    if not opaque:
        return True
    if opaque <= _FILLED_COVERAGE * image.width() * image.height():
        return True
    if lightest - darkest >= _VALUE_SPREAD:
        return False
    if not hues:
        return True
    # Hues are a circle, so their spread is the widest gap's complement.
    hues.sort()
    gaps = [b - a for a, b in zip(hues, hues[1:])] + [360 - hues[-1] + hues[0]]
    return 360 - max(gaps) < _HUE_SPREAD


def tinted_icon(icon: QIcon, colour: str, size: int) -> QIcon:
    """Recolour a single-colour icon to *colour*, or leave a picture alone."""
    if icon.isNull():
        return icon
    key = (icon.cacheKey(), colour, size)
    cached = _TINTED.get(key)
    if cached is not None:
        return cached
    tinted = _tinted_icon(icon, colour, size)
    _TINTED[key] = tinted
    return tinted


def _tinted_icon(icon: QIcon, colour: str, size: int) -> QIcon:
    pixmap = icon.pixmap(size, size)
    shape = (icon.cacheKey(), size)
    tintable = _TINTABLE.get(shape)
    if tintable is None:
        tintable = _TINTABLE[shape] = _tintable(pixmap)
    if not tintable:
        return icon
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(colour))
    painter.end()
    return QIcon(pixmap)


def ghost_button(label: str, icon_name: str = "") -> QPushButton:
    """A quiet header action, with the desktop's icon when it has one."""
    button = QPushButton(label)
    button.setObjectName("Ghost")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        from PySide6.QtGui import QIcon

        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            button.setIcon(icon)
    return button


def _scaled(factor: float) -> str:
    """A font size relative to the desktop's own, as a QSS value."""
    app = QGuiApplication.instance()
    base = app.font().pointSizeF() if app else 10.0
    return f"{(base if base > 0 else 10.0) * factor:.1f}pt"


class Card(QFrame):
    """A padded surface with a border. The basic unit of every page."""

    def __init__(self, parent: QWidget | None = None, flat: bool = False, padding: int = SPACE["lg"]):
        super().__init__(parent)
        self.setObjectName("CardFlat" if flat else "Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(SPACE["md"])

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget

    def shadow(self, palette: Palette) -> "Card":
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(24)
        effect.setOffset(0, 4)
        effect.setColor(QColor(0, 0, 0, 90 if palette.dark else 26))
        self.setGraphicsEffect(effect)
        return self


class Pill(QLabel):
    """A small status chip: connected, unread counts, DND state."""

    def __init__(self, text: str = "", tone: str = "muted", parent: QWidget | None = None):
        super().__init__(text, parent)
        self._tone = tone
        self._palette: Palette | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def apply(self, palette: Palette, tone: str | None = None) -> None:
        self._palette = palette
        if tone:
            self._tone = tone
        colour = {
            "accent": palette.accent,
            "success": palette.success,
            "warning": palette.warning,
            "danger": palette.danger,
            "muted": palette.muted,
        }.get(self._tone, palette.muted)
        background = QColor(colour)
        background.setAlpha(38)
        self.setStyleSheet(
            f"background: rgba({background.red()},{background.green()},{background.blue()},"
            f"{background.alpha()}); color: {colour}; border-radius: {RADIUS['pill']}px;"
            f"padding: 3px 10px; font-size: 12px; font-weight: 600;"
        )

    def set_state(self, text: str, tone: str) -> None:
        self.setText(text)
        if self._palette is not None:
            self.apply(self._palette, tone)


class Avatar(QLabel):
    """Circular initials badge, used where an app or contact icon is missing."""

    def __init__(self, text: str = "?", size: int = 40, parent: QWidget | None = None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self._initials = "".join(word[0] for word in text.split()[:2]).upper() or "?"
        self.update()

    def set_pixmap_rounded(self, pixmap: QPixmap) -> None:
        """Show *pixmap* clipped to a circle instead of initials."""
        rounded = QPixmap(self._size, self._size)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, self._size, self._size)
        painter.setClipPath(path)
        painter.drawPixmap(
            0, 0,
            pixmap.scaled(
                self._size, self._size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            ),
        )
        painter.end()
        self.setPixmap(rounded)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.pixmap() and not self.pixmap().isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Derive a stable colour from the initials so the same app keeps its hue.
        hue = (sum(ord(c) for c in self._initials) * 47) % 360
        painter.setBrush(QColor.fromHsv(hue, 90, 190 if self.palette().window().color().value() < 128 else 160))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, self._size, self._size)
        painter.setPen(QColor("#FFFFFF"))
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(max(9.0, self._size * 0.34))
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._initials)
        painter.end()


#: The pages' emoji, as the glyphs drawn in their place.
_EMPTY_GLYPHS = {
    "📞": "call-start",
    "💬": "mail-message",
    "🖼": "folder-pictures",
    "▦": "view-list-icons",
    "🔔": "notifications",
}


class EmptyState(QWidget):
    """Shown instead of a blank list, explaining what to do next."""

    actionClicked = Signal()

    def __init__(
        self,
        icon: str = "",
        title: str = "",
        message: str = "",
        action: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(SPACE["sm"])

        if icon:
            glyph = QLabel()
            glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            drawn = _EMPTY_GLYPHS.get(icon, "")
            if drawn and glyphs.available(drawn):
                # Drawn rather than an emoji: the emoji font costs a slow
                # fallback search on first use.
                glyph.setPixmap(glyphs.icon(drawn, 48).pixmap(48, 48))
            else:
                glyph.setText(icon)
                glyph.setStyleSheet("font-size: 44px;")
            layout.addWidget(glyph)

        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        body = QLabel(message)
        body.setObjectName("Muted")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        body.setMaximumWidth(420)
        layout.addWidget(body, alignment=Qt.AlignmentFlag.AlignCenter)

        if action:
            button = QPushButton(action)
            button.setObjectName("Primary")
            button.clicked.connect(self.actionClicked)
            layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)

        self.title_label = heading
        self.message_label = body

    def update_text(self, title: str, message: str) -> None:
        self.title_label.setText(title)
        self.message_label.setText(message)


class Toast(QLabel):
    """Transient confirmation, floating over the page it belongs to."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setVisible(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.setVisible(False))

    def show_message(self, text: str, palette: Palette, tone: str = "accent", msec: int = 2600) -> None:
        colour = {
            "accent": palette.accent,
            "success": palette.success,
            "danger": palette.danger,
            "warning": palette.warning,
        }.get(tone, palette.accent)
        self.setText(text)
        self.setStyleSheet(
            f"background: {colour}; color: {palette.accent_text}; border-radius: {RADIUS['md']}px;"
            f"padding: 10px 16px; font-weight: 600;"
        )
        self.adjustSize()
        self._reposition()
        self.setVisible(True)
        self.raise_()
        self._timer.start(msec)

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.move(
            max(SPACE["lg"], (parent.width() - self.width()) // 2),
            parent.height() - self.height() - SPACE["xl"],
        )


class Tile(Card):
    """A dashboard block: a titled card with an optional action in the corner."""

    actionClicked = Signal()

    def __init__(
        self,
        title: str,
        action: str = "",
        parent: QWidget | None = None,
        palette: "Palette | None" = None,
    ):
        super().__init__(parent, padding=SPACE["lg"])
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        label = QLabel(title)
        label.setObjectName("SectionTitle")
        header.addWidget(label)
        header.addStretch(1)

        self.badge = Pill("", "muted")
        if palette is not None:
            self.badge.apply(palette)
        self.badge.setVisible(False)
        header.addWidget(self.badge)

        if action:
            button = QPushButton(action)
            button.setObjectName("Ghost")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(self.actionClicked)
            header.addWidget(button)

        self.body().addLayout(header)

        #: Where a tile's rows go, so refreshing replaces only the content.
        self.content = QVBoxLayout()
        self.content.setSpacing(SPACE["sm"])
        self.body().addLayout(self.content)

    def set_badge(self, text: str, tone: str = "muted") -> None:
        self.badge.setVisible(bool(text))
        if text:
            self.badge.set_state(text, tone)

    def clear(self) -> None:
        while self.content.count():
            item = self.content.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()

    def add_row(self, widget: QWidget) -> None:
        self.content.addWidget(widget)

    def add_placeholder(self, text: str, palette: "Palette") -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {palette.muted}; font-size: 12px;")
        self.content.addWidget(label)


def line_row(title: str, detail: str, palette: "Palette", tone: str = "") -> QWidget:
    """A compact two-line entry, the dashboard's basic unit."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    top = QLabel(title)
    top.setStyleSheet("font-weight: 600; font-size: 13px;")
    top.setWordWrap(False)
    layout.addWidget(top)

    bottom = QLabel(detail)
    bottom.setStyleSheet(
        f"color: {tone or palette.muted}; font-size: 12px;"
    )
    bottom.setWordWrap(False)
    layout.addWidget(bottom)
    return container


def row(*widgets: QWidget, spacing: int = SPACE["sm"], stretch_last: bool = False) -> QWidget:
    """Lay widgets out horizontally in a transparent container."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget, 1 if (stretch_last and index == len(widgets) - 1) else 0)
    if not stretch_last:
        layout.addStretch(1)
    return container


def heading(title: str, subtitle: str = "") -> QWidget:
    """The standard page header."""
    container = QWidget()
    container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)

    label = QLabel(title)
    label.setObjectName("Title")
    layout.addWidget(label)

    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
    return container


def header_row(layout) -> QWidget:
    """Wrap a page's header layout so it keeps its own height."""
    holder = QWidget()
    holder.setLayout(layout)
    holder.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return holder


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


class OtpCard(Card):
    """A passcode, big and copyable in one click."""

    copied = Signal(str)

    def __init__(self, code: str, source: str, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent, flat=True, padding=SPACE["md"])
        self.code = code
        layout = QHBoxLayout()
        layout.setSpacing(SPACE["md"])

        text = QVBoxLayout()
        text.setSpacing(0)
        value = QLabel(code)
        value.setStyleSheet(
            f"font-size: {_scaled(2.1)}; font-weight: 700; letter-spacing: 4px;"
            f"color: {palette.text}; font-family: monospace;"
        )
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.addWidget(value)

        origin = QLabel(source)
        origin.setObjectName("Muted")
        text.addWidget(origin)
        layout.addLayout(text, 1)

        button = QPushButton("Copy")
        button.setObjectName("Copy")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self._copy)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.body().addLayout(layout)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.code)
        self.copied.emit(self.code)
