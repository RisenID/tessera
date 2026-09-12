"""The device panel: the phone itself, down the left of the window.

Everything here is about the phone rather than the app -- what it is, how it is
doing, its switches, what it is playing and what has just arrived. Navigation
lives in the tab strip instead. See docs/DESIGN.md.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..backends.mpris import MprisPlayer
from ..core import otp, platform
from ..core.hub import Hub
from ..core.models import Notification
from .theme import SPACE, Palette
from .widgets import Avatar, Pill, divider, themed_icon, tinted_icon

#: How many notifications the panel shows. The rest are on the full page,
#: which the feed's own button opens.
FEED_LIMIT = 8

#: How far the rail can be dragged. The minimum is what the complication strip
#: and four switches need before they start wrapping into nonsense.
MIN_WIDTH, MAX_WIDTH = 280, 720

#: Widths beyond this add space rather than size: icons stop growing so a very
#: wide rail does not turn into a row of billboards.
SCALE_CEILING = 1.6

#: What the ringer button steps through, in order.
RINGER_CYCLE = ("normal", "vibrate", "silent")

#: Icon and fallback glyph for each ringer mode. One family, so the switch
#: reads as one control: Breeze's phone-vibrate is a full-colour device icon
#: with no line-art version, and it disappeared into a dark panel.
RINGER_ICONS = {
    "normal": ("audio-volume-high", "\N{BELL}"),
    "vibrate": ("audio-volume-low", "\N{MOBILE PHONE}"),
    "silent": ("audio-volume-muted", "\N{SPEAKER WITH CANCELLATION STROKE}"),
}


#: Every switch the panel can carry: icons (first one the theme has wins),
#: fallback glyph, tooltip, whether it latches, and the feature that governs
#: it. Which of them are actually shown is config.panel.tiles.
TILES: dict[str, tuple[tuple[str, ...], str, str, bool, str]] = {
    # The Do Not Disturb roundel, not a crossed-out bell: the bell reads as
    # "notifications off", which is a different switch.
    "dnd": (("process-stop", "notifications-disabled"), "\N{CIRCLED MINUS}",
            "Do Not Disturb", True, "dnd_sync"),
    "ringer": (("audio-volume-high",), "\N{BELL}", "Ringer", False, ""),
    "clipboard": (("edit-paste",), "\N{CLIPBOARD}", "Clipboard sharing",
                  True, "clipboard"),
    # A bell, not a handset: the ringer switch next to it is a speaker, and
    # Breeze's phone-ringing is a full-colour device icon that vanishes on a
    # dark panel.
    "ring": (("notifications", "audio-volume-high"), "\N{BELL}", "Ring phone",
             False, ""),
    "hotspot": (("network-wireless-hotspot",), "\N{ANTENNA WITH BARS}",
                "Start the phone's hotspot and join it", True, "hotspot"),
    # A camera, not camera-web -- which at this size reads as a briefcase.
    "camera": (("camera-photo", "camera-video", "camera-web"),
               "\N{CAMERA}", "Use a phone camera as a webcam", True, "webcam"),
    "mirror": (("smartphone",), "\N{MOBILE PHONE}", "Mirror the phone's screen",
               True, "screen"),
    # Governed by phone_audio, not bluetooth_audio: the link route works on
    # every platform, and the switch falls back to Bluetooth when it must.
    "audio": (("audio-headphones",), "\N{HEADPHONE}",
              "Play the phone's audio here", True, "phone_audio"),
}

#: Human names for the switches, for the list in Settings.
TILE_LABELS = {
    "dnd": "Do Not Disturb", "ringer": "Ringer mode", "clipboard": "Clipboard sharing",
    "ring": "Ring phone", "hotspot": "Hotspot", "camera": "Webcam",
    "mirror": "Mirror screen", "audio": "Play phone audio here",
}


def _stepped(prefix: str, fraction: float, suffix: str = "") -> str:
    """A Breeze status icon name for a level: network-wireless-60, battery-080.

    Breeze draws these in steps of twenty (signal) or ten (battery), which is
    how a status bar shows strength, so the desktop's own artwork does the
    work instead of glyphs we would have to draw ourselves.
    """
    step = 10 if prefix.startswith("battery") else 20
    # Half up, not Python's round(): half a bar is worth showing as the higher
    # step, and round(2.5) is 2.
    clamped = max(0.0, min(1.0, fraction))
    value = int(clamped * 100 / step + 0.5) * step
    number = f"{value:03d}" if prefix.startswith("battery") else str(value)
    return f"{prefix}-{number}{suffix}"


class Complication(QWidget):
    """One at-a-glance reading: an icon and a short value.

    Hides itself when there is nothing to say, so the strip only ever shows
    what the phone actually reported.
    """

    def __init__(self, glyph: str, palette: Palette,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._palette = palette
        self._glyph = glyph
        self._icon_px = 16
        self._font_px = 11
        self._last: tuple[tuple[str, ...], str, str, str] = ((), "", "", "")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self._icon = QLabel()
        self._icon.setFixedSize(16, 16)
        layout.addWidget(self._icon)

        self._value = QLabel()
        layout.addWidget(self._value)
        self.setVisible(False)

    def set_metrics(self, icon_px: int, font_px: int) -> None:
        """Resize with the panel, then redraw whatever is already showing."""
        self._icon_px, self._font_px = icon_px, font_px
        self._icon.setFixedSize(icon_px, icon_px)
        names, text, tooltip, tone = self._last
        if names or text:
            self.set(*names, text=text, tooltip=tooltip, tone=tone)

    def set(self, *icon_names: str, text: str = "", tooltip: str = "",
            tone: str = "") -> None:
        """Show a reading, or hide the whole thing when there is nothing."""
        self._last = (icon_names, text, tooltip, tone)
        if not icon_names and not text:
            self.setVisible(False)
            return
        colour = tone or self._palette.muted
        icon = tinted_icon(themed_icon(*icon_names), colour, self._icon_px)
        if icon.isNull():
            self._icon.setStyleSheet(
                f"color: {colour}; font-size: {max(9, self._font_px - 1)}px;"
            )
            self._icon.setText(self._glyph)
        else:
            self._icon.setText("")
            self._icon.setPixmap(icon.pixmap(self._icon_px, self._icon_px))
        self._value.setText(text)
        self._value.setStyleSheet(f"color: {colour}; font-size: {self._font_px}px;")
        self.setToolTip(tooltip or text)
        self.setVisible(True)


class PanelOtp(QFrame):
    """The newest passcode, copyable without leaving the rail.

    The full OtpCard is too wide for 300 pixels, so this is the same idea at
    the rail's scale: the digits, where they came from, and one button.
    """

    copied = Signal(str)

    def __init__(self, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FeedRow")
        self.code = ""
        self._palette = palette

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["sm"], SPACE["xs"], SPACE["xs"], SPACE["xs"])
        layout.setSpacing(SPACE["sm"])

        text = QVBoxLayout()
        text.setSpacing(0)
        self.value = QLabel()
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.addWidget(self.value)
        self.source = QLabel()
        self.source.setStyleSheet(f"color: {palette.muted}; font-size: 10px;")
        text.addWidget(self.source)
        layout.addLayout(text, 1)

        self.button = QPushButton("Copy")
        self.button.setObjectName("Copy")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self._copy)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setVisible(False)

    def show_code(self, code: str, source: str, size: int = 17) -> None:
        self.code = code
        self.value.setText(code)
        self.value.setStyleSheet(
            f"font-family: monospace; font-weight: 700; letter-spacing: 2px;"
            f"font-size: {size}px; color: {self._palette.text};"
        )
        self.source.setText(source)
        self.setVisible(True)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.code)
        self.copied.emit(self.code)


class FeedRow(QFrame):
    """A notification in the panel: app, time and one line of text.

    A frame rather than a plain widget: a scroll area's children are forced
    transparent so cards sit on the page, and this one needs its own surface.
    """

    opened = Signal()
    dismissed = Signal(str)
    codeCopied = Signal(str)

    def __init__(self, note: Notification, palette: Palette, icons=None,
                 avatar: int = 24, code: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.note = note
        self.setObjectName("FeedRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["xs"], SPACE["sm"])
        layout.setSpacing(SPACE["sm"])

        self.avatar = Avatar(note.app or "?", avatar)
        if icons is not None and note.package:
            pixmap = icons.get(note.package)
            if pixmap is not None:
                self.avatar.set_pixmap_rounded(pixmap)
        layout.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(SPACE["xs"])
        app = QLabel(note.app or note.package or "Notification")
        app.setStyleSheet("font-weight: 600; font-size: 11px;")
        top.addWidget(app, 1)
        when = QLabel(note.time_text)
        when.setStyleSheet(f"color: {palette.muted}; font-size: 10px;")
        top.addWidget(when)
        text.addLayout(top)

        self.body = QLabel()
        self.body.setStyleSheet("font-size: 12px;")
        self.body.setMinimumWidth(1)
        self.body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full = " ".join(note.summary_line.split()) or note.app
        text.addWidget(self.body)

        # A passcode is worth copying from wherever it is being read, which
        # includes the rail. See docs/DESIGN.md.
        if code:
            copy = QPushButton(f"Copy {code}")
            copy.setObjectName("Copy")
            copy.setCursor(Qt.CursorShape.PointingHandCursor)
            copy.clicked.connect(lambda: self._copy(code))
            text.addSpacing(SPACE["xs"])
            text.addWidget(copy, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addLayout(text, 1)

        if note.clearable:
            close = QPushButton("\N{MULTIPLICATION X}")
            close.setObjectName("Ghost")
            close.setFixedSize(18, 18)
            close.setToolTip("Dismiss")
            close.setCursor(Qt.CursorShape.PointingHandCursor)
            close.clicked.connect(lambda: self.dismissed.emit(note.id))
            layout.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.body.setText(
            self.body.fontMetrics().elidedText(
                self._full, Qt.TextElideMode.ElideRight, max(40, self.body.width())
            )
        )

    def _copy(self, code: str) -> None:
        QGuiApplication.clipboard().setText(code)
        self.codeCopied.emit(code)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.opened.emit()


class DevicePanel(QWidget):
    """The phone at a glance, with its switches and its newest alerts."""

    pageRequested = Signal(str)
    statusMessage = Signal(str)
    #: Switches whose sequence belongs to a page, which the window brings
    #: forward: see HotspotPage/AudioPage/ScreenPage.quick_toggle.
    hotspotRequested = Signal()
    audioRequested = Signal()
    mirrorRequested = Signal()

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._player = MprisPlayer(self)
        self._media_service = ""
        self._scale = 1.0
        self._rows: list[FeedRow] = []

        self.setObjectName("Sidebar")
        # Bounds, not a fixed width: the rail lives in a splitter now and the
        # user drags it to whatever suits them.
        self.setMinimumWidth(MIN_WIDTH)
        self.setMaximumWidth(MAX_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["sm"])

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_complications())
        layout.addWidget(self._build_link_row())
        layout.addWidget(self._build_tiles())
        layout.addWidget(self._build_battery())
        layout.addWidget(divider())
        layout.addWidget(self._build_feed(), 1)
        layout.addWidget(divider())
        layout.addWidget(self._build_now_playing())

        self.status = QLabel("")
        self.status.setObjectName("BrandSub")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        hub.connectionChanged.connect(lambda _c: self.refresh_header())
        hub.capabilitiesChanged.connect(lambda _c: self.refresh_header())
        hub.batteryChanged.connect(self._on_battery)
        hub.phoneStatusChanged.connect(lambda _s: self.refresh_readings())
        hub.bluetoothStreamingChanged.connect(lambda _s: self.refresh_readings())
        hub.phoneAudioChanged.connect(lambda _p: self.refresh_readings())
        hub.phoneAudioChanged.connect(lambda _p: self.sync_toggles())
        hub.dndChanged.connect(self.sync_toggles)
        hub.hotspotChanged.connect(lambda _j: self.sync_toggles())
        hub.cameraStarted.connect(lambda _d: self.sync_toggles())
        hub.cameraStopped.connect(self.sync_toggles)
        hub.cameraFailed.connect(lambda _m: self.sync_toggles())
        hub.mirrors.changed.connect(self.sync_toggles)
        hub.mediaChanged.connect(lambda _m: self.refresh_media())
        hub.notificationsChanged.connect(self.refresh_feed)
        hub.otpArrived.connect(lambda _m, _n: self.refresh_otp())
        hub.icons.iconReady.connect(self._on_icon)

        # Started in showEvent, not here: a window hidden to the tray -- which
        # is how "start minimised" launches -- has nothing to keep up to date.
        self._media_timer = QTimer(self)
        self._media_timer.timeout.connect(self.refresh_media)

        self.apply_tiles()
        self.refresh_header()
        self.refresh_readings()
        self.refresh_media()
        self.refresh_feed()
        self.refresh_otp()
        self.sync_toggles()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._media_timer.start(8000)
        self.refresh_media()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().hideEvent(event)
        self._media_timer.stop()

    # -- size ----------------------------------------------------------------

    def _rescale(self) -> None:
        """Match what is drawn to the width the rail has been dragged to.

        Quantised to one decimal place: re-laying out on every pixel of a drag
        would rebuild the feed dozens of times over for no visible change.
        """
        scale = min(SCALE_CEILING, round(self.width() / MIN_WIDTH, 1))
        if scale == self._scale:
            return
        self._scale = scale
        self._apply_metrics()

    def _px(self, base: int) -> int:
        return max(base, round(base * self._scale))

    def _apply_metrics(self) -> None:
        icon_px, font_px = self._px(16), self._px(11)
        for complication in self._complications:
            complication.set_metrics(icon_px, font_px)

        self.phone_tile.setFixedSize(self._px(46), self._px(58))
        self._paint_phone_tile()

        for button in self._tile_buttons + self._transport:
            button.setFixedSize(self._px(52), self._px(34))
            self._paint_tile(button)
        self._reflow_tiles()

        base = self.font().pointSizeF()
        if base > 0:
            brand = self.brand.font()
            brand.setPointSizeF(base * 1.2 * self._scale)
            brand.setBold(True)
            self.brand.setFont(brand)

        self.battery_bar.setFixedHeight(self._px(6))
        self.battery_detail.setStyleSheet(
            f"color: {self.palette_tokens.muted}; font-size: {font_px}px;"
        )
        self.refresh_feed()
        self.refresh_otp()

    # -- construction --------------------------------------------------------

    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["sm"])

        self.phone_tile = QLabel()
        self.phone_tile.setObjectName("PhoneTile")
        self.phone_tile.setFixedSize(46, 58)
        self.phone_tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._paint_phone_tile()
        layout.addWidget(self.phone_tile, 0, Qt.AlignmentFlag.AlignTop)

        names = QVBoxLayout()
        names.setSpacing(0)
        names.addStretch(1)
        self.brand = QLabel("No phone")
        self.brand.setObjectName("BrandName")
        self.brand.setWordWrap(True)
        names.addWidget(self.brand)
        self.device_label = QLabel("")
        self.device_label.setObjectName("BrandSub")
        names.addWidget(self.device_label)
        names.addStretch(1)
        layout.addLayout(names, 1)
        return header

    def _paint_phone_tile(self) -> None:
        size = max(24, round(self.phone_tile.height() * 0.48))
        icon = tinted_icon(themed_icon("smartphone"), self.palette_tokens.text, size)
        if icon.isNull():
            self.phone_tile.setText("\N{MOBILE PHONE}")
        else:
            self.phone_tile.setPixmap(icon.pixmap(size, size))

    def _build_complications(self) -> QWidget:
        """The little readings, in one line under the name."""
        strip = QWidget()
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["sm"])

        p = self.palette_tokens
        self.comp_bluetooth = Complication("BT", p)
        self.comp_wifi = Complication("wifi", p)
        self.comp_cell = Complication("cell", p)
        self.comp_ringer = Complication("vol", p)
        self.comp_battery = Complication("bat", p)
        self._complications = (
            self.comp_bluetooth, self.comp_wifi, self.comp_cell,
            self.comp_ringer, self.comp_battery,
        )
        for widget in self._complications:
            layout.addWidget(widget)
        layout.addStretch(1)
        return strip

    def _build_link_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["xs"])

        self.link_pill = Pill("Offline", "muted")
        self.link_pill.apply(self.palette_tokens)
        layout.addWidget(self.link_pill)

        self.reconnect_button = QPushButton()
        self.reconnect_button.setObjectName("Ghost")
        self.reconnect_button.setFixedSize(24, 24)
        self.reconnect_button.setToolTip("Look for the phone again")
        refresh = tinted_icon(
            themed_icon("view-refresh"), self.palette_tokens.muted, 14
        )
        if refresh.isNull():
            self.reconnect_button.setText("\N{CLOCKWISE OPEN CIRCLE ARROW}")
        else:
            self.reconnect_button.setIcon(refresh)
            self.reconnect_button.setIconSize(QSize(14, 14))
        self.reconnect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reconnect_button.clicked.connect(self._reconnect)
        layout.addWidget(self.reconnect_button)
        layout.addStretch(1)
        return row

    # -- the switches --------------------------------------------------------

    def _build_tiles(self) -> QWidget:
        """The phone's switches as squares, Phone Link's arrangement.

        A grid rather than a row: which ones are here is the user's choice, and
        they reflow to however many fit as the rail is dragged wider.
        """
        self.tile_host = QWidget()
        self.tile_grid = QGridLayout(self.tile_host)
        self.tile_grid.setContentsMargins(0, SPACE["xs"], 0, SPACE["xs"])
        self.tile_grid.setSpacing(SPACE["xs"])

        self.tiles: dict[str, QPushButton] = {}
        self._tile_buttons: list[QPushButton] = []
        for key, (icons, glyph, tip, checkable, _feature) in TILES.items():
            # Parented here rather than by the layout: a switch the user has
            # not chosen is never added to the grid, and showing a parentless
            # widget makes it a top-level window of its own.
            button = self._tile(icons, glyph, tip, checkable, self.tile_host)
            button.clicked.connect(
                lambda _checked=False, k=key: self._tile_clicked(k)
            )
            self.tiles[key] = button
            self._tile_buttons.append(button)

        self.clipboard_toggle = self.tiles["clipboard"]
        self.clipboard_toggle.setChecked(
            self.hub.config.features.clipboard
            and self.hub.config.clipboard.mode != "off"
        )
        # Named because the readings and the sync signals reach for them.
        self.dnd_toggle = self.tiles["dnd"]
        self.ringer_tile = self.tiles["ringer"]
        self.hotspot_tile = self.tiles["hotspot"]
        self.camera_tile = self.tiles["camera"]

        self.apply_tiles()
        return self.tile_host

    def _tile_clicked(self, key: str) -> None:
        {
            "dnd": self._toggle_dnd,
            "ringer": self._cycle_ringer,
            "clipboard": self._toggle_clipboard,
            "ring": self._ring,
            "hotspot": self._toggle_hotspot,
            "camera": self._toggle_camera,
            "mirror": self._toggle_mirror,
            "audio": self._toggle_audio,
        }[key]()

    def _tile(self, icons: tuple[str, ...], glyph: str, tip: str,
              checkable: bool = False,
              parent: QWidget | None = None) -> QPushButton:
        button = QPushButton(parent)
        button.setObjectName("Quick")
        button.setProperty("icons", icons)
        button.setProperty("glyph", glyph)
        button.setToolTip(tip)
        button.setCheckable(checkable)
        button.setFixedSize(self._px(52), self._px(34))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paint_tile(button)
        if checkable:
            # A lit switch is drawn in the accent colour, so its icon has to
            # change with it or it disappears into the fill.
            button.toggled.connect(lambda _on, b=button: self._paint_tile(b))
        return button

    def _paint_tile(self, button: QPushButton) -> None:
        names = [name for name in (button.property("icons") or ()) if name]
        colour = (
            self.palette_tokens.accent_text if button.isChecked()
            else self.palette_tokens.text
        )
        size = max(18, round(button.height() * 0.53))
        icon = tinted_icon(themed_icon(*names), colour, size)
        if icon.isNull():
            button.setText(button.property("glyph") or "")
        else:
            button.setText("")
            button.setIcon(icon)
            button.setIconSize(QSize(size, size))

    def _reflow_tiles(self) -> None:
        """As many squares per row as the panel is wide enough for.

        Which tiles are showing is read from the flag apply_tiles sets, not
        from isHidden(): before the window is first shown every child reports
        itself hidden, and filtering on that left half the tiles out of the
        grid for good.
        """
        buttons = [
            b for b in getattr(self, "_tile_buttons", [])
            if b.property("feature_off") is not True
        ]
        if not buttons:
            return
        span = buttons[0].width() + self.tile_grid.spacing()
        usable = self.width() - 2 * SPACE["md"]
        columns = max(3, min(len(buttons), usable // max(1, span)))
        for index in reversed(range(self.tile_grid.count())):
            self.tile_grid.takeAt(index)
        for index, button in enumerate(buttons):
            self.tile_grid.addWidget(button, index // columns, index % columns)
        self.tile_grid.setColumnStretch(columns, 1)

    def apply_tiles(self) -> None:
        """Show the switches the user chose, minus any that cannot work.

        A feature switched off in Settings hides its switch; so does one this
        platform cannot do at all, which is not the user's choice to make.
        """
        chosen = [key for key in self.hub.config.panel.tiles if key in TILES]
        features = self.hub.config.features
        for key, button in self.tiles.items():
            feature = TILES[key][4]
            on = key in chosen and (
                not feature
                or (platform.supported(feature)
                    and bool(getattr(features, feature, True)))
            )
            button.setProperty("feature_off", not on)
            button.setVisible(on)
        # Draw them in the order the user listed them.
        self._tile_buttons = [self.tiles[key] for key in chosen] + [
            button for key, button in self.tiles.items() if key not in chosen
        ]
        self._reflow_tiles()



    def _build_battery(self) -> QWidget:
        """Level, and what the phone says about it: charging, time, temperature."""
        self.battery_row = QWidget()
        layout = QVBoxLayout(self.battery_row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.battery_bar = QProgressBar()
        self.battery_bar.setRange(0, 100)
        self.battery_bar.setTextVisible(False)
        self.battery_bar.setFixedHeight(6)
        layout.addWidget(self.battery_bar)

        self.battery_detail = QLabel("")
        self.battery_detail.setStyleSheet(
            f"color: {self.palette_tokens.muted}; font-size: 11px;"
        )
        self.battery_detail.setWordWrap(True)
        layout.addWidget(self.battery_detail)

        self.battery_row.setVisible(False)
        return self.battery_row

    def _build_feed(self) -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["xs"])

        head = QHBoxLayout()
        head.setSpacing(SPACE["sm"])
        title = QLabel("Notifications")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        self.feed_count = Pill("", "accent")
        self.feed_count.apply(self.palette_tokens)
        self.feed_count.setVisible(False)
        head.addWidget(self.feed_count)
        head.addStretch(1)

        # The panel holds the newest few; this is how you reach the rest,
        # rather than scrolling a rail that grows without limit.
        self.open_all = QPushButton("Open")
        self.open_all.setObjectName("Ghost")
        self.open_all.setToolTip("Open the notifications page")
        self.open_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_all.clicked.connect(
            lambda: self.pageRequested.emit("Notifications")
        )
        head.addWidget(self.open_all)

        self.clear_all = QPushButton("Clear")
        self.clear_all.setObjectName("Ghost")
        self.clear_all.setToolTip("Dismiss every notification")
        self.clear_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_all.clicked.connect(self._dismiss_all)
        head.addWidget(self.clear_all)
        layout.addLayout(head)

        self.otp_strip = PanelOtp(self.palette_tokens)
        self.otp_strip.copied.connect(
            lambda code: self.statusMessage.emit(f"Copied {code}")
        )
        layout.addWidget(self.otp_strip)

        self.feed_scroll = QScrollArea()
        self.feed_scroll.setWidgetResizable(True)
        self.feed_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        host = QWidget()
        self.feed_layout = QVBoxLayout(host)
        self.feed_layout.setContentsMargins(0, 0, 0, 0)
        self.feed_layout.setSpacing(SPACE["xs"])
        self.feed_layout.addStretch(1)
        self.feed_scroll.setWidget(host)
        layout.addWidget(self.feed_scroll, 1)

        self.feed_more = QPushButton("")
        self.feed_more.setObjectName("Ghost")
        self.feed_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.feed_more.clicked.connect(
            lambda: self.pageRequested.emit("Notifications")
        )
        self.feed_more.setVisible(False)
        layout.addWidget(self.feed_more)

        self.feed_empty = QLabel("Nothing new.")
        self.feed_empty.setObjectName("BrandSub")
        layout.addWidget(self.feed_empty)
        return block

    def _build_now_playing(self) -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.track_label = QLabel("Nothing playing")
        self.track_label.setStyleSheet("font-weight: 650;")
        self.track_label.setWordWrap(True)
        layout.addWidget(self.track_label)

        self.album_label = QLabel()
        self.album_label.setObjectName("BrandSub")
        self.album_label.setWordWrap(True)
        layout.addWidget(self.album_label)

        controls = QHBoxLayout()
        controls.setSpacing(SPACE["xs"])
        self._transport: list[QPushButton] = []
        for icon_name, glyph, action, tip in (
            ("media-skip-backward", "\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}",
             "Previous", "Previous track"),
            ("media-playback-start", "\N{BLACK RIGHT-POINTING TRIANGLE}",
             "PlayPause", "Play or pause"),
            ("media-skip-forward", "\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}",
             "Next", "Next track"),
        ):
            button = self._tile((icon_name,), glyph, tip)
            button.clicked.connect(lambda _c=False, a=action: self._media_control(a))
            controls.addWidget(button)
            self._transport.append(button)
        controls.addStretch(1)
        layout.addLayout(controls)
        return block

    # -- actions -------------------------------------------------------------

    def _reconnect(self) -> None:
        self.reconnect_button.setEnabled(False)
        self.hub.reconnect()
        # The attempt is asynchronous; a permanently dead button reads as a hang.
        QTimer.singleShot(2500, lambda: self.reconnect_button.setEnabled(True))

    def _toggle_dnd(self) -> None:
        self.hub.set_phone_dnd("priority" if self.dnd_toggle.isChecked() else "off")

    def _cycle_ringer(self) -> None:
        """Step the phone's ringer: normal, vibrate, silent.

        All three rather than only the two asked for: a button that can put the
        phone on silent has to be able to take it off again.
        """
        current = self.hub.ringer
        following = (
            RINGER_CYCLE[(RINGER_CYCLE.index(current) + 1) % len(RINGER_CYCLE)]
            if current in RINGER_CYCLE else "vibrate"
        )
        self.hub.set_ringer(following)
        self.statusMessage.emit(f"Ringer: {following}")

    def _toggle_clipboard(self) -> None:
        on = self.clipboard_toggle.isChecked()
        self.hub.config.features.clipboard = on
        self.hub.config.clipboard.mode = "two_way" if on else "off"
        self.hub.clipboard.set_mode(self.hub.config.clipboard.mode)
        self.hub.config.save()
        self.hub.apply_features()
        self.statusMessage.emit(
            "Clipboard sharing on" if on else "Clipboard sharing off"
        )

    def _ring(self) -> None:
        # The companion app rings the phone itself, on the alarm stream so a
        # silenced phone still answers. KDE Connect is only the fallback now.
        self.hub.ring_phone(self.statusMessage.emit)

    def _toggle_hotspot(self) -> None:
        # The tile reports what the hotspot is doing, which only the page
        # knows, so it is set from hotspotChanged rather than by the click.
        self.hotspot_tile.setChecked(self.hub.hotspot_joined)
        self.hotspotRequested.emit()

    def _toggle_mirror(self) -> None:
        self.tiles["mirror"].setChecked(self.hub.mirrors.is_running("screen"))
        self.mirrorRequested.emit()

    def _toggle_audio(self) -> None:
        # Audio is only ever taken over on a click, never on a connection, so
        # this is the click that does it. See docs/DESIGN.md.
        self.tiles["audio"].setChecked(self._audio_playing())
        self.audioRequested.emit()

    def _toggle_camera(self) -> None:
        if self.hub.camera_running:
            self.hub.stop_camera()
            self.statusMessage.emit("Stopping the phone camera")
        else:
            self.hub.start_camera()
            self.statusMessage.emit("Starting the phone camera")
        self.sync_toggles()

    def _dismiss_all(self) -> None:
        for note in self.hub.notifications:
            if note.clearable:
                self.hub.dismiss(note.id)

    def _media_control(self, action: str) -> None:
        if self.hub.companion.connected and self.hub.companion.supports("media_control"):
            self.hub.media_command(action.lower())
            QTimer.singleShot(400, self.refresh_media)
            return
        try:
            self._player.control(self._media_service, action)
        except RuntimeError as exc:
            self.statusMessage.emit(str(exc)[:120])
        QTimer.singleShot(600, self.refresh_media)

    # -- refreshing ----------------------------------------------------------

    def refresh_header(self) -> None:
        name = self.hub.phone_name
        self.brand.setText(name or "No phone")
        model = self.hub.companion.phone.name or self.hub.bluetooth_name
        self.device_label.setText("" if model == name else model)
        source = self.hub.source
        if source == "companion":
            self.link_pill.set_state("Connected", "success")
        elif source == "kdeconnect":
            self.link_pill.set_state("KDE Connect", "warning")
        else:
            self.link_pill.set_state("Offline", "muted")
        self.refresh_readings()

    def refresh_readings(self) -> None:
        """The complication strip, the ringer tile and the battery block."""
        status = self.hub.phone_status
        p = self.palette_tokens

        if self.hub.phone_audio_active:
            # Over the link, not Bluetooth -- so say so: the radio is not
            # involved and the phone's own headphones are untouched.
            self.comp_bluetooth.set(
                "audio-headphones", "audio-volume-high",
                text="audio", tooltip="The phone's audio is playing here, over the link",
                tone=p.accent,
            )
        elif self.hub.bluetooth_streaming:
            self.comp_bluetooth.set(
                "network-bluetooth-activated", "network-bluetooth",
                text="audio", tooltip="Phone audio is playing here", tone=p.accent,
            )
        elif self.hub.bluetooth_connected:
            self.comp_bluetooth.set(
                "network-bluetooth-activated", "network-bluetooth",
                tooltip="Bluetooth connected",
            )
        else:
            self.comp_bluetooth.set()

        wifi = _sub(status, "wifi")
        if wifi.get("connected"):
            level, top = _level(wifi)
            self.comp_wifi.set(
                _stepped("network-wireless", level / top) if level >= 0
                else "network-wireless-connected",
                "network-wireless",
                tooltip="Wi-Fi" + _strength(level, top),
            )
        else:
            self.comp_wifi.set()

        cell = _sub(status, "cell")
        if cell:
            level, top = _level(cell)
            kind = str(cell.get("type", ""))
            suffix = {"5G": "-5g", "LTE": "-lte", "3G": "-umts", "2G": "-edge"}.get(kind, "")
            operator = str(cell.get("operator", ""))
            self.comp_cell.set(
                _stepped("network-mobile", level / top, suffix) if level >= 0 else "",
                _stepped("network-mobile", level / top) if level >= 0 else "",
                "network-mobile-available",
                text=kind or operator,
                tooltip=" \N{MIDDLE DOT} ".join(
                    part for part in (operator, kind) if part
                ) + _strength(level, top),
            )
        else:
            self.comp_cell.set()

        ringer = str(status.get("ringer", ""))
        volume = status.get("volume", -1)
        loud = isinstance(volume, int) and volume >= 0
        if ringer == "silent":
            self.comp_ringer.set("audio-volume-muted", text="silent",
                                 tooltip="Ringer silent", tone=p.warning)
        elif ringer == "vibrate":
            self.comp_ringer.set("audio-volume-low", "audio-volume-medium",
                                 tooltip="Ringer on vibrate")
        elif ringer == "normal":
            self.comp_ringer.set(
                "audio-volume-high",
                text=f"{volume}%" if loud else "",
                tooltip="Ringer on" + (
                    f" \N{MIDDLE DOT} media volume {volume}%" if loud else ""
                ),
            )
        else:
            self.comp_ringer.set()

        self._refresh_ringer_tile(ringer)
        self._refresh_battery(_sub(status, "battery"))

    def _refresh_ringer_tile(self, ringer: str) -> None:
        """The tile shows the mode the phone is in, and says what a click does."""
        icons, glyph = RINGER_ICONS.get(ringer, RINGER_ICONS["normal"])
        self.ringer_tile.setProperty("icons", (icons,))
        self.ringer_tile.setProperty("glyph", glyph)
        self._paint_tile(self.ringer_tile)
        if ringer in RINGER_CYCLE:
            following = RINGER_CYCLE[
                (RINGER_CYCLE.index(ringer) + 1) % len(RINGER_CYCLE)
            ]
            self.ringer_tile.setToolTip(
                f"Ringer {ringer} \N{EM DASH} click for {following}"
            )
        else:
            self.ringer_tile.setToolTip("Ringer \N{EM DASH} click to change")

    def _refresh_battery(self, battery: dict) -> None:
        level = battery.get("level", -1)
        if not isinstance(level, int) or level < 0:
            # KDE Connect reports a bare level through _on_battery instead, so
            # only a phone that has gone away clears the block.
            if not self.hub.connected:
                self.battery_row.setVisible(False)
                self.comp_battery.set()
            return
        self._show_battery(level, bool(battery.get("charging")))

        detail = []
        state = battery.get("status", "")
        source = battery.get("source", "")
        if state == "charging":
            detail.append(f"Charging{f' ({source.upper()})' if source else ''}")
        elif state == "full":
            detail.append("Full")
        elif state:
            detail.append(state.capitalize())
        to_full = battery.get("toFull")
        if isinstance(to_full, (int, float)) and to_full > 0:
            detail.append(f"{_duration(int(to_full))} to full")
        current = battery.get("current")
        if isinstance(current, (int, float)) and current:
            detail.append(f"{abs(current) / 1000:.2f} A")
        temperature = battery.get("temperature")
        if isinstance(temperature, (int, float)) and temperature:
            detail.append(f"{temperature:.0f} \N{DEGREE SIGN}C")
        health = battery.get("health", "")
        if health and health != "good":
            detail.append(f"Health: {health}")
        self.battery_detail.setText(" \N{MIDDLE DOT} ".join(detail))
        self.battery_detail.setVisible(bool(detail))

    def _on_battery(self, level: int, charging: bool) -> None:
        """A bare level, from whichever source reported it."""
        self._show_battery(level, charging)

    def _show_battery(self, level: int, charging: bool) -> None:
        p = self.palette_tokens
        tone = p.success if (charging or level > 30) else (
            p.warning if level > 15 else p.danger
        )
        self.battery_row.setVisible(True)
        self.battery_bar.setValue(max(0, min(100, level)))
        self.battery_bar.setStyleSheet(
            f"QProgressBar {{ background: {p.surface_hover};"
            f" border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {tone}; border-radius: 3px; }}"
        )
        self.comp_battery.set(
            _stepped("battery", level / 100, "-charging" if charging else ""),
            _stepped("battery", level / 100),
            "battery",
            text=f"{level}%",
            tooltip=f"Battery {level}%" + (", charging" if charging else ""),
            tone=tone,
        )

    def refresh_media(self) -> None:
        media = self.hub.media
        if media.get("title"):
            artist = media.get("artist", "")
            self.track_label.setText(
                f"{media['title']} \N{EM DASH} {artist}" if artist else media["title"]
            )
            detail = media.get("album", "") or media.get("app", "")
            state = "" if media.get("playing") else "  (paused)"
            self.album_label.setText(f"{detail}{state}")
            return

        address = self.hub.config.bluetooth.address
        self._media_service = self._player.find_player(address, self.hub.bluetooth_name)
        track = self._player.track(self._media_service)
        self.track_label.setText(track.summary)
        self.album_label.setText(track.album)

    def refresh_feed(self) -> None:
        notifications = self.hub.notifications
        while self.feed_layout.count() > 1:
            item = self.feed_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                # Unparent as well as delete: deleteLater leaves the row on
                # screen until the event loop gets round to it, and rebuilding
                # on a resize drew the old rows behind the new ones.
                widget.setParent(None)
                widget.deleteLater()

        self._rows = []
        want_codes = self.hub.config.features.otp
        for note in notifications[:FEED_LIMIT]:
            match = otp.find_code(
                f"{note.title} {note.text}".strip(), note.app
            ) if want_codes else None
            row = FeedRow(note, self.palette_tokens, self.hub.icons,
                          avatar=self._px(24),
                          code=match.code if match else "")
            row.opened.connect(lambda: self.pageRequested.emit("Notifications"))
            row.dismissed.connect(self.hub.dismiss)
            row.codeCopied.connect(
                lambda code: self.statusMessage.emit(f"Copied {code}")
            )
            self._rows.append(row)
            self.feed_layout.insertWidget(self.feed_layout.count() - 1, row)

        count = len(notifications)
        self.feed_count.setVisible(bool(count))
        if count:
            self.feed_count.set_state(str(count), "accent")
        self.feed_scroll.setVisible(bool(count))
        self.clear_all.setVisible(bool(count))
        self.open_all.setVisible(bool(count))
        hidden = max(0, count - FEED_LIMIT)
        self.feed_more.setVisible(bool(hidden))
        self.feed_more.setText(f"{hidden} more on the notifications page")
        self.feed_empty.setVisible(not count)
        self.feed_empty.setText(
            "Nothing new." if self.hub.connected else "No phone connected."
        )

    def refresh_otp(self) -> None:
        """The newest passcode, or nothing when there is none."""
        if not self.hub.config.features.otp:
            self.otp_strip.setVisible(False)
            return
        codes = self.hub.recent_codes(limit=1)
        if not codes:
            self.otp_strip.setVisible(False)
            return
        match, note = codes[0]
        self.otp_strip.show_code(
            match.code,
            f"{note.app} \N{MIDDLE DOT} {note.time_text}",
            size=self._px(17),
        )

    def _on_icon(self, package: str, pixmap) -> None:
        for row in self._rows:
            if row.note.package == package:
                row.avatar.set_pixmap_rounded(pixmap)

    def sync_toggles(self, mode: str = "") -> None:
        """Every latching switch reports the phone's state, not the last click."""
        self.dnd_toggle.setChecked((mode or self.hub.phone_dnd) != "off")
        self.hotspot_tile.setChecked(self.hub.hotspot_joined)
        self.camera_tile.setChecked(self.hub.camera_running)
        self.tiles["mirror"].setChecked(self.hub.mirrors.is_running("screen"))
        self.tiles["audio"].setChecked(self._audio_playing())

    def _audio_playing(self) -> bool:
        """Either route: over the link, or over Bluetooth."""
        return (
            self.hub.phone_audio_active
            or self.hub.phone_audio_pending
            or self.hub.bluetooth_streaming
        )

    def set_status(self, message: str) -> None:
        self.status.setText(message)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._rescale()
        self._reflow_tiles()


def _sub(status: dict, key: str) -> dict:
    value = status.get(key)
    return value if isinstance(value, dict) else {}


def _level(reading: dict) -> tuple[int, int]:
    """A reported signal level and its scale, or (-1, 4) when unknown."""
    level = reading.get("level")
    top = reading.get("max")
    top = top if isinstance(top, int) and top > 0 else 4
    if not isinstance(level, int) or level < 0:
        return -1, top
    return min(level, top), top


def _strength(level: int, top: int) -> str:
    return f" \N{MIDDLE DOT} signal {level}/{top}" if level >= 0 else ""


def _duration(millis: int) -> str:
    minutes = millis // 60000
    if minutes < 60:
        return f"{max(1, minutes)} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"
