"""Desktop notifications with something to press."""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import QObject, SLOT, Signal, Slot

from ..core.proc import Result, have, run, submit
from .dbus import HAVE_QTDBUS, QDBusMessage, session

log = logging.getLogger(__name__)

GDBUS = "gdbus"

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
INTERFACE = "org.freedesktop.Notifications"

#: The app name the server shows, and the desktop entry it matches us to for
#: the icon and the settings page.
APP_NAME = "Tessera"
DESKTOP_ENTRY = "dev.tessera.Tessera"

#: Action keys. "inline-reply" is the one KDE and GNOME both understand as
#: "draw a text box in the popup" rather than "draw a button".
REPLY = "inline-reply"
DISMISS = "dismiss"
OPEN = "default"

#: How long a popup stays up, in milliseconds. The server may override it.
TIMEOUT_MS = 8000

#: What send() returns when the server refused a repeat of a popup it is
#: still showing (Plasma rate-limits identical notifications). Nothing was
#: lost: the popup already up says the same.
REPEATED = -1


class Notifier(QObject):
    """The desktop's notification server, where there is one."""

    #: id of the notification, and what the user typed.
    replied = Signal(int, str)
    #: id, and the action key pressed.
    activated = Signal(int, str)
    closed = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._capabilities: list[str] = []
        self._connected = False
        self._bus = session()
        #: Sending needs gdbus; see _gdbus for why. Without it the caller falls
        #: back to the tray rather than sending nothing.
        connected = HAVE_QTDBUS and self._bus.isConnected()
        # No bus, nothing to send to: skip the PATH search (slow on Windows).
        self._can_send = connected and have(GDBUS)
        if connected:
            self._connect_signals()
            self._read_capabilities()
        if self._capabilities and not self._can_send:
            log.info("gdbus is missing, so notifications fall back to the tray")

    # -- what this desktop can do -------------------------------------------

    @property
    def available(self) -> bool:
        """Whether notifications can be sent this way at all."""
        return bool(self._capabilities) and self._can_send

    @property
    def can_reply(self) -> bool:
        """Whether a reply box can be drawn inside the popup."""
        return "inline-reply" in self._capabilities

    @property
    def can_act(self) -> bool:
        return "actions" in self._capabilities

    @property
    def capabilities(self) -> list[str]:
        return list(self._capabilities)

    def _read_capabilities(self) -> None:
        reply = self._call("GetCapabilities")
        if reply is None:
            return
        arguments = reply.arguments()
        if arguments and isinstance(arguments[0], list):
            self._capabilities = [str(entry) for entry in arguments[0]]
            log.info("notification server offers: %s", ", ".join(self._capabilities))

    def _connect_signals(self) -> None:
        # QDBusConnection binds by moc signature, not by Python callable, so
        # each of these has to match a @Slot-decorated method below exactly.
        for name, slot in (
            ("ActionInvoked", "_onAction(uint,QString)"),
            ("NotificationReplied", "_onReplied(uint,QString)"),
            ("NotificationClosed", "_onClosed(uint,uint)"),
            # KDE names the inline-reply signal differently from the action
            # one; a server that has neither simply never sends either.
        ):
            if not self._bus.connect(SERVICE, PATH, INTERFACE, name, self, SLOT(slot)):
                log.debug("could not subscribe to %s", name)
        self._connected = True

    # -- sending -------------------------------------------------------------

    def send(
        self,
        summary: str,
        body: str,
        *,
        icon: str = "",
        replace: int = 0,
        repliable: bool = False,
        clearable: bool = True,
        reply_placeholder: str = "Reply",
        urgent: bool = False,
    ) -> int:
        """Raise a notification. Returns the server's id, REPEATED, or 0."""
        if not self.available or not self._can_send:
            return 0

        actions: list[str] = []
        if self.can_act:
            if repliable and self.can_reply:
                # The key first, then the label the server shows on the send
                # button beside the box.
                actions += [REPLY, "Reply"]
            if clearable:
                actions += [DISMISS, "Dismiss on phone"]

        hints: dict[str, str] = {
            "desktop-entry": _variant(DESKTOP_ENTRY),
            "urgency": _variant(2 if urgent else 1, "byte"),
        }
        if repliable and self.can_reply:
            hints["x-kde-reply-placeholder-text"] = _variant(reply_placeholder)

        result = self._gdbus(
            "Notify",
            _string(APP_NAME),
            f"uint32 {max(0, int(replace))}",
            _string(icon),
            _string(summary),
            _string(body),
            _array(actions),
            _dictionary(hints),
            str(TIMEOUT_MS),
        )
        if result.ok:
            return _first_id(result.stdout)
        if "ExcessNotificationGeneration" in result.stderr:
            return REPEATED
        return 0

    def send_async(self, summary: str, body: str, on_done=None, **options) -> None:
        """send(), on a worker: gdbus is a process per notification."""
        if not self.available:
            if on_done is not None:
                on_done(0)
            return
        submit(lambda: self.send(summary, body, **options), on_done=on_done,
               on_error=lambda message: log.debug("notify: %s", message))

    def close(self, notification_id: int) -> None:
        if notification_id and self._can_send:
            submit(self._gdbus, "CloseNotification", f"uint32 {int(notification_id)}",
                   on_error=lambda message: log.debug("close: %s", message))

    # -- plumbing ------------------------------------------------------------

    def _gdbus(self, method: str, *args: str) -> Result:
        """Call the notification server through gdbus."""
        result = run(
            [
                GDBUS, "call", "--session",
                "--dest", SERVICE,
                "--object-path", PATH,
                "--method", f"{INTERFACE}.{method}",
                *args,
            ],
            timeout=5.0,
        )
        if not result.ok:
            log.debug("gdbus %s failed: %s", method, result.text.strip()[:200])
        return result

    def _call(self, method: str, *args: object) -> "QDBusMessage | None":
        if not HAVE_QTDBUS or not self._bus.isConnected():
            return None
        message = QDBusMessage.createMethodCall(SERVICE, PATH, INTERFACE, method)
        if args:
            message.setArguments(list(args))
        reply = self._bus.call(message, timeout=4000)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            log.debug("%s failed: %s", method, reply.errorMessage())
            return None
        return reply

    # Named for the moc signatures above rather than in this project's style:
    # QtDBus matches them by name and argument types.

    # "uint", not int: the ids are uint32 on the wire, and a slot registered as
    # (int,QString) is never matched -- the connection succeeds and then
    # nothing is ever delivered, which is a quiet way to lose every reply.

    @Slot("uint", str)
    def _onAction(self, notification_id: int, key: str) -> None:  # noqa: N802
        self.activated.emit(int(notification_id), str(key))

    @Slot("uint", str)
    def _onReplied(self, notification_id: int, text: str) -> None:  # noqa: N802
        self.replied.emit(int(notification_id), str(text))

    @Slot("uint", "uint")
    def _onClosed(self, notification_id: int, _reason: int) -> None:  # noqa: N802
        self.closed.emit(int(notification_id))


