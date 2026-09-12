"""Phone screen mirroring, and individual apps in their own windows."""

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
from ..widgets import Avatar, Card, EmptyState, Toast, heading


class AppTile(Card):
    """One launchable app, opening into its own desktop window."""

    def __init__(self, package: str, name: str, on_open, palette: Palette, icons=None, parent=None):
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


class ScreenPage(QWidget):
    """Mirror the whole phone, or run one app beside it."""

    def __init__(self, hub: Hub, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self.hub = hub
        self.palette_tokens = palette
        self._apps: list[dict] = []
        self._tiles: dict[str, AppTile] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        outer.setSpacing(SPACE["lg"])
        outer.addWidget(
            heading("Screen", "Mirror your phone, or open a single app in its own window")
        )

        # -- full mirror -----------------------------------------------------
        mirror_card = Card(self)
        title = QLabel("Whole phone")
        title.setObjectName("SectionTitle")
        mirror_card.add(title)

        description = QLabel(
            "Opens your phone's screen in a window you can touch, type and scroll in."
        )
        description.setObjectName("Muted")
        description.setWordWrap(True)
        mirror_card.add(description)

        controls = QHBoxLayout()
        self.mirror_button = QPushButton("Start mirroring")
        self.mirror_button.setObjectName("Primary")
        self.mirror_button.clicked.connect(self._toggle_mirror)
        controls.addWidget(self.mirror_button)

        self.screen_off = QPushButton("Mirror with phone screen off")
        self.screen_off.setCheckable(True)
        self.screen_off.setChecked(hub.config.mirror.turn_screen_off)
        self.screen_off.toggled.connect(self._set_screen_off)
        controls.addWidget(self.screen_off)
        controls.addStretch(1)
        mirror_card.body().addLayout(controls)

        self.mirror_status = QLabel()
        self.mirror_status.setObjectName("Muted")
        self.mirror_status.setWordWrap(True)
        mirror_card.add(self.mirror_status)
        outer.addWidget(mirror_card)

        # -- app launcher ----------------------------------------------------
        header = QHBoxLayout()
        apps_title = QLabel("Apps")
        apps_title.setObjectName("SectionTitle")
        header.addWidget(apps_title)
        header.addStretch(1)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps...")
        self.search.setMaximumWidth(260)
        self.search.textChanged.connect(self._render_apps)
        header.addWidget(self.search)

        refresh = QPushButton("Refresh")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.load_apps)
        header.addWidget(refresh)
        outer.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(SPACE["md"])
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.grid_host)
        outer.addWidget(self.scroll, 1)

        self.empty = EmptyState(
            "📱",
            "No app list yet",
            "Connect the companion app to see your installed apps here.",
            "Load apps",
        )
        self.empty.actionClicked.connect(self.load_apps)
        outer.addWidget(self.empty)

        self.toast = Toast(self)

        hub.mirrors.changed.connect(self._update_state)
        hub.mirrors.failed.connect(lambda m: self.toast.show_message(m[:120], palette, "danger"))
        hub.connectionChanged.connect(lambda _c: self.load_apps())
        hub.icons.iconReady.connect(self._on_icon)

        self._update_state()
        self.load_apps()

    # -- mirroring -----------------------------------------------------------

    def _options(self) -> mirror.MirrorOptions:
        cfg = self.hub.config.mirror
        return mirror.MirrorOptions(
            max_size=cfg.max_size,
            fps=cfg.fps,
            bitrate=cfg.bitrate,
            audio=cfg.audio,
            stay_awake=cfg.stay_awake,
            turn_screen_off=cfg.turn_screen_off,
        )

    def _set_screen_off(self, enabled: bool) -> None:
        self.hub.config.mirror.turn_screen_off = enabled
        self.hub.config.save()

    def _toggle_mirror(self) -> None:
        if self.hub.mirrors.is_running("screen"):
            self.hub.mirrors.close("screen")
            return
        try:
            argv = mirror.mirror_command(
                self.hub.serial, self._options(), self.hub.phone_name
            )
            self.hub.mirrors.open("screen", "Phone screen", argv)
        except mirror.MirrorError as exc:
            self.toast.show_message(str(exc)[:140], self.palette_tokens, "danger")

    def _open_app(self, package: str, name: str) -> None:
        if self.hub.mirrors.is_running(package):
            self.hub.mirrors.close(package)
            return
        try:
            argv = mirror.app_command(
                self.hub.serial,
                package,
                self._options(),
                size=self.hub.config.mirror.app_window_size,
                title=name,
            )
            self.hub.mirrors.open(package, name, argv)
            self.toast.show_message(f"Opening {name}...", self.palette_tokens)
        except mirror.MirrorError as exc:
            self.toast.show_message(str(exc)[:140], self.palette_tokens, "danger")

    def _update_state(self) -> None:
        running = self.hub.mirrors.is_running("screen")
        self.mirror_button.setText("Stop mirroring" if running else "Start mirroring")

        if not mirror.available():
            self.mirror_status.setText(
                "scrcpy is not installed. " + packages.advice("scrcpy")
            )
        elif not self.hub.serial:
            self.mirror_status.setText(
                "No phone reachable over adb, which screen control needs."
            )
        else:
            open_windows = len(self.hub.mirrors.sessions)
            self.mirror_status.setText(
                f"{open_windows} window{'s' if open_windows != 1 else ''} open"
                if open_windows
                else "Ready."
            )

    # -- app list ------------------------------------------------------------

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
        self._apps = [
            app for app in items if show_system or not app.get("system")
        ]
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

        columns = 4
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
            self.grid.addWidget(tile, index // columns, index % columns)

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

    def _on_icon(self, package: str, pixmap) -> None:
        tile = self._tiles.get(package)
        if tile is not None:
            tile.avatar.set_pixmap_rounded(pixmap)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.toast._reposition()
