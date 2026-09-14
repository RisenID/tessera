"""Photo and video library."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
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
from ..widgets import Card, EmptyState, Toast, header_row, heading


def _date(item: dict) -> str:
    when = item.get("time", 0)
    return datetime.fromtimestamp(when / 1000).strftime("%d %b %Y") if when else "Unknown date"


class Thumb(Card):
    """One item in the grid, filled in once its thumbnail arrives."""

    def __init__(self, item: dict, palette: Palette, on_open, on_view=None, parent=None):
        super().__init__(parent, flat=True, padding=SPACE["sm"])
        self.item = item
        self.thumbnail = QPixmap()
        self._on_view = on_view
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(190, 210)

        layout = QVBoxLayout()
        layout.setSpacing(SPACE["xs"])

        self.image = QLabel()
        self.image.setFixedSize(174, 150)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setStyleSheet(
            f"background: {palette.surface_hover}; border-radius: {RADIUS['sm']}px;"
        )
        self.image.setText("🎬" if item.get("video") else "🖼")
        layout.addWidget(self.image)

        caption = QLabel(_date(item))
        caption.setObjectName("Muted")
        caption.setStyleSheet(f"color: {palette.muted}; font-size: 11px;")
        layout.addWidget(caption)

        open_button = QPushButton("Save")
        open_button.setObjectName("Ghost")
        open_button.clicked.connect(lambda: on_open(item))
        layout.addWidget(open_button)

        self.body().addLayout(layout)

    def set_image(self, data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        self.thumbnail = pixmap
        self.image.setPixmap(
            pixmap.scaled(
                174, 150,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._on_view is not None:
            self._on_view(self.item)
        super().mouseReleaseEvent(event)


class PhotoViewer(QDialog):
    """Fullscreen view of the library. Arrow keys move, Esc closes."""

    def __init__(self, page: "PhotosPage", items: list, index: int):
        super().__init__(page.window())
        self.page = page
        self.items = items
        self.index = index
        self._pixmap = QPixmap()
        self.setWindowTitle("Photos")
        self.setStyleSheet("QDialog { background: #000; } QLabel { color: #ddd; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, SPACE["md"])

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(1, 1)
        layout.addWidget(self.image, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(SPACE["lg"], 0, SPACE["lg"], 0)
        self.previous_button = QPushButton("‹ Previous")
        self.previous_button.clicked.connect(lambda: self.step(-1))
        bar.addWidget(self.previous_button)
        self.caption = QLabel()
        bar.addWidget(self.caption, 1, Qt.AlignmentFlag.AlignCenter)
        save = QPushButton("Save")
        save.clicked.connect(lambda: page.save(self.items[self.index]))
        bar.addWidget(save)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bar.addWidget(close)
        self.next_button = QPushButton("Next ›")
        self.next_button.clicked.connect(lambda: self.step(1))
        bar.addWidget(self.next_button)
        layout.addLayout(bar)

        self.show_item()

    @property
    def item(self) -> dict:
        return self.items[self.index]

    def step(self, delta: int) -> None:
        index = self.index + delta
        if 0 <= index < len(self.items):
            self.index = index
            self.show_item()

    def show_item(self) -> None:
        item = self.item
        kind = "Video" if item.get("video") else "Photo"
        self.caption.setText(
            f"{item.get('name') or kind} · {_date(item)} · {self.index + 1} of {len(self.items)}"
        )
        self.previous_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(self.items) - 1)
        # The thumbnail first, then the full picture once it arrives.
        self.set_pixmap(self.page.thumbnail(item))
        if not item.get("video"):
            self.page.fetch_full(item, lambda data, key=item.get("id"): self._on_full(key, data))

    def _on_full(self, media_id: str, data: bytes) -> None:
        if media_id != self.item.get("id"):
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.set_pixmap(pixmap)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap.isNull():
            self.image.setText("Loading…" if not self.item.get("video") else "🎬")
            return
        self.image.setPixmap(
            self._pixmap.scaled(
                self.image.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rescale()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Space):
            self.step(1)
        elif key == Qt.Key.Key_Left:
            self.step(-1)
        elif key == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)


class PhotosPage(QWidget):
    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._tiles: dict[str, Thumb] = {}
        self._items: list[dict] = []
        self._full: dict[str, bytes] = {}
        self.viewer: PhotoViewer | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        header = QHBoxLayout()
        header.addWidget(heading("Photos", "Everything in your phone's gallery"), 1)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.load)
        header.addWidget(refresh, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(header_row(header))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        host = QWidget()
        self.grid = QGridLayout(host)
        self.grid.setSpacing(SPACE["md"])
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(host)
        outer.addWidget(self.scroll, 1)

        self.empty = EmptyState(
            "🖼",
            "No photos yet",
            "Connect the companion app and grant it photo access to browse your gallery here.",
            "Load photos",
        )
        self.empty.actionClicked.connect(self.load)
        outer.addWidget(self.empty)

        self.toast = Toast(self)
        hub.connectionChanged.connect(lambda _c: self.load())
        self.load()

    def load(self) -> None:
        if not self.hub.companion.connected:
            self._render([])
            return
        self.hub.companion.request(
            {"t": "media_list", "limit": 120},
            lambda reply: self._render(reply.get("items", [])),
        )

    def _render(self, items: list) -> None:
        while self.grid.count():
            entry = self.grid.takeAt(0)
            widget = entry.widget() if entry else None
            if widget is not None:
                widget.deleteLater()
        self._tiles.clear()
        self._items = list(items)

        columns = 5
        for index, item in enumerate(items):
            tile = Thumb(item, self.palette_tokens, self.save, self.view)
            self.grid.addWidget(tile, index // columns, index % columns)
            self._tiles[item.get("id", "")] = tile
            self._request_thumb(item.get("id", ""))

        self.scroll.setVisible(bool(items))
        self.empty.setVisible(not items)
        if not items and self.hub.companion.connected:
            self.empty.update_text(
                "Nothing to show",
                "The phone returned no photos. Check that photo access is granted "
                "in the companion app.",
            )

    def _request_thumb(self, media_id: str) -> None:
        if not media_id:
            return
        self.hub.companion.request(
            {"t": "media_get", "id": media_id, "thumb": True},
            lambda reply, key=media_id: self._on_thumb(key, reply),
        )

    def _on_thumb(self, media_id: str, reply: dict) -> None:
        tile = self._tiles.get(media_id)
        data = reply.get("data")
        if tile is not None and isinstance(data, (bytes, bytearray)):
            tile.set_image(bytes(data))
            if self.viewer is not None and self.viewer.item.get("id") == media_id:
                self.viewer.set_pixmap(tile.thumbnail)

    # -- fullscreen ------------------------------------------------------------

    def view(self, item: dict) -> None:
        if item not in self._items:
            return
        if self.viewer is not None:
            self.viewer.close()
        self.viewer = PhotoViewer(self, self._items, self._items.index(item))
        self.viewer.finished.connect(self._viewer_closed)
        self.viewer.showFullScreen()

    def _viewer_closed(self) -> None:
        self.viewer = None

    def thumbnail(self, item: dict) -> QPixmap:
        tile = self._tiles.get(item.get("id", ""))
        return tile.thumbnail if tile is not None else QPixmap()

    def fetch_full(self, item: dict, on_data) -> None:
        """The full-size picture, cached for the session."""
        media_id = item.get("id", "")
        if media_id in self._full:
            on_data(self._full[media_id])
            return
        if not self.hub.companion.connected:
            return

        def arrived(reply: dict) -> None:
            data = reply.get("data")
            if isinstance(data, (bytes, bytearray)):
                self._full[media_id] = bytes(data)
                on_data(self._full[media_id])

        self.hub.companion.request(
            {"t": "media_get", "id": media_id, "thumb": False}, arrived
        )

    # -- saving ----------------------------------------------------------------

    def save(self, item: dict) -> None:
        name = item.get("name") or "photo.jpg"
        target, _ = QFileDialog.getSaveFileName(self, "Save photo", name)
        if not target:
            return
        media_id = item.get("id", "")
        if media_id in self._full:
            self._write(target, {"data": self._full[media_id]})
            return
        self.hub.companion.request(
            {"t": "media_get", "id": media_id, "thumb": False},
            lambda reply: self._write(target, reply),
        )

    def _write(self, target: str, reply: dict) -> None:
        data = reply.get("data")
        if not isinstance(data, (bytes, bytearray)):
            self.toast.show_message("Could not download that item", self.palette_tokens, "danger")
            return
        try:
            with open(target, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            self.toast.show_message(f"Could not save: {exc}", self.palette_tokens, "danger")
            return
        self.toast.show_message("Saved", self.palette_tokens, "success")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast._reposition()
