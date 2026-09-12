"""The application shell: sidebar, pages and tray icon."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

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
from .widgets import Pill

#: name, icon, page class, and the feature switch that governs it. Settings has
#: no switch, because it is where the switches live.
PAGES = [
    ("Overview", "▦", HomePage, None),
    ("Notifications", "🔔", NotificationsPage, "notifications"),
    ("Calls", "📞", CallsPage, "calls"),
    ("Messages", "💬", MessagesPage, "messages"),
    ("Photos", "🖼", PhotosPage, "photos"),
    ("Screen", "📱", ScreenPage, "screen"),
    ("Webcam", "🎥", WebcamPage, "webcam"),
    ("Audio", "🎧", AudioPage, "bluetooth_audio"),
    ("Do Not Disturb", "🌙", DndPage, "dnd_sync"),
    ("Hotspot", "📶", HotspotPage, "hotspot"),
    ("Settings", "⚙", SettingsPage, None),
]


class MainWindow(QMainWindow):
    def __init__(self, hub: Hub, palette: Palette) -> None:
        super().__init__()
        self.hub = hub
        self.palette_tokens = palette
        self._quitting = False

        self.setWindowTitle("Tessera")
        self.resize(1180, 780)
        self.setMinimumSize(900, 600)

        root = QWidget()
        root.setObjectName("Root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        for _name, _icon, page_class, _feature in PAGES:
            page = page_class(hub, palette)
            self.stack.addWidget(page)
            if isinstance(page, SettingsPage):
                page.featuresChanged.connect(self._apply_feature_visibility)
            if isinstance(page, HomePage):
                # Tiles hand off to their full page rather than duplicating it.
                page.openPage.connect(self.show_page)
        layout.addWidget(self.stack, 1)

        self.setCentralWidget(root)
        self._apply_feature_visibility()
        self.nav.setCurrentRow(0)

        self._build_tray()

        hub.statusChanged.connect(self._set_status)
        hub.connectionChanged.connect(lambda _c: self._refresh_header())
        hub.batteryChanged.connect(self._on_battery)
        hub.errorOccurred.connect(self._set_status)
        hub.capabilitiesChanged.connect(lambda _c: self._refresh_header())
        self._refresh_header()

    # -- sidebar -------------------------------------------------------------

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(232)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["md"])

        self.brand = QLabel("Tessera")
        self.brand.setObjectName("BrandName")
        layout.addWidget(self.brand)

        self.device_label = QLabel("No phone")
        self.device_label.setObjectName("BrandSub")
        self.device_label.setWordWrap(True)
        layout.addWidget(self.device_label)

        badges = QHBoxLayout()
        badges.setSpacing(SPACE["xs"])
        self.link_pill = Pill("Offline", "muted")
        self.link_pill.apply(self.palette_tokens)
        badges.addWidget(self.link_pill)

        self.battery_pill = Pill("", "muted")
        self.battery_pill.apply(self.palette_tokens)
        self.battery_pill.setVisible(False)
        badges.addWidget(self.battery_pill)
        badges.addStretch(1)
        layout.addLayout(badges)

        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setIconSize(QSize(18, 18))
        for name, icon, _page, _feature in PAGES:
            self.nav.addItem(QListWidgetItem(f"{icon}   {name}"))
        self.nav.currentRowChanged.connect(self._change_page)
        layout.addWidget(self.nav, 1)

        self.status = QLabel("")
        self.status.setObjectName("BrandSub")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.reconnect_button = QPushButton("Reconnect")
        self.reconnect_button.setToolTip(
            "Look for the phone again — useful after changing network or "
            "waking the phone"
        )
        self.reconnect_button.clicked.connect(self._reconnect)
        layout.addWidget(self.reconnect_button)

        return sidebar

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
        for index, (_name, _icon, _page, feature) in enumerate(PAGES):
            item = self.nav.item(index)
            if item is None:
                continue
            item.setHidden(bool(feature) and not getattr(features, feature, True))

        # Never leave the selection on something now hidden.
        current = self.nav.currentRow()
        if 0 <= current < self.nav.count() and self.nav.item(current).isHidden():
            for index in range(self.nav.count()):
                if not self.nav.item(index).isHidden():
                    self.nav.setCurrentRow(index)
                    break

    def show_page(self, name: str) -> None:
        """Switch to a page by name, ignoring one that is switched off."""
        for index, (page_name, _icon, _page, _feature) in enumerate(PAGES):
            if page_name != name:
                continue
            item = self.nav.item(index)
            if item is not None and not item.isHidden():
                self.nav.setCurrentRow(index)
            return

    def _change_page(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    # -- header state --------------------------------------------------------

    def _refresh_header(self) -> None:
        self.device_label.setText(self.hub.phone_name)
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
            [name for name, _i, _p, _f in PAGES].index("Screen")
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
