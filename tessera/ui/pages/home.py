"""The dashboard: everything worth glancing at, without navigating.

Phone Link and Sefirah both put the frequently-checked things on one surface —
what just arrived, who called, what is playing, and the handful of switches
people actually toggle. Detail lives on the dedicated pages; this is the
overview, so a tile shows a few rows and hands off rather than duplicating a
whole page.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...backends.mpris import MprisPlayer
from ...core.hub import Hub
from ..theme import RADIUS, SPACE, Palette
from ..widgets import Card, Tile, Toast, heading, line_row


def _ago(millis: int) -> str:
    if not millis:
        return ""
    moment = datetime.fromtimestamp(millis / 1000)
    now = datetime.now()
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if (now.date() - moment.date()).days == 1:
        return "Yesterday"
    return moment.strftime("%d %b")


class QuickToggle(QPushButton):
    """A chip-sized switch for the things people flip constantly."""

    def __init__(self, label: str, palette: Palette, parent=None):
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._palette = palette
        self.toggled.connect(self._restyle)
        self._restyle(False)

    def _restyle(self, on: bool) -> None:
        palette = self._palette
        if on:
            style = (
                f"background: {palette.accent}; color: {palette.accent_text};"
                f"border: 1px solid {palette.accent};"
            )
        else:
            style = (
                f"background: {palette.surface_alt}; color: {palette.muted};"
                f"border: 1px solid {palette.border};"
            )
        self.setStyleSheet(
            style + f"border-radius: {RADIUS['pill']}px; padding: 7px 16px; font-weight: 600;"
        )


class HomePage(QWidget):
    """Overview of the phone, with the common actions in reach."""

    openPage = Signal(str)      # ask the window to switch to a full page

    #: Tiles are refreshed together rather than each on its own timer.
    REFRESH_MS = 8000

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._player = MprisPlayer(self)
        self._service = ""
        self._thumbs: dict[str, QPixmap] = {}

        page = QVBoxLayout(self)
        page.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], 0)
        page.setSpacing(SPACE["lg"])
        page.addWidget(heading("Overview", "Your phone at a glance"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.addWidget(scroll, 1)

        host = QWidget()
        scroll.setWidget(host)
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, SPACE["md"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        outer.addWidget(self._build_quick_row())

        grid = QGridLayout()
        grid.setSpacing(SPACE["lg"])
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.media_tile = Tile("Now playing", palette=palette)
        self._build_media(self.media_tile)
        grid.addWidget(self.media_tile, 0, 0)

        self.calls_tile = Tile("Recent calls", "Open", palette=palette)
        self.calls_tile.actionClicked.connect(lambda: self.openPage.emit("Calls"))
        grid.addWidget(self.calls_tile, 0, 1)

        self.notifications_tile = Tile("Notifications", "Open", palette=palette)
        self.notifications_tile.actionClicked.connect(
            lambda: self.openPage.emit("Notifications")
        )
        grid.addWidget(self.notifications_tile, 1, 0)

        self.messages_tile = Tile("Messages", "Open", palette=palette)
        self.messages_tile.actionClicked.connect(lambda: self.openPage.emit("Messages"))
        grid.addWidget(self.messages_tile, 1, 1)

        self.photos_tile = Tile("Recent photos", "Open", palette=palette)
        self.photos_tile.actionClicked.connect(lambda: self.openPage.emit("Photos"))
        grid.addWidget(self.photos_tile, 2, 0, 1, 2)

        outer.addLayout(grid)
        outer.addStretch(1)

        self.toast = Toast(self)

        hub.notificationsChanged.connect(self.refresh_notifications)
        hub.callChanged.connect(lambda _c: self.refresh_calls())
        hub.connectionChanged.connect(lambda _c: self.refresh_all())
        hub.dndChanged.connect(self._sync_toggles)
        hub.mediaChanged.connect(lambda _m: self.refresh_media())

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_light)
        self.refresh_all()

    # Polling stops while the page is off screen. See the same pair in
    # pages/audio.py for why: a hidden page has nothing to keep up to date,
    # and a minimised window hides every page at once.

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._timer.start(self.REFRESH_MS)
        self.refresh_all()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()

    # -- quick actions -------------------------------------------------------

    def _build_quick_row(self) -> QWidget:
        card = Card(self, flat=True, padding=SPACE["md"])
        row = QHBoxLayout()
        row.setSpacing(SPACE["sm"])

        self.dnd_toggle = QuickToggle("Do Not Disturb", self.palette_tokens)
        self.dnd_toggle.clicked.connect(self._toggle_dnd)
        row.addWidget(self.dnd_toggle)

        self.clipboard_toggle = QuickToggle("Clipboard", self.palette_tokens)
        self.clipboard_toggle.setChecked(
            self.hub.config.features.clipboard
            and self.hub.config.clipboard.mode != "off"
        )
        self.clipboard_toggle.clicked.connect(self._toggle_clipboard)
        row.addWidget(self.clipboard_toggle)

        for label, page in (("Hotspot", "Hotspot"), ("Mirror screen", "Screen"),
                            ("Webcam", "Webcam")):
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _c=False, p=page: self.openPage.emit(p))
            row.addWidget(button)

        ring = QPushButton("Ring phone")
        ring.setCursor(Qt.CursorShape.PointingHandCursor)
        ring.clicked.connect(self._ring)
        row.addWidget(ring)

        row.addStretch(1)
        card.body().addLayout(row)
        return card

    def _toggle_dnd(self) -> None:
        wanted = "priority" if self.dnd_toggle.isChecked() else "off"
        self.hub.set_phone_dnd(wanted)

    def _toggle_clipboard(self) -> None:
        on = self.clipboard_toggle.isChecked()
        self.hub.config.features.clipboard = on
        self.hub.config.clipboard.mode = "two_way" if on else "off"
        self.hub.clipboard.set_mode(self.hub.config.clipboard.mode)
        self.hub.config.save()
        self.hub.apply_features()
        self.toast.show_message(
            "Clipboard sharing on" if on else "Clipboard sharing off",
            self.palette_tokens,
        )

    def _ring(self) -> None:
        try:
            self.hub.kdeconnect.ring()
            self.toast.show_message("Ringing your phone", self.palette_tokens)
        except Exception:
            self.toast.show_message(
                "Ringing needs KDE Connect paired with this phone",
                self.palette_tokens,
                "warning",
            )

    def _sync_toggles(self, mode: str = "") -> None:
        self.dnd_toggle.setChecked((mode or self.hub.phone_dnd) != "off")

    # -- media ---------------------------------------------------------------

    def _build_media(self, tile: Tile) -> None:
        self.track_label = QLabel("Nothing playing")
        self.track_label.setStyleSheet("font-weight: 650;")
        self.track_label.setWordWrap(True)
        tile.add_row(self.track_label)

        self.album_label = QLabel()
        self.album_label.setStyleSheet(
            f"color: {self.palette_tokens.muted}; font-size: 12px;"
        )
        tile.add_row(self.album_label)

        controls = QWidget()
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        for label, action in (("⏮", "Previous"), ("⏯", "PlayPause"), ("⏭", "Next")):
            button = QPushButton(label)
            button.setFixedWidth(52)
            button.clicked.connect(lambda _c=False, a=action: self._control(a))
            layout.addWidget(button)
        layout.addStretch(1)
        tile.add_row(controls)

    def _control(self, action: str) -> None:
        # Send through the companion when it is there; it controls whatever app
        # is playing, not merely whatever Bluetooth exposes.
        if self.hub.companion.connected and self.hub.media.get("canControl"):
            self.hub.media_command(
                {"PlayPause": "playpause", "Next": "next", "Previous": "previous"}[action]
            )
        else:
            try:
                self._player.control(self._service, action)
            except RuntimeError as exc:
                self.toast.show_message(str(exc)[:110], self.palette_tokens, "warning")
        QTimer.singleShot(600, self.refresh_media)

    def refresh_media(self) -> None:
        # The companion app is preferred: it reads MediaSession, so it works
        # with no Bluetooth connected and without moving the phone's audio.
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
        self._service = self._player.find_player(address, self.hub.bluetooth_name)
        track = self._player.track(self._service)
        self.track_label.setText(track.summary)
        self.album_label.setText(
            track.album
            or ("" if self._service else "Nothing playing on your phone")
        )

    # -- tiles ---------------------------------------------------------------

    def refresh_all(self) -> None:
        self.refresh_notifications()
        self.refresh_media()
        self.refresh_calls()
        self.refresh_messages()
        self.refresh_photos()
        self._sync_toggles()

    def refresh_light(self) -> None:
        """The cheap ones, on a timer; the rest only on a real change."""
        self.refresh_media()
        self._sync_toggles()

    def refresh_notifications(self) -> None:
        tile = self.notifications_tile
        tile.clear()
        notifications = self.hub.notifications[:4]
        tile.set_badge(
            str(len(self.hub.notifications)) if self.hub.notifications else "",
            "accent",
        )
        if not notifications:
            tile.add_placeholder("Nothing new.", self.palette_tokens)
            return
        for note in notifications:
            tile.add_row(
                line_row(note.app or "Notification", note.summary_line, self.palette_tokens)
            )

    def refresh_calls(self) -> None:
        tile = self.calls_tile
        if not self.hub.companion.connected:
            tile.clear()
            tile.add_placeholder("Connect your phone to see calls.", self.palette_tokens)
            return
        self.hub.companion.request(
            {"t": "calls_recent", "limit": 3}, lambda r: self._render_calls(r.get("items", []))
        )

    def _render_calls(self, items: list) -> None:
        tile = self.calls_tile
        tile.clear()
        missed = sum(1 for c in items if c.get("kind") == "missed")
        tile.set_badge(f"{missed} missed" if missed else "", "danger")
        if not items:
            tile.add_placeholder("No recent calls.", self.palette_tokens)
            return
        for call in items:
            name = call.get("name") or call.get("number") or "Unknown"
            detail = f"{call.get('kind', '').capitalize()} · {_ago(call.get('time', 0))}"
            tone = self.palette_tokens.danger if call.get("kind") == "missed" else ""
            tile.add_row(line_row(name, detail, self.palette_tokens, tone))

    def refresh_messages(self) -> None:
        tile = self.messages_tile
        if not self.hub.companion.connected:
            tile.clear()
            tile.add_placeholder("Connect your phone to see messages.", self.palette_tokens)
            return
        self.hub.companion.request(
            {"t": "sms_threads", "limit": 3},
            lambda r: self._render_messages(r.get("items", [])),
        )

    def _render_messages(self, items: list) -> None:
        tile = self.messages_tile
        tile.clear()
        unread = sum(1 for m in items if not m.get("read", True) and not m.get("outgoing"))
        tile.set_badge(f"{unread} unread" if unread else "", "accent")
        if not items:
            tile.add_placeholder("No messages.", self.palette_tokens)
            return
        for thread in items:
            name = thread.get("name") or thread.get("address") or "Unknown"
            body = " ".join((thread.get("body") or "").split())[:46]
            tile.add_row(line_row(name, body, self.palette_tokens))

    def refresh_photos(self) -> None:
        tile = self.photos_tile
        if not self.hub.companion.connected:
            tile.clear()
            tile.add_placeholder("Connect your phone to see photos.", self.palette_tokens)
            return
        self.hub.companion.request(
            {"t": "media_list", "limit": 6},
            lambda r: self._render_photos(r.get("items", [])),
        )

    def _render_photos(self, items: list) -> None:
        tile = self.photos_tile
        tile.clear()
        if not items:
            tile.add_placeholder("No photos.", self.palette_tokens)
            return

        strip = QWidget()
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["sm"])
        self._thumb_labels = {}

        for item in items:
            holder = QLabel()
            holder.setFixedSize(96, 96)
            holder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            holder.setStyleSheet(
                f"background: {self.palette_tokens.surface_hover};"
                f"border-radius: {RADIUS['md']}px;"
            )
            holder.setText("🎬" if item.get("video") else "🖼")
            layout.addWidget(holder)
            self._thumb_labels[item.get("id", "")] = holder
            self._request_thumb(item.get("id", ""))
        layout.addStretch(1)
        tile.add_row(strip)

    def _request_thumb(self, media_id: str) -> None:
        if not media_id:
            return
        cached = self._thumbs.get(media_id)
        if cached is not None:
            self._apply_thumb(media_id, cached)
            return
        self.hub.companion.request(
            {"t": "media_get", "id": media_id, "thumb": True},
            lambda reply, key=media_id: self._on_thumb(key, reply),
        )

    def _on_thumb(self, media_id: str, reply: dict) -> None:
        data = reply.get("data")
        if not isinstance(data, (bytes, bytearray)):
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(bytes(data)):
            return
        self._thumbs[media_id] = pixmap
        self._apply_thumb(media_id, pixmap)

    def _apply_thumb(self, media_id: str, pixmap: QPixmap) -> None:
        holder = getattr(self, "_thumb_labels", {}).get(media_id)
        if holder is None:
            return
        holder.setText("")
        holder.setPixmap(
            pixmap.scaled(
                96, 96,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
