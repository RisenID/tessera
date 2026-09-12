"""The device panel: the phone itself, down the left of the window.

Everything here is about the phone rather than the app -- what it is, how it is
doing, its switches, what it is playing and what has just arrived. Navigation
lives in the tab strip instead. See docs/DESIGN.md.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
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
from ..core.hub import Hub
from ..core.models import Notification
from .theme import SPACE, Palette
from .widgets import Avatar, Pill, divider, themed_icon, tinted_icon

#: How many notifications the panel shows before the full page is needed.
FEED_LIMIT = 12


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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self._icon = QLabel()
        self._icon.setFixedSize(16, 16)
        layout.addWidget(self._icon)

        self._value = QLabel()
        self._value.setStyleSheet(f"color: {palette.muted}; font-size: 11px;")
        layout.addWidget(self._value)
        self.setVisible(False)

    def set(self, *icon_names: str, text: str = "", tooltip: str = "",
            tone: str = "") -> None:
        """Show a reading, or hide the whole thing when there is nothing."""
        if not icon_names and not text:
            self.setVisible(False)
            return
        colour = tone or self._palette.muted
        icon = tinted_icon(themed_icon(*icon_names), colour, 16)
        if icon.isNull():
            self._icon.setStyleSheet(f"color: {colour}; font-size: 10px;")
            self._icon.setText(self._glyph)
        else:
            self._icon.setPixmap(icon.pixmap(16, 16))
        self._value.setText(text)
        self._value.setStyleSheet(
            f"color: {tone or self._palette.muted}; font-size: 11px;"
        )
        self.setToolTip(tooltip or text)
        self.setVisible(True)


class FeedRow(QFrame):
    """A notification in the panel: app, time and one line of text.

    A frame rather than a plain widget: a scroll area's children are forced
    transparent so cards sit on the page, and this one needs its own surface.
    """

    opened = Signal()
    dismissed = Signal(str)

    def __init__(self, note: Notification, palette: Palette, icons=None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.note = note
        self.setObjectName("FeedRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["xs"], SPACE["sm"])
        layout.setSpacing(SPACE["sm"])

        self.avatar = Avatar(note.app or "?", 24)
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
        layout.addLayout(text, 1)

        if note.clearable:
            close = QPushButton("✕")
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

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.opened.emit()


class DevicePanel(QWidget):
    """The phone at a glance, with its switches and its newest alerts."""

    pageRequested = Signal(str)
    statusMessage = Signal(str)

    WIDTH = 320

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._player = MprisPlayer(self)
        self._media_service = ""

        self.setObjectName("Sidebar")
        self.setFixedWidth(self.WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["sm"])

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_complications())
        layout.addWidget(self._build_link_row())
        layout.addWidget(self._build_quick_row())
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
        hub.dndChanged.connect(self.sync_toggles)
        hub.mediaChanged.connect(lambda _m: self.refresh_media())
        hub.notificationsChanged.connect(self.refresh_feed)
        hub.icons.iconReady.connect(self._on_icon)

        # Started in showEvent, not here: a window hidden to the tray -- which
        # is how "start minimised" launches -- has nothing to keep up to date.
        self._media_timer = QTimer(self)
        self._media_timer.timeout.connect(self.refresh_media)

        self.refresh_header()
        self.refresh_readings()
        self.refresh_media()
        self.refresh_feed()
        self.sync_toggles()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._media_timer.start(8000)
        self.refresh_media()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().hideEvent(event)
        self._media_timer.stop()

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
        icon = themed_icon("smartphone")
        if icon.isNull():
            self.phone_tile.setText("📱")
        else:
            self.phone_tile.setPixmap(icon.pixmap(28, 28))
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
        for widget in (
            self.comp_bluetooth, self.comp_wifi, self.comp_cell,
            self.comp_ringer, self.comp_battery,
        ):
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
            self.reconnect_button.setText("⟳")
        else:
            self.reconnect_button.setIcon(refresh)
            self.reconnect_button.setIconSize(QSize(14, 14))
        self.reconnect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reconnect_button.clicked.connect(self._reconnect)
        layout.addWidget(self.reconnect_button)
        layout.addStretch(1)
        return row

    def _build_quick_row(self) -> QWidget:
        """The phone's switches as a row of squares, Phone Link's arrangement."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, SPACE["xs"], 0, SPACE["xs"])
        layout.setSpacing(SPACE["xs"])

        self.dnd_toggle = self._square("notifications-disabled", "🌙",
                                       "Do Not Disturb", checkable=True)
        self.dnd_toggle.clicked.connect(self._toggle_dnd)
        layout.addWidget(self.dnd_toggle)

        self.clipboard_toggle = self._square("edit-paste", "📋",
                                             "Clipboard sharing", checkable=True)
        self.clipboard_toggle.setChecked(
            self.hub.config.features.clipboard
            and self.hub.config.clipboard.mode != "off"
        )
        self.clipboard_toggle.clicked.connect(self._toggle_clipboard)
        layout.addWidget(self.clipboard_toggle)

        # A speaker, not a handset: the vibrate complication above already
        # uses the phone glyph, and two of them side by side read as one thing.
        ring = self._square("audio-volume-high", "🔔", "Ring phone",
                            fallback="phone-ringing")
        ring.clicked.connect(self._ring)
        layout.addWidget(ring)

        self.stream_button = self._square("audio-headphones", "🎧",
                                          "Play phone audio here")
        self.stream_button.clicked.connect(lambda: self.pageRequested.emit("Audio"))
        layout.addWidget(self.stream_button)
        layout.addStretch(1)
        return row

    def _square(self, icon_name: str, glyph: str, tip: str,
                checkable: bool = False, fallback: str = "") -> QPushButton:
        button = QPushButton()
        button.setObjectName("Quick")
        button.setProperty("icons", (icon_name, fallback))
        button.setProperty("glyph", glyph)
        button.setToolTip(tip)
        button.setCheckable(checkable)
        button.setFixedSize(52, 34)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paint_square(button)
        if checkable:
            # A lit switch is drawn in the accent colour, so its icon has to
            # change with it or it disappears into the fill.
            button.toggled.connect(lambda _on, b=button: self._paint_square(b))
        return button

    def _paint_square(self, button: QPushButton) -> None:
        names = [name for name in button.property("icons") or () if name]
        colour = (
            self.palette_tokens.accent_text if button.isChecked()
            else self.palette_tokens.text
        )
        icon = tinted_icon(themed_icon(*names), colour, 18)
        if icon.isNull():
            button.setText(button.property("glyph") or "")
        else:
            button.setIcon(icon)
            button.setIconSize(QSize(18, 18))

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
        head.setSpacing(SPACE["xs"])
        title = QLabel("Notifications")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        self.feed_count = Pill("", "accent")
        self.feed_count.apply(self.palette_tokens)
        self.feed_count.setVisible(False)
        head.addWidget(self.feed_count)
        head.addStretch(1)
        self.clear_all = QPushButton("Clear all")
        self.clear_all.setObjectName("Ghost")
        self.clear_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_all.clicked.connect(self._dismiss_all)
        head.addWidget(self.clear_all)
        layout.addLayout(head)

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
        for icon_name, glyph, action, tip in (
            ("media-skip-backward", "⏮", "Previous", "Previous track"),
            ("media-playback-start", "⏯", "PlayPause", "Play or pause"),
            ("media-skip-forward", "⏭", "Next", "Next track"),
        ):
            button = self._square(icon_name, glyph, tip)
            button.clicked.connect(lambda _c=False, a=action: self._media_control(a))
            controls.addWidget(button)
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
        try:
            self.hub.kdeconnect.ring()
            self.statusMessage.emit("Ringing your phone")
        except Exception:
            self.statusMessage.emit(
                "Ringing needs KDE Connect paired with this phone"
            )

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
        """The complication strip and the battery block."""
        status = self.hub.phone_status
        p = self.palette_tokens

        if self.hub.bluetooth_streaming:
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
                tooltip=" · ".join(
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
            self.comp_ringer.set("phone-vibrate", "audio-volume-low",
                                 tooltip="Ringer on vibrate")
        elif ringer == "normal":
            self.comp_ringer.set(
                "audio-volume-high",
                text=f"{volume}%" if loud else "",
                tooltip="Ringer on" + (f" · media volume {volume}%" if loud else ""),
            )
        else:
            self.comp_ringer.set()

        self._refresh_battery(_sub(status, "battery"))

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
            detail.append(f"{temperature:.0f} °C")
        health = battery.get("health", "")
        if health and health != "good":
            detail.append(f"Health: {health}")
        self.battery_detail.setText(" · ".join(detail))
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
                f"{media['title']} — {artist}" if artist else media["title"]
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
                widget.deleteLater()

        self._rows: list[FeedRow] = []
        for note in notifications[:FEED_LIMIT]:
            row = FeedRow(note, self.palette_tokens, self.hub.icons)
            row.opened.connect(lambda: self.pageRequested.emit("Notifications"))
            row.dismissed.connect(self.hub.dismiss)
            self._rows.append(row)
            self.feed_layout.insertWidget(self.feed_layout.count() - 1, row)

        count = len(notifications)
        self.feed_count.setVisible(bool(count))
        if count:
            self.feed_count.set_state(str(count), "accent")
        self.feed_scroll.setVisible(bool(count))
        self.clear_all.setVisible(bool(count))
        self.feed_empty.setVisible(not count)
        self.feed_empty.setText(
            "Nothing new." if self.hub.connected else "No phone connected."
        )

    def _on_icon(self, package: str, pixmap) -> None:
        for row in getattr(self, "_rows", []):
            if row.note.package == package:
                row.avatar.set_pixmap_rounded(pixmap)

    def sync_toggles(self, mode: str = "") -> None:
        self.dnd_toggle.setChecked((mode or self.hub.phone_dnd) != "off")

    def set_status(self, message: str) -> None:
        self.status.setText(message)


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
    return f" · signal {level}/{top}" if level >= 0 else ""


def _duration(millis: int) -> str:
    minutes = millis // 60000
    if minutes < 60:
        return f"{max(1, minutes)} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"
