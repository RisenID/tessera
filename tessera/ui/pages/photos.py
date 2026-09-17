"""Photo and video library."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImageReader, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
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


def fetch_full(hub, cache: dict, item: dict, on_data, on_error=None) -> None:
    """The full-size picture, cached in *cache* for the session."""
    media_id = item.get("id", "")
    if media_id in cache:
        on_data(cache[media_id])
        return
    if not hub.companion.connected:
        if on_error is not None:
            on_error("No phone connected.")
        return

    def arrived(reply: dict) -> None:
        data = reply.get("data")
        if isinstance(data, (bytes, bytearray)):
            cache[media_id] = bytes(data)
            on_data(cache[media_id])
        elif on_error is not None:
            on_error(str(reply.get("message") or "The phone could not send it."))

    hub.companion.request({"t": "media_get", "id": media_id, "thumb": False}, arrived)


def save_media(widget, hub, cache: dict, item: dict, toast, palette) -> None:
    """Ask where to save *item*, then download it there."""
    name = item.get("name") or "photo.jpg"
    target, _ = QFileDialog.getSaveFileName(widget, "Save photo", name)
    if not target:
        return

    def write(data: bytes) -> None:
        try:
            with open(target, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            toast.show_message(f"Could not save: {exc}", palette, "danger")
            return
        toast.show_message("Saved", palette, "success")

    fetch_full(hub, cache, item, write,
               lambda message: toast.show_message(message[:140], palette, "danger"))


def load_pixmap(data: bytes) -> QPixmap:
    """Decode *data*, turned upright by its EXIF orientation."""
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    image = reader.read()
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


def _date(item: dict) -> str:
    when = item.get("time", 0)
    return datetime.fromtimestamp(when / 1000).strftime("%d %b %Y") if when else "Unknown date"


def _month(item: dict) -> str:
    """The section a photo belongs to in the grid."""
    when = item.get("time", 0)
    if not when:
        return "Undated"
    moment = datetime.fromtimestamp(when / 1000)
    now = datetime.now()
    if moment.year == now.year and moment.month == now.month:
        return "This month"
    return moment.strftime("%B %Y")


try:  # pragma: no cover - depends on the Qt build
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    HAVE_VIDEO = True
except ImportError:  # pragma: no cover
    QAudioOutput = QMediaPlayer = QVideoWidget = None
    HAVE_VIDEO = False


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
        pixmap = load_pixmap(data)
        if pixmap.isNull():
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

        # Videos play here too, once the whole file has arrived: the phone
        # sends it as one frame, and refuses one too large for that.
        self._player = None
        self._audio = None
        self._buffer: QBuffer | None = None
        self.video = QVideoWidget() if HAVE_VIDEO else QLabel("Videos cannot be played by this build of Qt.")
        self.video.setVisible(False)
        layout.addWidget(self.video, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(SPACE["lg"], 0, SPACE["lg"], 0)
        self.previous_button = QPushButton("‹ Previous")
        self.previous_button.clicked.connect(lambda: self.step(-1))
        bar.addWidget(self.previous_button)
        self.caption = QLabel()
        bar.addWidget(self.caption, 1, Qt.AlignmentFlag.AlignCenter)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip("Copy the picture to the clipboard (Ctrl+C)")
        self.copy_button.clicked.connect(self.copy)
        bar.addWidget(self.copy_button)
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

        # Buttons would otherwise take the arrow keys for focus movement.
        for button in self.findChildren(QPushButton):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

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
        self._show_caption()
        self.copy_button.setVisible(not item.get("video"))
        self.previous_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(self.items) - 1)
        self._stop_video()
        # The thumbnail first, then the full picture once it arrives.
        self.set_pixmap(self.page.thumbnail(item))
        self.page.fetch_full(
            item,
            lambda data, key=item.get("id"): self._on_full(key, data),
            lambda message, key=item.get("id"): self._on_failed(key, message),
        )

    def _stop_video(self) -> None:
        if self._player is not None:
            self._player.stop()
            self._player.setSourceDevice(None)
        self._buffer = None
        self.video.setVisible(False)
        self.image.setVisible(True)

    def _play_video(self, data: bytes) -> None:
        if not HAVE_VIDEO:
            self.caption.setText("This build of Qt cannot play videos.")
            return
        if self._player is None:
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._player.setAudioOutput(self._audio)
            self._player.setVideoOutput(self.video)
            self._player.errorOccurred.connect(
                lambda _e, text: self.caption.setText(text or "The video could not be played.")
            )
        buffer = QBuffer(self)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._buffer = buffer
        self.image.setVisible(False)
        self.video.setVisible(True)
        self._player.setSourceDevice(buffer)
        self._player.play()

    def _show_caption(self) -> None:
        item = self.item
        kind = "Video" if item.get("video") else "Photo"
        self.caption.setText(
            f"{item.get('name') or kind} · {_date(item)} · {self.index + 1} of {len(self.items)}"
        )

    def _on_failed(self, media_id: str, message: str) -> None:
        if media_id == self.item.get("id"):
            self.caption.setText(message)

    def _on_full(self, media_id: str, data: bytes) -> None:
        if media_id != self.item.get("id"):
            return
        if self.item.get("video"):
            self._play_video(data)
            return
        pixmap = load_pixmap(data)
        if not pixmap.isNull():
            self.set_pixmap(pixmap)

    def copy(self) -> None:
        """The picture as shown, full size once it has arrived."""
        if self._pixmap.isNull() or self.item.get("video"):
            self.caption.setText("Nothing to copy yet")
            return
        QGuiApplication.clipboard().setPixmap(self._pixmap)
        self.caption.setText("Copied to the clipboard")
        QTimer.singleShot(1500, self._show_caption)

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

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_video()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Space and self._player is not None and self.video.isVisible():
            # Space pauses a video rather than skipping past it.
            if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._player.pause()
            else:
                self._player.play()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Space):
            self.step(1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.step(-1)
        elif key == Qt.Key.Key_Escape:
            self.close()
        elif event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
        else:
            super().keyPressEvent(event)


class PhotosPage(QWidget):
    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._tiles: dict[str, Thumb] = {}
        self._headers: list[QLabel] = []
        self._items: list[dict] = []
        self._full: dict[str, bytes] = {}
        self.viewer: PhotoViewer | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        header = QHBoxLayout()
        header.addWidget(heading("Photos", "Everything in your phone's gallery"), 1)
        self.shutter = QPushButton("Take a photo")
        self.shutter.setObjectName("Primary")
        self.shutter.setToolTip("Take a photo with the phone's camera and save it here")
        self.shutter.clicked.connect(self._take_photo)
        header.addWidget(self.shutter, 0, Qt.AlignmentFlag.AlignVCenter)
        self.facing = QComboBox()
        self.facing.addItem("Back camera", "back")
        self.facing.addItem("Front camera", "front")
        self.facing.setCurrentIndex(max(0, self.facing.findData(hub.config.capture.facing)))
        self.facing.currentIndexChanged.connect(self._facing_changed)
        header.addWidget(self.facing, 0, Qt.AlignmentFlag.AlignVCenter)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.load)
        header.addWidget(refresh, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(header_row(header))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        self.grid = QGridLayout(host)
        self.grid.setSpacing(SPACE["md"])
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self._columns = 0
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

    def _facing_changed(self, _index: int) -> None:
        self.hub.config.capture.facing = self.facing.currentData() or "back"
        self.hub.config.save()

    def _take_photo(self) -> None:
        self.shutter.setEnabled(False)

        def done(ok: bool, message: str, path: str = "") -> None:
            self.shutter.setEnabled(True)
            self.toast.show_message(message[:140], self.palette_tokens, "success" if ok else "danger")
            if ok and path:
                from ...backends.filetransfer import open_path
                from pathlib import Path

                open_path(Path(path))

        self.hub.take_photo(facing=self.facing.currentData() or "back", on_done=done)

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
        self._headers = []
        self._items = list(items)

        for item in items:
            self._tiles[item.get("id", "")] = Thumb(item, self.palette_tokens, self.save, self.view)
            self._request_thumb(item.get("id", ""))
        self._columns = 0
        self._place_tiles()

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

    def fetch_full(self, item: dict, on_data, on_error=None) -> None:
        fetch_full(self.hub, self._full, item, on_data, on_error)

    def save(self, item: dict) -> None:
        save_media(self, self.hub, self._full, item, self.toast, self.palette_tokens)

    def _place_tiles(self) -> None:
        """As many tiles per row as the width holds, in sections by month."""
        tiles = list(self._tiles.values())
        if not tiles:
            return
        margins = self.grid.contentsMargins()
        spacing = self.grid.spacing()
        width = self.scroll.viewport().width() - margins.left() - margins.right()
        columns = max(1, (width + spacing) // (tiles[0].width() + spacing))
        if columns == self._columns:
            return
        self._columns = columns
        for tile in tiles:
            self.grid.removeWidget(tile)
        for header in self._headers:
            self.grid.removeWidget(header)
            header.deleteLater()
        self._headers = []

        row, column, section = 0, 0, None
        for tile in tiles:
            month = _month(tile.item)
            if month != section:
                if column:
                    row += 1
                    column = 0
                header = QLabel(month)
                header.setObjectName("SectionTitle")
                self.grid.addWidget(header, row, 0, 1, columns, Qt.AlignmentFlag.AlignLeft)
                self._headers.append(header)
                section = month
                row += 1
            self.grid.addWidget(tile, row, column)
            column += 1
            if column == columns:
                row += 1
                column = 0

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_tiles()
        self.toast._reposition()
