"""The application shell: device panel, tab strip, pages and tray icon."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import platform
from ..core.hub import Hub
from .pages.apps import AppsPage
from .pages.audio import AudioPage
from .pages.share import SharePage
from .pages.home import HomePage
from .pages.calls import CallsPage
from .pages.dnd import DndPage
from .pages.hotspot import HotspotPage
from .pages.messages import MessagesPage
from .pages.notifications import NotificationsPage
from .pages.photos import PhotosPage
from .pages.screen import ScreenPage
from .pages.settings import SettingsPage
from .pages.unavailable import UnavailablePage
from .pages.webcam import WebcamPage
from .panel import MAX_WIDTH, MIN_WIDTH, DevicePanel
from .popups import Popups
from .theme import SPACE, Palette, tab_stylesheet
from .widgets import themed_icon, tinted_icon

#: name, icon theme name, text fallback, page class, and the feature
#: switch that governs it.
PAGES = [
    ("Overview", "go-home", "▦", HomePage, None),
    ("Calls", "call-start", "📞", CallsPage, "calls"),
    ("Messages", "mail-message", "💬", MessagesPage, "messages"),
    ("Photos", "folder-pictures", "🖼", PhotosPage, "photos"),
    ("Apps", "view-list-icons", "▦", AppsPage, "apps"),
    ("Share", "document-send", "📤", SharePage, "file_transfer"),
    ("Notifications", "notifications", "🔔", NotificationsPage, "notifications"),
    ("Screen", "smartphone", "📱", ScreenPage, "screen"),
    ("Webcam", "camera-web", "🎥", WebcamPage, "webcam"),
    # phone_audio, not bluetooth_audio: the page leads with the route that
    # works everywhere, and hides its Bluetooth half where that cannot.
    ("Audio", "audio-headphones", "🎧", AudioPage, "phone_audio"),
    ("Do Not Disturb", "notifications-disabled", "🌙", DndPage, "dnd_sync"),
    ("Hotspot", "network-wireless-hotspot", "📶", HotspotPage, "hotspot"),
    ("Settings", "settings-configure", "⚙", SettingsPage, None),
]

#: Pages whose feature this platform cannot do at all. Hidden rather than
#: broken: see core/platform.py for the reasons, which Settings shows.
IMPOSSIBLE = frozenset(
    name for name, _i, _g, _p, feature in PAGES
    if feature and not platform.supported(feature)
)

#: The ones that earn a permanent tab: the phone's content.
PRIMARY = ("Overview", "Calls", "Messages", "Photos", "Apps")

#: Built at startup rather than on first visit: the one that is showing, and
#: the one whose construction tells the panel whether the hotspot is joined.
EAGER = ("Overview", "Hotspot")


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
        # Files dropped anywhere on the window go to the phone.
        self.setAcceptDrops(True)

        root = QWidget()
        root.setObjectName("Root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.panel = DevicePanel(hub, palette)
        self.panel.pageRequested.connect(self.show_page)
        self.panel.statusMessage.connect(self._set_status)
        self.panel.hotspotRequested.connect(
            lambda: self._from_panel("Hotspot")
        )
        self.panel.audioRequested.connect(lambda: self._from_panel("Audio"))
        self.panel.mirrorRequested.connect(lambda: self._from_panel("Screen"))

        # Content area: tabs across the top, the selected page beneath.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_strip())

        # Pages are built on first visit: each one costs widgets and a round
        # of requests to the phone, and most are never opened in a session.
        self.stack = QStackedWidget()
        self._built: set[int] = set()
        for name, _icon, _glyph, _page_class, feature in PAGES:
            if name in IMPOSSIBLE:
                # Never built: a page for a feature this platform lacks would
                # probe for Linux machinery the moment it was constructed.
                self.stack.addWidget(
                    UnavailablePage(name, platform.reason(feature), palette)
                )
                self._built.add(self.stack.count() - 1)
            else:
                self.stack.addWidget(QWidget())
        for name in EAGER:
            self._page(name)
        content_layout.addWidget(self.stack, 1)

        # A splitter, so the rail is dragged to whatever width suits rather
        # than being a number this code chose.
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("Split")
        self.splitter.setHandleWidth(4)
        self.splitter.addWidget(self.panel)
        self.splitter.addWidget(content)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(lambda _p, _i: self._remember_width())
        layout.addWidget(self.splitter, 1)

        #: Written a moment after the drag stops, not on every pixel of it.
        self._width_save = QTimer(self)
        self._width_save.setSingleShot(True)
        self._width_save.setInterval(700)
        self._width_save.timeout.connect(hub.config.save)

        self.setCentralWidget(root)
        self._apply_panel_width()
        self._apply_feature_visibility()
        self.tabs.setCurrentIndex(0)
        self._change_page(0)

        self._build_tray()
        #: The phone's notifications, repeated on this desktop: through the
        #: desktop's own notification server where there is one, so a message
        #: can be answered from the popup, and the tray everywhere else.
        self.popups = Popups(hub, self.tray, self)
        self.popups.opened.connect(self._on_popup_opened)
        # The desktop's media applet has an "open this player" button.
        hub.raiseRequested.connect(self._raise_window)

        hub.statusChanged.connect(self._set_status)
        hub.errorOccurred.connect(self._set_status)
        hub.fileReceived.connect(self._on_file_received)

    def _on_file_received(self, transfer) -> None:
        """Say that a file arrived, where a file arriving is easy to miss."""
        from ..backends.filetransfer import human

        self._set_status(f"{transfer.name} arrived · {human(transfer.size)}")
        if not self.hub.config.files.notify:
            return
        notifier = getattr(self.popups, "notifier", None)
        body = f"Saved to {transfer.path.parent}" if transfer.path else "Saved"
        if notifier is None or not notifier.available:
            # Windows, or a Linux session with no notification server: the
            # tray's own popup, which is a real toast on Windows.
            tray = getattr(self, "tray", None)
            if tray is not None and tray.isVisible():
                tray.showMessage(transfer.name, body, tray.icon(), 6000)
            return
        notifier.send_async(
            f"{transfer.name}",
            body,
            icon="document-save",
            # Neither of the phone's actions applies to a file on this disk:
            # there is nothing to dismiss on the phone and nothing to reply to.
            repliable=False,
            clearable=False,
        )

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
        #: menu, so the strip always says which page is showing.
        self.slot = self._add_tab("Overview")
        self.tabs.setTabVisible(self.slot, False)

        # Room for the label, the icon and the underline.
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
        for name, icon_name, _glyph, _page, _feature in PAGES:
            if name in PRIMARY or name == "Settings":
                continue
            # One rule for the menu, the strip and show_page, so a page that
            # cannot be opened is never offered.
            if not self._enabled(name):
                continue
            action = QAction(name, self.more_menu)
            icon = tinted_icon(themed_icon(icon_name), self.palette_tokens.text, 16)
            if not icon.isNull():
                action.setIcon(icon)
            action.triggered.connect(lambda _c=False, n=name: self.show_page(n))
            self.more_menu.addAction(action)
        self.more_button.setVisible(not self.more_menu.isEmpty())

    # -- navigation ----------------------------------------------------------

    def _page(self, name: str) -> QWidget:
        """The page called *name*, built now if it has not been yet."""
        index = self._page_index(name)
        if index in self._built:
            return self.stack.widget(index)
        self._built.add(index)
        page_class = next(p for n, _i, _g, p, _f in PAGES if n == name)
        page = page_class(self.hub, self.palette_tokens)
        placeholder = self.stack.widget(index)
        self.stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self.stack.insertWidget(index, page)
        if isinstance(page, SettingsPage):
            page.featuresChanged.connect(self._apply_feature_visibility)
            page.featuresChanged.connect(self.panel.apply_tiles)
            # Switching Bluetooth audio off takes its button with it.
            page.featuresChanged.connect(self.panel.refresh_header)
            page.featuresChanged.connect(self._apply_panel_width)
        if isinstance(page, HomePage):
            # Tiles hand off to their full page rather than duplicating it.
            page.openPage.connect(self.show_page)
        return page

    @property
    def settings_page(self) -> SettingsPage:
        return self._page("Settings")

    @property
    def audio_page(self) -> AudioPage:
        return self._page("Audio")

    def _page_index(self, name: str) -> int:
        return next(
            (i for i, (n, _i, _g, _p, _f) in enumerate(PAGES) if n == name), 0
        )

    def _enabled(self, name: str) -> bool:
        if name in IMPOSSIBLE:
            return False
        feature = next((f for n, _i, _g, _p, f in PAGES if n == name), None)
        return not feature or bool(getattr(self.hub.config.features, feature, True))

    def _apply_feature_visibility(self) -> None:
        """Hide what is switched off."""
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
            self.stack.setCurrentWidget(self._page(name))

    @property
    def current_page_name(self) -> str:
        return self.tabs.tabData(self.tabs.currentIndex()) or ""

    def _raise_window(self) -> None:
        """Bring the window forward, from wherever it was asked for."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_popup_opened(self, _notification_id: str) -> None:
        """A popup was clicked rather than answered: show the full list."""
        self._raise_window()
        self.show_page("Notifications")

    def _set_status(self, message: str) -> None:
        self.panel.set_status(message)

    # -- dropped files -------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if (
            event.mimeData().hasUrls()
            and self.hub.config.features.file_transfer
            and any(url.isLocalFile() for url in event.mimeData().urls())
        ):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [
            url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()
        ]
        if not paths:
            return
        event.acceptProposedAction()
        self.hub.send_files(paths)
        if self.hub.config.files.show_progress:
            self.show_page("Share")

    def _from_panel(self, name: str) -> None:
        """A panel switch whose work belongs to a page: run it, and show it."""
        if not self._enabled(name):
            self._set_status(f"{name} is switched off in Settings.")
            return
        self.show_page(name)
        toggle = getattr(self._page(name), "quick_toggle", None)
        if toggle is not None:
            toggle()

    # -- the rail's width ----------------------------------------------------

    def _display_mode(self) -> str:
        """Which saved width applies: a filled screen is not a window."""
        return (
            "width_fullscreen"
            if self.isFullScreen() or self.isMaximized() else "width"
        )

    def _apply_panel_width(self) -> None:
        """Give the rail the width saved for the mode the window is in."""
        cfg = self.hub.config.panel
        wanted = max(MIN_WIDTH, min(MAX_WIDTH, int(getattr(cfg, self._display_mode()))))
        if self.panel.width() == wanted:
            return
        rest = max(1, self.splitter.width() - wanted - self.splitter.handleWidth())
        self.splitter.setSizes([wanted, rest])

    def _remember_width(self) -> None:
        cfg = self.hub.config.panel
        setattr(cfg, self._display_mode(), self.panel.width())
        # Settings shows the same two numbers; a drag is the other way of
        # setting them, so keep the boxes honest -- where they exist yet.
        if self._page_index("Settings") in self._built:
            self.settings_page.reload_panel_widths()
        self._width_save.start()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().changeEvent(event)
        # Maximising or going full screen switches to that mode's saved width.
        if event.type() == event.Type.WindowStateChange:
            QTimer.singleShot(0, self._apply_panel_width)

    # -- tray ----------------------------------------------------------------

    def _build_tray(self) -> None:
        from . import appicon

        icon = QIcon.fromTheme(platform.APP_ID, appicon.icon())

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Tessera")

        menu = QMenu()
        show = QAction("Show Tessera", self)
        show.triggered.connect(self._restore)
        menu.addAction(show)

        mirror_action = QAction("Mirror phone screen", self)
        mirror_action.triggered.connect(self._mirror_from_tray)
        menu.addAction(mirror_action)

        clipboard_action = QAction("Copy phone clipboard", self)
        clipboard_action.triggered.connect(lambda: self.hub.clipboard.pull(force=True))
        menu.addAction(clipboard_action)
        self.hub.clipboard.pulled.connect(
            lambda _text, source: self._set_status(
                f"Copied the clipboard from {'the phone' if source == 'phone' else source}"
            )
        )
        self.hub.clipboard.pullFailed.connect(self._set_status)
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
        """Closing hides to the tray; quitting really quits."""
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
            self.tray.icon(),
            3000,
        )
