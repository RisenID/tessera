"""The overview: everything worth glancing at, without navigating."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.hub import Hub
from ..theme import RADIUS, SPACE, Palette
from ..widgets import Card, OtpCard, Tile, Toast, heading, line_row


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


class HomePage(QWidget):
    """Overview of the phone, with the common actions in reach."""

    openPage = Signal(str)      # ask the window to switch to a full page

    #: Tiles are refreshed together rather than each on its own timer.
    REFRESH_MS = 8000

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
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

        # The newest passcode, copyable without leaving the overview. It used
        # to exist only on the notifications page.
        self.otp_card: OtpCard | None = None
        self.otp_slot = QVBoxLayout()
        self.otp_slot.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.otp_slot)

        grid = QGridLayout()
        grid.setSpacing(SPACE["lg"])
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # No notifications tile: the panel shows the live feed on every page,
        # and two copies of it drifted apart.
        self.messages_tile = Tile("Messages", "Open", palette=palette)
        self.messages_tile.actionClicked.connect(lambda: self.openPage.emit("Messages"))
        grid.addWidget(self.messages_tile, 0, 0)

        self.calls_tile = Tile("Recent calls", "Open", palette=palette)
        self.calls_tile.actionClicked.connect(lambda: self.openPage.emit("Calls"))
        grid.addWidget(self.calls_tile, 0, 1)

        self.photos_tile = Tile("Recent photos", "Open", palette=palette)
        self.photos_tile.actionClicked.connect(lambda: self.openPage.emit("Photos"))
        grid.addWidget(self.photos_tile, 1, 0, 1, 2)

        outer.addLayout(grid)
        outer.addStretch(1)

        self.toast = Toast(self)

        hub.otpArrived.connect(lambda _m, _n: self.refresh_otp())
        hub.callChanged.connect(lambda _c: self.refresh_calls())
        hub.connectionChanged.connect(lambda _c: self.refresh_all())

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

    # -- media ---------------------------------------------------------------

    # -- tiles ---------------------------------------------------------------

    def refresh_otp(self) -> None:
        """Show the newest passcode, or nothing when there is none."""
        if self.otp_card is not None:
            self.otp_slot.removeWidget(self.otp_card)
            self.otp_card.deleteLater()
            self.otp_card = None
        if not self.hub.config.features.otp:
            return
        codes = self.hub.recent_codes(limit=1)
        if not codes:
            return
        match, note = codes[0]
        self.otp_card = OtpCard(
            match.code, f"{note.app} · {note.time_text}", self.palette_tokens
        )
        self.otp_card.copied.connect(
            lambda _c: self.toast.show_message("Passcode copied", self.palette_tokens, "success")
        )
        self.otp_slot.addWidget(self.otp_card)

    def refresh_all(self) -> None:
        self.refresh_otp()
        self.refresh_calls()
        self.refresh_messages()
        self.refresh_photos()

    def refresh_light(self) -> None:
        """The cheap ones, on a timer; the rest only on a real change."""
        self.refresh_otp()

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
