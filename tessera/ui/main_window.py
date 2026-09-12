"""The application shell: device panel, tab strip, pages and tray icon."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QTabBar,
    QToolButton,
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
from .panel import DevicePanel
from .theme import SPACE, Palette, tab_stylesheet
from .widgets import themed_icon, tinted_icon

#: name, icon theme name, text fallback, page class, and the feature switch
#: that governs it. Settings has no switch: it is where the switches live.
#:
#: Icon names are the freedesktop ones, so the desktop's own icon theme draws
#: them. The emoji are only for a system with no usable theme.
PAGES = [
    ("Overview", "go-home", "▦", HomePage, None),
    ("Calls", "call-start", "📞", CallsPage, "calls"),
    ("Messages", "mail-message", "💬", MessagesPage, "messages"),
    ("Photos", "folder-pictures", "🖼", PhotosPage, "photos"),
    ("Notifications", "notifications", "🔔", NotificationsPage, "notifications"),
    ("Screen", "smartphone", "📱", ScreenPage, "screen"),
    ("Webcam", "camera-web", "🎥", WebcamPage, "webcam"),
    ("Audio", "audio-headphones", "🎧", AudioPage, "bluetooth_audio"),
    ("Do Not Disturb", "notifications-disabled", "🌙", DndPage, "dnd_sync"),
    ("Hotspot", "network-wireless-hotspot", "📶", HotspotPage, "hotspot"),
    ("Settings", "settings-configure", "⚙", SettingsPage, None),
]

#: The four that earn a permanent tab. Everything else is a device feature
#: reached from the More menu -- ten worded tabs in one row was unreadable,
#: and Phone Link itself shows four.
PRIMARY = ("Overview", "Calls", "Messages", "Photos")


class PageTabs(QTabBar):
    """The tab strip, with the selected tab underlined in the accent colour."""

    def __init__(self, palette: Palette, parent: QWidget | None = None):
        super().__init__(parent)
        self._accent = QColor(palette.accent)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        index = self.currentIndex()
        if index < 0 or not self.isTabVisible(index):
            return
        rect = self.tabRect(index)
        # Clamped to the widget: a layout can hand the bar fewer pixels than a
        # tab is tall, and the underline would then be drawn off the bottom.
        bottom = min(float(rect.bottom()), self.height() - 1.0)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._accent)
        painter.drawRoundedRect(
            QRectF(rect.left() + 6, bottom - 3.0, rect.width() - 12, 3.0), 1.5, 1.5
        )
        painter.end()


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

        self.panel = DevicePanel(hub, palette)
        self.panel.pageRequested.connect(self.show_page)
        self.panel.statusMessage.connect(self._set_status)
        layout.addWidget(self.panel)

        # Content area: tabs across the top, the selected page beneath.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_strip())

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
        hub.errorOccurred.connect(self._set_status)

    # -- tab strip -----------------------------------------------------------

    def _build_strip(self) -> QWidget:
        """Four tabs, a More menu and the gear. Nothing else belongs up here."""
        holder = QWidget()
        holder.setObjectName("TabStrip")
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(SPACE["lg"], SPACE["sm"], SPACE["md"], 0)
        layout.setSpacing(SPACE["sm"])

        self.tabs = PageTabs(self.palette_tokens)
        self.tabs.setObjectName("Tabs")
        self.tabs.setStyleSheet(tab_stylesheet(self.palette_tokens))
        self.tabs.setExpanding(False)
        self.tabs.setDocumentMode(True)
        # The strip draws the rule under the tabs, full width; the bar's own
        # base stopped short of the More button.
        self.tabs.setDrawBase(False)
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        for name in PRIMARY:
            self._add_tab(name)

        #: The last tab holds whichever secondary page was opened from the More
        #: menu, so the strip always says which page is showing. Selecting a
        #: page with no tab of its own used to leave a different tab lit.
        self.slot = self._add_tab("Overview")
        self.tabs.setTabVisible(self.slot, False)

        # Room for the label, the icon and the underline. Asking the bar for
        # its size hint here is too early: it has not been polished yet, and
        # the answer came back three pixels short.
        self.tabs.setMinimumHeight(
            max(20, self.tabs.fontMetrics().height()) + SPACE["lg"] + 2
        )
        self.tabs.currentChanged.connect(self._change_page)
        # The bar takes the leftover width rather than sitting next to a
        # spacer: beside a stretch it collapses to its scrollable minimum,
        # which showed one elided tab and two arrows.
        layout.addWidget(self.tabs, 1)

        self.more_button = QToolButton()
        self.more_button.setObjectName("Strip")
        self.more_button.setText("More")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_menu = QMenu(self.more_button)
        self.more_button.setMenu(self.more_menu)
        layout.addWidget(self.more_button)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("Strip")
        gear = tinted_icon(
            themed_icon("settings-configure"), self.palette_tokens.muted, 16
        )
        if gear.isNull():
            self.settings_button.setText("⚙")
        else:
            self.settings_button.setIcon(gear)
            self.settings_button.setIconSize(QSize(16, 16))
        self.settings_button.setToolTip("Settings")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(lambda: self.show_page("Settings"))
        layout.addWidget(self.settings_button)
        return holder

    def _tab_icon(self, name: str) -> QIcon:
        icon_name = next(i for n, i, _g, _p, _f in PAGES if n == name)
        return tinted_icon(themed_icon(icon_name), self.palette_tokens.text, 16)

    def _add_tab(self, name: str) -> int:
        glyph = next(g for n, _i, g, _p, _f in PAGES if n == name)
        icon = self._tab_icon(name)
        index = self.tabs.addTab(name if not icon.isNull() else f"{glyph} {name}")
        if not icon.isNull():
            self.tabs.setTabIcon(index, icon)
        self.tabs.setTabData(index, name)
        self.tabs.setTabToolTip(index, name)
        return index

    def _fill_slot(self, name: str) -> None:
        glyph = next(g for n, _i, g, _p, _f in PAGES if n == name)
        icon = self._tab_icon(name)
        self.tabs.setTabText(self.slot, name if not icon.isNull() else f"{glyph} {name}")
        self.tabs.setTabIcon(self.slot, icon)
        self.tabs.setTabData(self.slot, name)
        self.tabs.setTabToolTip(self.slot, name)
        self.tabs.setTabVisible(self.slot, True)

    def _rebuild_more_menu(self) -> None:
        """The pages with no tab of their own, minus the ones switched off."""
        self.more_menu.clear()
        features = self.hub.config.features
        for name, icon_name, _glyph, _page, feature in PAGES:
            if name in PRIMARY or name == "Settings":
                continue
            if feature and not getattr(features, feature, True):
                continue
            action = QAction(name, self.more_menu)
            icon = themed_icon(icon_name)
            if not icon.isNull():
                action.setIcon(icon)
            action.triggered.connect(lambda _c=False, n=name: self.show_page(n))
            self.more_menu.addAction(action)
        self.more_button.setVisible(not self.more_menu.isEmpty())

    # -- navigation ----------------------------------------------------------

    def _page_index(self, name: str) -> int:
        return next(
            (i for i, (n, _i, _g, _p, _f) in enumerate(PAGES) if n == name), 0
        )

    def _enabled(self, name: str) -> bool:
        feature = next((f for n, _i, _g, _p, f in PAGES if n == name), None)
        return not feature or bool(getattr(self.hub.config.features, feature, True))

    def _apply_feature_visibility(self) -> None:
        """Hide what is switched off.

        A page left reachable while its feature is disabled invites the obvious
        bug report, so the strip and the menu both reflect what is available.
        """
        for index in range(self.tabs.count()):
            name = self.tabs.tabData(index)
            if index == self.slot:
                if not self.tabs.isTabVisible(self.slot):
                    continue
                self.tabs.setTabVisible(self.slot, self._enabled(name))
            elif name:
                self.tabs.setTabVisible(index, self._enabled(name))
        self._rebuild_more_menu()

        if not self.tabs.isTabVisible(self.tabs.currentIndex()):
            for index in range(self.tabs.count()):
                if self.tabs.isTabVisible(index):
                    self.tabs.setCurrentIndex(index)
                    self._change_page(index)
                    break

    def show_page(self, name: str) -> None:
        """Switch to a page by name, ignoring one that is switched off."""
        if not any(n == name for n, _i, _g, _p, _f in PAGES) or not self._enabled(name):
            return
        for index in range(self.tabs.count()):
            if index != self.slot and self.tabs.tabData(index) == name:
                self.tabs.setCurrentIndex(index)
                return
        self._fill_slot(name)
        if self.tabs.currentIndex() == self.slot:
            # Same tab, different page: currentChanged will not fire.
            self._change_page(self.slot)
        else:
            self.tabs.setCurrentIndex(self.slot)

    def _change_page(self, index: int) -> None:
        name = self.tabs.tabData(index) if index >= 0 else None
        if name:
            self.stack.setCurrentIndex(self._page_index(name))

    @property
    def current_page_name(self) -> str:
        return self.tabs.tabData(self.tabs.currentIndex()) or ""

    def _set_status(self, message: str) -> None:
        self.panel.set_status(message)

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
        self.show_page("Screen")
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