# -- GVariant text, which is what gdbus reads ---------------------------------


def _string(value: str) -> str:
    """A GVariant string literal: single-quoted, with the obvious escapes."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "")
    )
    return f"'{escaped}'"


def _array(values: list[str]) -> str:
    return "[" + ", ".join(_string(value) for value in values) + "]"


def _variant(value: object, kind: str = "") -> str:
    """A boxed value, which is what a{sv} holds."""
    if isinstance(value, str):
        return f"<{_string(value)}>"
    return f"<{kind + ' ' if kind else ''}{value}>"


def _dictionary(values: dict[str, str]) -> str:
    if not values:
        return "{}"
    body = ", ".join(f"{_string(key)}: {value}" for key, value in values.items())
    return "{" + body + "}"


def _first_id(output: str) -> int:
    """Pull the id out of gdbus's "(uint32 42,)"."""
    match = re.search(r"uint32\s+(\d+)", output)
    return int(match.group(1)) if match else 0


def available() -> bool:
    """Whether this desktop has a notification server at all."""
    if not HAVE_QTDBUS:
        return False
    bus = session()
    return bool(bus.isConnected())


# -- Windows -----------------------------------------------------------------
#
# No notification server on the session bus; Action Center toasts carry the
# same reply box and buttons, so the same class name stands in.
from ..core import platform as _platform                              # noqa: E402

if _platform.IS_WINDOWS:
    from .notify_win import WindowsNotifier as Notifier                # noqa: F811

    def available() -> bool:                                          # noqa: F811
        return Notifier().available
