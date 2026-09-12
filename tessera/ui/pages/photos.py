"""Photo and video library."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
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
from ..widgets import Card, EmptyState, Toast, heading


class Thumb(Card):
    """One item in the grid, filled in once its thumbnail arrives."""

    def __init__(self, item: dict, palette: Palette, on_open, parent=None):
        super().__init__(parent, flat=True, padding=SPACE["sm"])
        self.item = item
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

        when = item.get("time", 0)
        caption = QLabel(
            datetime.fromtimestamp(when / 1000).strftime("%d %b %Y") if when else "Unknown date"
        )
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
        self.image.setPixmap(
            pixmap.scaled(
                174, 150,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class PhotosPage(QWidget):
    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._tiles: dict[str, Thumb] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        header = QHBoxLayout()
        header.addWidget(heading("Photos", "Everything in your phone's gallery"), 1)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.load)
        header.addWidget(refresh, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(header)

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

        columns = 5
        for index, item in enumerate(items):
            tile = Thumb(item, self.palette_tokens, self._save)
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

    def _save(self, item: dict) -> None:
        name = item.get("name") or "photo.jpg"
        target, _ = QFileDialog.getSaveFileName(self, "Save photo", name)
        if not target:
            return
        self.hub.companion.request(
            {"t": "media_get", "id": item.get("id", ""), "thumb": False},
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
