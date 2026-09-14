"""The overview: everything worth glancing at, without navigating."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.hub import Hub
from ..theme import RADIUS, SPACE, Palette
from ..widgets import Tile, Toast, heading, line_row


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


class _PhotoThumb(QLabel):
    """A recent photo that opens fullscreen when clicked."""

    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


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
        self._photo_items: list[dict] = []
        self._full: dict[str, bytes] = {}
        self.viewer = None

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

        # No passcode card: the sidebar shows the newest code on every page,
        # and the notifications page lists the last few.

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

        hub.callChanged.connect(lambda _c: self.refresh_calls())
        hub.connectionChanged.connect(lambda _c: self.refresh_all())

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_light)
        self.refresh_all()

    # Polling stops while the page is off screen.

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

    def refresh_all(self) -> None:
        self.refresh_calls()
        self.refresh_messages()
        self.refresh_photos()

    def refresh_light(self) -> None:
        """Times move on even when nothing changes."""
        self.refresh_calls()

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
        self._photo_items = list(items)

        for item in items:
            holder = _PhotoThumb()
            holder.setCursor(Qt.CursorShape.PointingHandCursor)
            holder.clicked.connect(lambda it=item: self.view(it))
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
        if self.viewer is not None and self.viewer.item.get("id") == media_id:
            self.viewer.set_pixmap(pixmap)

    # -- fullscreen, shared with the Photos page -------------------------------

    def view(self, item: dict) -> None:
        from .photos import PhotoViewer

        if item not in self._photo_items:
            return
        if self.viewer is not None:
            self.viewer.close()
        self.viewer = PhotoViewer(self, self._photo_items, self._photo_items.index(item))
        self.viewer.finished.connect(self._viewer_closed)
        self.viewer.showFullScreen()

    def _viewer_closed(self) -> None:
        self.viewer = None

    def thumbnail(self, item: dict) -> QPixmap:
        return self._thumbs.get(item.get("id", ""), QPixmap())

    def fetch_full(self, item: dict, on_data, on_error=None) -> None:
        from .photos import fetch_full

        fetch_full(self.hub, self._full, item, on_data, on_error)

    def save(self, item: dict) -> None:
        from .photos import save_media

        save_media(self, self.hub, self._full, item, self.toast, self.palette_tokens)

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
