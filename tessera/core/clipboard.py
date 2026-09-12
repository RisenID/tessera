"""Clipboard sharing between the desktop and the phone.

The hard part is not moving the text but stopping it bouncing: applying a value
received from the phone changes the local clipboard, which would otherwise be
read as a local change and sent straight back. Every value applied from the
other side is recorded first, and echoes of it are ignored.
"""

from __future__ import annotations

import logging

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

    #: Qt can emit several change signals for one copy; coalesce them.
    DEBOUNCE_MS = 250

    def __init__(self, client, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._config = config
        self._applied: str | None = None      # last value we set locally
        self._last_sent: str | None = None

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
    def _sends(self) -> bool:
        return self._config.mode in (MODE_DESKTOP_TO_PHONE, MODE_TWO_WAY)

    @property
    def _receives(self) -> bool:
        return self._config.mode in (MODE_PHONE_TO_DESKTOP, MODE_TWO_WAY)

    # -- desktop -> phone ----------------------------------------------------

    def _on_local_change(self) -> None:
        if not self._sends:
            return
        self._debounce.start(self.DEBOUNCE_MS)

    def _push_local(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None or not self._client.connected:
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
        self._client.send({"t": "clipboard_set", "text": text})
        self.sent.emit(text)

    # -- phone -> desktop ----------------------------------------------------

    def apply_remote(self, text: str) -> None:
        """Put text from the phone on the local clipboard."""
        if not self._receives or not text:
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

    def pull(self) -> None:
        """Ask the phone for its clipboard, for an explicit 'paste from phone'."""
        if not self._client.connected:
            self.errorOccurred.emit("No phone connected.")
            return
        self._client.request(
            {"t": "clipboard_get"},
            lambda reply: self.apply_remote(str(reply.get("text", ""))),
        )
