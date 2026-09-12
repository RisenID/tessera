"""The application shell: sidebar, pages and tray icon."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QProgressBar,
    QStackedWidget,
    QSystemTrayIcon,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from ..backends.mpris import MprisPlayer
from ..core.hub import Hub
from .pages.audio import AudioPage
from .pages.home import HomePage
from .pages.calls import CallsPage
from .pages.dnd import DndPage
from .pages.hotspot import HotspotPage
from .pages.messages import MessagesPage
from .pages.notifications import NotificationsPage
from .pages.photos import PhotosPage
from .pages.screen import ScreenPage
from .pages.settings import SettingsPage
from .pages.webcam import WebcamPage
from .theme import SPACE, Palette
from .widgets import Pill, divider

#: Shorter labels for the tab strip, where ten of them share one row.
TAB_LABELS = {"Do Not Disturb": "DND", "Notifications": "Alerts"}

#: name, icon theme name, text fallback, page class, and the feature switch
#: that governs it. Settings has no switch: it is where the switches live.
#:
#: Icon names are the freedesktop ones, so the desktop's own icon theme draws
#: them. The emoji are only for a system with no usable theme.
PAGES = [
    ("Overview", "go-home", "▦", HomePage, None),
    ("Notifications", "notifications", "🔔",
     NotificationsPage, "notifications"),
    ("Calls", "call-start", "📞", CallsPage, "calls"),
    ("Messages", "mail-message", "💬", MessagesPage, "messages"),
    ("Photos", "folder-pictures", "🖼", PhotosPage, "photos"),
    ("Screen", "smartphone", "📱", ScreenPage, "screen"),
    ("Webcam", "camera-web", "🎥", WebcamPage, "webcam"),
    ("Audio", "audio-headphones", "🎧", AudioPage, "bluetooth_audio"),
    ("Do Not Disturb", "notifications-disabled", "🌙", DndPage, "dnd_sync"),
    ("Hotspot", "network-wireless-hotspot", "📶", HotspotPage, "hotspot"),
    ("Settings", "settings-configure", "⚙", SettingsPage, None),
]


class MainWindow(QMainWindow):
    def __init__(self, hub: Hub, palette: Palette) -> None:
        super().__init__()
        self.hub = hub
        self.palette_tokens = palette
        self._quitting = False
        self._player = MprisPlayer(self)
        self._media_service = ""

        self.setWindowTitle("Tessera")
        self.resize(1180, 780)
        self.setMinimumSize(900, 600)

        root = QWidget()
        root.setObjectName("Root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_device_panel())

        # Content area: tabs across the top, the selected page beneath -- the
        # arrangement Phone Link uses, rather than a list down the side.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_tabs())

        self.stack = QStackedWidget()
        for _name, _icon, _glyph, page_class, _feature in PAGES:
            page = page_class(hub, palette)
            self.stack.addWidget(page)
            if isinstance(page, SettingsPage):
                page.featuresChanged.connect(self._apply_feature_visibility)
            if isinstance(page, HomePage):
                # Tiles hand off to their full page rather than duplicating it.
                page.openPage.connect(self.show_page)
        content_layout.addWidget(self.stack, 1)
        layout.addWidget(content, 1)

        self.setCentralWidget(root)
        self._apply_feature_visibility()
        self.tabs.setCurrentIndex(0)
        self._change_page(0)

        self._build_tray()

        hub.statusChanged.connect(self._set_status)
        hub.connectionChanged.connect(lambda _c: self._refresh_header())
        hub.batteryChanged.connect(self._on_battery)
        hub.mediaChanged.connect(lambda _m: self._refresh_media())
        hub.dndChanged.connect(self._sync_toggles)
        hub.errorOccurred.connect(self._set_status)
        hub.capabilitiesChanged.connect(lambda _c: self._refresh_header())
        self._refresh_media()
        self._sync_toggles()
        self._media_timer = QTimer(self)
        self._media_timer.timeout.connect(self._refresh_media)
        self._media_timer.start(8000)
        self._refresh_header()

    # -- device panel --------------------------------------------------------

    def _build_device_panel(self) -> QWidget:
        """The phone itself: what it is, how it is doing, what it is playing.

        Phone Link's left-hand pane. Navigation is not in here; see _build_tabs.
        """
        panel = QWidget()
        panel.setObjectName("Sidebar")
        panel.setFixedWidth(300)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["md"])

        top = QHBoxLayout()
        top.setSpacing(SPACE["sm"])
        phone_icon = QLabel()
        icon = QIcon.fromTheme("smartphone")
        if not icon.isNull():
            phone_icon.setPixmap(icon.pixmap(32, 32))
        top.addWidget(phone_icon, 0, Qt.AlignmentFlag.AlignTop)

        names = QVBoxLayout()
        names.setSpacing(0)
        self.brand = QLabel("No phone")
        self.brand.setObjectName("BrandName")
        self.brand.setWordWrap(True)
        names.addWidget(self.brand)
        self.device_label = QLabel("")
        self.device_label.setObjectName("BrandSub")
        names.addWidget(self.device_label)
        top.addLayout(names, 1)

        layout.addLayout(top)

        badges = QHBoxLayout()
        badges.setSpacing(SPACE["xs"])
        self.link_pill = Pill("Offline", "muted")
        self.link_pill.apply(self.palette_tokens)
        badges.addWidget(self.link_pill)
        badges.addStretch(1)
        layout.addLayout(badges)

        # Battery as a bar rather than a number in a chip: it is the one thing
        # on this panel people read at a glance.
        self.battery_row = QWidget()
        battery_layout = QVBoxLayout(self.battery_row)
        battery_layout.setContentsMargins(0, 0, 0, 0)
        battery_layout.setSpacing(2)
        self.battery_label = QLabel("Battery")
        self.battery_label.setObjectName("BrandSub")
        battery_layout.addWidget(self.battery_label)
        self.battery_bar = QProgressBar()
        self.battery_bar.setRange(0, 100)
        self.battery_bar.setTextVisible(False)
        self.battery_bar.setFixedHeight(6)
        battery_layout.addWidget(self.battery_bar)
        self.battery_row.setVisible(False)
        layout.addWidget(self.battery_row)

        layout.addWidget(divider())
        layout.addWidget(self._build_quick_toggles())
        layout.addWidget(divider())
        layout.addWidget(self._build_now_playing())
        layout.addStretch(1)

        self.status = QLabel("")
        self.status.setObjectName("BrandSub")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.reconnect_button = QPushButton("Reconnect")
        self.reconnect_button.setToolTip("Look for the phone again")
        refresh_icon = QIcon.fromTheme("view-refresh")
        if not refresh_icon.isNull():
            self.reconnect_button.setIcon(refresh_icon)
        self.reconnect_button.clicked.connect(self._reconnect)
        layout.addWidget(self.reconnect_button)

        return panel

    def _build_quick_toggles(self) -> QWidget:
        """The phone's own switches, next to the phone they belong to.

        Navigation is not among them: the tabs above cover that, and buttons
        that merely opened a page were a second way to do the same thing.
        """
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["xs"])

        self.dnd_toggle = self._chip("Do Not Disturb", "notifications-disabled", True)
        self.dnd_toggle.clicked.connect(self._toggle_dnd)
        layout.addWidget(self.dnd_toggle)

        self.clipboard_toggle = self._chip("Clipboard sharing", "edit-paste", True)
        self.clipboard_toggle.setChecked(
            self.hub.config.features.clipboard
            and self.hub.config.clipboard.mode != "off"
        )
        self.clipboard_toggle.clicked.connect(self._toggle_clipboard)
        layout.addWidget(self.clipboard_toggle)

        ring = self._chip("Ring phone", "audio-volume-high")
        ring.clicked.connect(self._ring)
        layout.addWidget(ring)
        return block

    @staticmethod
    def _chip(label: str, icon_name: str, checkable: bool = False) -> QPushButton:
        button = QPushButton(label)
        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            button.setIcon(icon)
        button.setCheckable(checkable)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _toggle_dnd(self) -> None:
        self.hub.set_phone_dnd("priority" if self.dnd_toggle.isChecked() else "off")

    def _toggle_clipboard(self) -> None:
        on = self.clipboard_toggle.isChecked()
        self.hub.config.features.clipboard = on
        self.hub.config.clipboard.mode = "two_way" if on else "off"
        self.hub.clipboard.set_mode(self.hub.config.clipboard.mode)
        self.hub.config.save()
        self.hub.apply_features()
        self._set_status("Clipboard sharing on" if on else "Clipboard sharing off")

    def _ring(self) -> None:
        try:
            self.hub.kdeconnect.ring()
            self._set_status("Ringing your phone")
        except Exception:
            self._set_status("Ringing needs KDE Connect paired with this phone")

    def _sync_toggles(self, mode: str = "") -> None:
        self.dnd_toggle.setChecked((mode or self.hub.phone_dnd) != "off")

    def _build_now_playing(self) -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["xs"])

        heading = QLabel("Now playing")
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)

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
            button = QPushButton()
            icon = QIcon.fromTheme(icon_name)
            if icon.isNull():
                button.setText(glyph)
            else:
                button.setIcon(icon)
            button.setToolTip(tip)
            button.setFixedWidth(44)
            button.clicked.connect(lambda _c=False, a=action: self._media_control(a))
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)
        return block

    # -- tabs ----------------------------------------------------------------

    def _build_tabs(self) -> QWidget:
        """Page navigation across the top of the content area."""
        holder = QWidget()
        holder.setObjectName("TabStrip")
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(SPACE["lg"], SPACE["sm"], SPACE["lg"], 0)
        layout.setSpacing(0)

        self.tabs = QTabBar()
        self.tabs.setObjectName("Tabs")
        self.tabs.setExpanding(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setDrawBase(True)
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        for name, icon_name, glyph, _page, _feature in PAGES:
            icon = QIcon.fromTheme(icon_name)
            # Settings is the gear at the end, not a worded tab.
            label = "" if name == "Settings" else TAB_LABELS.get(name, name)
            index = self.tabs.addTab(label if not icon.isNull() else f"{glyph} {label}")
            if not icon.isNull():
                self.tabs.setTabIcon(index, icon)
            self.tabs.setTabToolTip(index, name)
        self.tabs.setTabToolTip(self.tabs.count() - 1, "Settings")
        self.tabs.currentChanged.connect(self._change_page)
        layout.addWidget(self.tabs, 1)
        return holder

    def _reconnect(self) -> None:
        self.reconnect_button.setEnabled(False)
        self.hub.reconnect()
        # Re-enable shortly: the attempt is asynchronous, and leaving the
        # button dead would look like a hang.
        QTimer.singleShot(2500, lambda: self.reconnect_button.setEnabled(True))

    def _apply_feature_visibility(self) -> None:
        """Hide the pages for features that are switched off.

        Leaving a page reachable while its feature is disabled invites the
        obvious bug report, so the nav reflects what is actually available.
        """
        features = self.hub.config.features
        for index, (name, _icon, _glyph, _page, feature) in enumerate(PAGES):
            off = bool(feature) and not getattr(features, feature, True)
            self.tabs.setTabVisible(index, not off)

        if not self.tabs.isTabVisible(self.tabs.currentIndex()):
            for index in range(self.tabs.count()):
                if self.tabs.isTabVisible(index):
                    self.tabs.setCurrentIndex(index)
                    break

    def show_page(self, name: str) -> None:
        """Switch to a page by name, ignoring one that is switched off."""
        for index, (page_name, _icon, _glyph, _page, feature) in enumerate(PAGES):
            if page_name != name:
                continue
            features = self.hub.config.features
            if feature and not getattr(features, feature, True):
                return
            if self.tabs.isTabVisible(index):
                self.tabs.setCurrentIndex(index)
            return

    def _change_page(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    def _media_control(self, action: str) -> None:
        if self.hub.companion.connected and self.hub.companion.supports("media_control"):
            self.hub.media_command(action.lower().replace("playpause", "playpause"))
            QTimer.singleShot(400, self._refresh_media)
            return
        try:
            self._player.control(self._media_service, action)
        except RuntimeError as exc:
            self._set_status(str(exc)[:120])
        QTimer.singleShot(600, self._refresh_media)

    def _refresh_media(self) -> None:
        """What the phone is playing, from the companion app or from MPRIS."""
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

    # -- header state --------------------------------------------------------

    def _refresh_header(self) -> None:
        name = self.hub.phone_name
        self.brand.setText(name or "No phone")
        model = self.hub.companion.phone.name or self.hub.bluetooth_name
        self.device_label.setText("" if model == name else model)
        source = self.hub.source
        if source == "companion":
            self.link_pill.set_state("Companion app", "success")
        elif source == "kdeconnect":
            self.link_pill.set_state("KDE Connect", "warning")
        else:
            self.link_pill.set_state("Offline", "muted")

    def _on_battery(self, level: int, charging: bool) -> None:
        self.battery_pill.setVisible(True)
        tone = "success" if charging or level > 30 else ("warning" if level > 15 else "danger")
        self.battery_pill.set_state(f"{'⚡' if charging else ''}{level}%", tone)

    def _set_status(self, message: str) -> None:
        self.status.setText(message)

    # -- tray ----------------------------------------------------------------

    def _build_tray(self) -> None:
        # A plain coloured square keeps the app themeable without shipping icons.
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        icon = QIcon.fromTheme("smartphone", QIcon(pixmap))

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Tessera")

        menu = QMenu()
        show = QAction("Show Tessera", self)
        show.triggered.connect(self._restore)
        menu.addAction(show)

        mirror_action = QAction("Mirror phone screen", self)
        mirror_action.triggered.connect(self._mirror_from_tray)
        menu.addAction(mirror_action)
        menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore()

    def _restore(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _mirror_from_tray(self) -> None:
        self.nav.setCurrentRow(
            [name for name, _i, _g, _p, _f in PAGES].index("Screen")
        )
        self._restore()

    def _quit(self) -> None:
        self._quitting = True
        self.close()

    # -- window lifecycle ----------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Closing hides to the tray; quitting really quits.

        The point of this app is to be there when a notification arrives, so
        the window closing should not stop it listening.
        """
        if self._quitting or not self.tray.isVisible():
            self.hub.stop()
            event.accept()
            # Quitting on the last window is disabled so that closing the
            # window can hide to the tray, which means a real quit has to ask
            # for it explicitly.
            application = QApplication.instance()
            if application is not None:
                application.quit()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Tessera",
            "Still running in the tray. Choose Quit to stop it.",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )
