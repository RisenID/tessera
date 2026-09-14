"""Clipboard sharing between the desktop and the phone."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QClipboard, QGuiApplication

log = logging.getLogger(__name__)

#: Refuse to sync anything larger than this. Clipboards routinely hold whole
#: documents, and shipping those to a phone over Wi-Fi helps nobody.
MAX_LENGTH = 64 * 1024

MODE_OFF = "off"
MODE_PHONE_TO_DESKTOP = "phone_to_desktop"
MODE_DESKTOP_TO_PHONE = "desktop_to_phone"
MODE_TWO_WAY = "two_way"

MODE_LABELS = {
    MODE_OFF: "Off",
    MODE_PHONE_TO_DESKTOP: "Phone → Desktop",
    MODE_DESKTOP_TO_PHONE: "Desktop → Phone",
    MODE_TWO_WAY: "Keep both in sync",
}


class ClipboardSync(QObject):
    """Mirrors clipboard text in whichever direction is configured."""

    sent = Signal(str)        # text pushed to the phone
    received = Signal(str)    # text taken from the phone
    errorOccurred = Signal(str)
    #: A forced pull finished: the text and where it came from.
    pulled = Signal(str, str)
    pullFailed = Signal(str)

    #: Qt can emit several change signals for one copy; coalesce them.
    DEBOUNCE_MS = 250

    def __init__(self, client, config, parent: QObject | None = None, *, helper=None) -> None:
        super().__init__(parent)
        self._client = client
        #: The adb route (backends.clipboard_adb.AdbClipboard), used only when
        #: the phone cannot share its clipboard itself.
        self._helper = helper
        self._config = config
        if helper is not None:
            helper.changed.connect(self.apply_remote)
        self._applied: str | None = None      # last value we set locally
        self._last_sent: str | None = None
        #: When this computer's clipboard last changed, in ms since the epoch.
        self._changed_at = 0
        #: The adb helper's next answer is a forced pull.
        self._force_next = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._push_local)

        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.dataChanged.connect(self._on_local_change)

    # -- configuration -------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._config.mode

    def set_mode(self, mode: str) -> None:
        if mode not in MODE_LABELS:
            raise ValueError(f"unknown clipboard mode {mode!r}")
        self._config.mode = mode

    @property
    def route(self) -> str:
        """"phone" when the companion app shares the clipboard, "adb" when the
        helper does, "" when nothing can."""
        if self._client.connected and self._client.supports("clipboard"):
            return "phone"
        if self._helper is not None and self._helper.running:
            return "adb"
        return ""

    @property
    def wants_helper(self) -> bool:
        """Whether the adb route is worth running: sharing is on, and the
        phone is not already doing it without adb."""
        return (
            self._config.mode != MODE_OFF
            and not (self._client.connected and self._client.supports("clipboard"))
        )

    @property
    def _sends(self) -> bool:
        return self._config.mode in (MODE_DESKTOP_TO_PHONE, MODE_TWO_WAY)

    @property
    def _receives(self) -> bool:
        return self._config.mode in (MODE_PHONE_TO_DESKTOP, MODE_TWO_WAY)

    # -- desktop -> phone ----------------------------------------------------

    def state(self) -> tuple[str, int]:
        """This computer's clipboard text and when it last changed."""
        clipboard = QGuiApplication.clipboard()
        text = clipboard.text(QClipboard.Mode.Clipboard) if clipboard is not None else ""
        if len(text) > MAX_LENGTH:
            text = ""
        return text, self._changed_at

    def _on_local_change(self) -> None:
        self._changed_at = int(time.time() * 1000)
        if not self._sends:
            return
        self._debounce.start(self.DEBOUNCE_MS)

    def _push_local(self) -> None:
        clipboard = QGuiApplication.clipboard()
        route = self.route
        if clipboard is None or not route:
            return

        text = clipboard.text(QClipboard.Mode.Clipboard)
        if not text or text == self._applied or text == self._last_sent:
            # Either nothing to do, or this is the echo of a value the phone
            # just gave us.
            return
        if len(text) > MAX_LENGTH:
            log.debug("clipboard too large to sync (%d chars)", len(text))
            return

        self._last_sent = text
        if route == "phone":
            self._client.send({"t": "clipboard_set", "text": text})
        elif not self._helper.send(text):
            return
        self.sent.emit(text)

    # -- phone -> desktop ----------------------------------------------------

    def apply_remote(self, text: str, force: bool = False) -> None:
        """Put text from the phone on the local clipboard."""
        if self._force_next:
            self._force_next, force = False, True
            if text:
                self.pulled.emit(text, "phone")
        if (not self._receives and not force) or not text:
            return
        if len(text) > MAX_LENGTH:
            return

        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        if clipboard.text(QClipboard.Mode.Clipboard) == text:
            return

        self._applied = text
        clipboard.setText(text, QClipboard.Mode.Clipboard)
        self.received.emit(text)

    def pull(self, force: bool = False) -> None:
        """Copy the phone's clipboard here now; *force* ignores the sync direction."""
        route = self.route
        failed = self.pullFailed.emit if force else self.errorOccurred.emit
        if route == "adb":
            # The answer arrives as an ordinary change, through apply_remote.
            self._force_next = force
            self._helper.pull()
            return
        if not route:
            failed(
                "The phone's clipboard is out of reach: connect adb, start "
                "Shizuku, or switch on Tessera under the phone's Accessibility "
                "settings."
            )
            return
        self._client.request(
            {"t": "clipboard_get", "latest": True},
            lambda reply: self._on_pulled(reply, force),
        )

    def _on_pulled(self, reply: dict, force: bool) -> None:
        text = str(reply.get("text", ""))
        if reply.get("t") == "error":
            (self.pullFailed if force else self.errorOccurred).emit(
                str(reply.get("message") or "The phone refused.")
            )
            return
        if force and not text:
            self.pullFailed.emit("Nothing to copy: the phone's clipboard is empty.")
            return
        self.apply_remote(text, force=force)
        if force:
            self.pulled.emit(text, str(reply.get("from") or "phone"))
