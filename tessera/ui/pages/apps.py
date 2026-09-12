"""The phone's apps, each opening into its own desktop window."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...backends import mirror
from ...core.hub import Hub
from ...core import packages
from ..theme import SPACE, Palette
from ..widgets import Avatar, Card, EmptyState, Toast, header_row, heading

#: Tile width including its gap, used to decide how many fit per row.
TILE = 150


class AppTile(Card):
    """One launchable app."""

    def __init__(self, package: str, name: str, on_open, palette: Palette,
                 icons=None, parent=None):
        super().__init__(parent, flat=True, padding=SPACE["md"])
        self.package = package
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout()
        layout.setSpacing(SPACE["sm"])
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.avatar = Avatar(name, 48)
        if icons is not None and package:
            pixmap = icons.get(package)
            if pixmap is not None:
                self.avatar.set_pixmap_rounded(pixmap)
        layout.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignCenter)

        label = QLabel(name)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet("font-weight: 600;")
        layout.addWidget(label)

        button = QPushButton("Open")
        button.setObjectName("Primary")
        button.clicked.connect(lambda: on_open(package, name))
        layout.addWidget(button)

        self.body().addLayout(layout)


class AppsPage(QWidget):
    """A grid of the phone's apps. Opening one needs scrcpy over adb."""

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._apps: list[dict] = []
        self._tiles: dict[str, AppTile] = {}
        self._columns = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])

        header = QHBoxLayout()
        header.addWidget(heading("Apps", "Open one app in its own window"), 1)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps...")
        self.search.setMaximumWidth(260)
        self.search.textChanged.connect(self._render_apps)
        header.addWidget(self.search, 0, Qt.AlignmentFlag.AlignVCenter)

        refresh = QPushButton("Refresh")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.load_apps)
        header.addWidget(refresh, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(header_row(header))

        self.note = QLabel()
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        outer.addWidget(self.note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(SPACE["md"])
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.grid_host)
        outer.addWidget(self.scroll, 1)

        self.empty = EmptyState(
            "▦",
            "No app list yet",
            "Connect the companion app to see your installed apps here.",
            "Load apps",
        )
        self.empty.actionClicked.connect(self.load_apps)
        outer.addWidget(self.empty)

        self.toast = Toast(self)

        hub.connectionChanged.connect(lambda _c: self.load_apps())
        hub.icons.iconReady.connect(self._on_icon)
        hub.mirrors.failed.connect(
            lambda m: self.toast.show_message(m[:120], palette, "danger")
        )
        self.load_apps()

    # -- loading -------------------------------------------------------------

    def load_apps(self) -> None:
        if not self.hub.companion.connected:
            self._apps = []
            self._render_apps()
            return
        self.hub.companion.request(
            {"t": "app_list"}, lambda reply: self._on_apps(reply.get("items", []))
        )

    def _on_apps(self, items: list) -> None:
        show_system = self.hub.config.mirror.show_system_apps
        self._apps = [app for app in items if show_system or not app.get("system")]
        self._render_apps()

    def _render_apps(self) -> None:
        needle = self.search.text().strip().lower()
        visible = [
            app for app in self._apps
            if not needle or needle in app.get("name", "").lower()
        ]

        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        self._tiles.clear()

        # As many as fit: a fixed four left most of a wide window empty.
        self._columns = max(2, (self.scroll.viewport().width() or 900) // TILE)
        for index, app in enumerate(visible):
            package = app.get("package", "")
            tile = AppTile(
                package,
                app.get("name", package),
                self._open_app,
                self.palette_tokens,
                self.hub.icons,
            )
            self._tiles[package] = tile
            self.grid.addWidget(tile, index // self._columns, index % self._columns)

        self.scroll.setVisible(bool(visible))
        self.empty.setVisible(not visible)
        if not visible:
            if not self.hub.companion.connected:
                self.empty.update_text(
                    "No app list yet",
                    "Connect the companion app to see your installed apps here.",
                )
            elif needle:
                self.empty.update_text("No matches", f"Nothing matching '{needle}'.")

        if not mirror.available():
            self.note.setText(
                "Listing works over the companion app, but opening an app needs "
                "scrcpy. " + packages.advice("scrcpy")
            )
        elif not self.hub.serial:
            self.note.setText("Opening an app needs the phone reachable over adb.")
        self.note.setVisible(not mirror.available() or not self.hub.serial)

    # -- opening -------------------------------------------------------------

    def _open_app(self, package: str, name: str) -> None:
        if self.hub.mirrors.is_running(package):
            self.hub.mirrors.close(package)
            return
        cfg = self.hub.config.mirror
        try:
            argv = mirror.app_command(
                self.hub.serial,
                package,
                mirror.MirrorOptions(
                    max_size=cfg.max_size,
                    fps=cfg.fps,
                    bitrate=cfg.bitrate,
                    audio=cfg.audio,
                    stay_awake=cfg.stay_awake,
                    turn_screen_off=cfg.turn_screen_off,
                ),
                size=cfg.app_window_size,
                title=name,
            )
            self.hub.mirrors.open(package, name, argv)
            self.toast.show_message(f"Opening {name}...", self.palette_tokens)
        except mirror.MirrorError as exc:
            self.toast.show_message(str(exc)[:140], self.palette_tokens, "danger")

    def _on_icon(self, package: str, pixmap) -> None:
        tile = self._tiles.get(package)
        if tile is not None:
            tile.avatar.set_pixmap_rounded(pixmap)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.toast._reposition()
        if self._apps and max(2, self.scroll.viewport().width() // TILE) != self._columns:
            self._render_apps()
