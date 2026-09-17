"""Injecting pointer and keyboard input on Linux, through the RemoteDesktop portal.

Gio rather than QtDBus: the portal's state arguments are uint32, which PySide6
has no way to send, and Qt's event loop on Linux already runs GLib's.
"""

from __future__ import annotations

import collections
import logging
import secrets
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the system
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    HAVE_GIO = True
except (ImportError, ValueError):  # pragma: no cover
    Gio = GLib = None
    HAVE_GIO = False

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REMOTE = "org.freedesktop.portal.RemoteDesktop"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"

DEVICE_KEYBOARD, DEVICE_POINTER = 1, 2
#: Keep the permission until the user revokes it, so the dialog is seen once.
PERSIST_UNTIL_REVOKED = 2
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112

#: Milliseconds between keyboard events. KWin injects a keysym by borrowing a
#: keycode and remapping it; back-to-back press/release pairs race, and the
#: release can land on the next key's slot, so a key sticks (and autorepeats --
#: space, most of all) or reads as another. A short, even pace lets each one
#: settle. 5 ms is 100 keystrokes a second, well past any typing.
KEY_GAP_MS = 5

#: X keysyms for the names the phone sends.
KEYSYMS = {
    "enter": 0xFF0D, "backspace": 0xFF08, "tab": 0xFF09, "escape": 0xFF1B, "space": 0x20,
    "left": 0xFF51, "up": 0xFF52, "right": 0xFF53, "down": 0xFF54,
    "home": 0xFF50, "end": 0xFF57, "pageup": 0xFF55, "pagedown": 0xFF56, "delete": 0xFFFF,
    "f5": 0xFFC2, "f11": 0xFFC8,
    "play": 0x1008FF14, "next": 0x1008FF17, "previous": 0x1008FF16,
    "volumeup": 0x1008FF13, "volumedown": 0x1008FF11, "mute": 0x1008FF12,
}


def keysym(char: str) -> int:
    """The keysym for one character: Latin-1 as itself, the rest by code point."""
    code = ord(char)
    if 0x20 <= code <= 0xFF:
        return code
    if char == "\n":
        return KEYSYMS["enter"]
    if char == "\t":
        return KEYSYMS["tab"]
    return 0x01000000 | code


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def available() -> bool:
    """Whether a RemoteDesktop portal answers on the session bus."""
    if not HAVE_GIO:
        return False
    try:
        _bus().call_sync(
            PORTAL, PORTAL_PATH, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (REMOTE, "version")), None, Gio.DBusCallFlags.NONE, 2000, None,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - GLib.Error, or no bus at all
        log.debug("no RemoteDesktop portal: %s", exc)
        return False


class PortalInput(QObject):
    """One portal session, opened on first use and kept for the run."""

    #: The permission was granted (True) or refused, with a reason.
    ready = Signal(bool, str)

    def __init__(self, restore_token: str = "", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._connection = None
        self._session_path = ""
        self._starting = False
        self._restore_token = restore_token
        #: Told when a new restore token arrives, so it can be saved.
        self.on_token: Callable[[str], None] | None = None
        self._steps: list[Callable[[dict], None]] = []
        self._subscriptions: list[int] = []
        #: Keyboard press/release pairs waiting their turn, paced by _drain_keys.
        self._keys: collections.deque[tuple[int, int]] = collections.deque()
        self._key_pump = 0

    def available(self) -> bool:
        return available()

    @property
    def running(self) -> bool:
        return bool(self._session_path)

    # -- opening the session -----------------------------------------------------

    def start(self, on_ready: Callable[[bool, str], None] | None = None) -> None:
        if on_ready is not None:
            self.ready.connect(on_ready)
        if self._session_path or self._starting:
            return
        if not HAVE_GIO:
            self.ready.emit(False, "Controlling this desktop needs python3-gobject, which is not installed.")
            return
        try:
            self._connection = _bus()
        except Exception as exc:  # noqa: BLE001
            self.ready.emit(False, f"No session bus: {exc}")
            return
        self._starting = True
        self._steps = [self._on_created, self._on_selected, self._on_started]
        self._request("CreateSession", "(a{sv})", (
            {"session_handle_token": GLib.Variant("s", "tessera_" + secrets.token_hex(4))},
        ))

    def _request(self, method: str, signature: str, arguments: tuple) -> None:
        """A portal call whose answer comes as a Response signal on a request object."""
        connection = self._connection
        token = "tessera_" + secrets.token_hex(4)
        sender = connection.get_unique_name().lstrip(":").replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        self._subscriptions.append(connection.signal_subscribe(
            PORTAL, REQUEST, "Response", path, None, Gio.DBusSignalFlags.NONE, self._on_response,
        ))
        options = dict(arguments[-1])
        options["handle_token"] = GLib.Variant("s", token)
        arguments = (*arguments[:-1], options)
        try:
            connection.call_sync(PORTAL, PORTAL_PATH, REMOTE, method, GLib.Variant(signature, arguments),
                                 None, Gio.DBusCallFlags.NONE, 5000, None)
        except Exception as exc:  # noqa: BLE001
            self._fail(f"The portal refused {method}: {exc}")

    def _on_response(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        for subscription in self._subscriptions:
            self._connection.signal_unsubscribe(subscription)
        self._subscriptions = []
        if not self._steps:
            return
        code, results = parameters.unpack()
        step = self._steps.pop(0)
        if int(code) != 0:
            self._fail("The desktop did not allow Tessera to control the pointer and keyboard."
                       if int(code) == 1 else "The portal request failed.")
            return
        step(dict(results))

    def _on_created(self, results: dict) -> None:
        path = str(results.get("session_handle", ""))
        if not path:
            self._fail("The portal gave no session.")
            return
        self._session_path = path
        self._connection.signal_subscribe(
            PORTAL, SESSION, "Closed", path, None, Gio.DBusSignalFlags.NONE, self._on_closed,
        )
        options = {
            "types": GLib.Variant("u", DEVICE_KEYBOARD | DEVICE_POINTER),
            "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
        }
        if self._restore_token:
            options["restore_token"] = GLib.Variant("s", self._restore_token)
        self._request("SelectDevices", "(oa{sv})", (path, options))

    def _on_selected(self, _results: dict) -> None:
        self._request("Start", "(osa{sv})", (self._session_path, "", {}))

    def _on_started(self, results: dict) -> None:
        self._starting = False
        token = str(results.get("restore_token", "") or "")
        if token and token != self._restore_token:
            self._restore_token = token
            if self.on_token is not None:
                self.on_token(token)
        log.info("remote input: portal session ready")
        self.ready.emit(True, "")

    def _on_closed(self, *_args) -> None:
        log.info("remote input: the portal closed the session")
        self._session_path = ""
        self._starting = False

    def _fail(self, message: str) -> None:
        log.warning("remote input: %s", message)
        self._session_path = ""
        self._starting = False
        self._steps = []
        self.ready.emit(False, message)

    def stop(self) -> None:
        self._keys.clear()
        if self._key_pump:
            GLib.source_remove(self._key_pump)
            self._key_pump = 0
        path, self._session_path = self._session_path, ""
        if not path or self._connection is None:
            return
        try:
            self._connection.call_sync(PORTAL, path, SESSION, "Close", None, None,
                                       Gio.DBusCallFlags.NONE, 2000, None)
        except Exception as exc:  # noqa: BLE001
            log.debug("closing the portal session: %s", exc)

    # -- events ----------------------------------------------------------------

    def _notify(self, method: str, signature: str, *arguments) -> None:
        if not self._session_path or self._connection is None:
            return
        # No reply awaited: a pointer stream must not wait on the bus.
        self._connection.call(
            PORTAL, PORTAL_PATH, REMOTE, method,
            GLib.Variant(signature, (self._session_path, {}, *arguments)),
            None, Gio.DBusCallFlags.NONE, 2000, None, lambda *_a: None,
        )

    def move(self, dx: float, dy: float) -> None:
        self._notify("NotifyPointerMotion", "(oa{sv}dd)", float(dx), float(dy))

    def button(self, name: str, down: bool) -> None:
        code = {"left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE}.get(name)
        if code is not None:
            self._notify("NotifyPointerButton", "(oa{sv}iu)", int(code), 1 if down else 0)

    def click(self, name: str = "left") -> None:
        self.button(name, True)
        self.button(name, False)

    def scroll(self, dx: float, dy: float) -> None:
        self._notify("NotifyPointerAxis", "(oa{sv}dd)", float(dx), float(dy))

    def key(self, name: str) -> None:
        code = KEYSYMS.get(name.lower())
        if code is not None:
            self._tap(code)

    def text(self, text: str) -> None:
        for char in text:
            self._tap(keysym(char))

    def _tap(self, code: int) -> None:
        """Queue one keystroke; the drain paces press and release apart."""
        self._keys.append((int(code), 1))
        self._keys.append((int(code), 0))
        if not self._key_pump and self._session_path:
            # Send the first at once, then pace the rest.
            self._drain_keys()
            if self._keys:
                self._key_pump = GLib.timeout_add(KEY_GAP_MS, self._drain_keys)

    def _drain_keys(self) -> bool:
        if not self._keys or not self._session_path:
            self._key_pump = 0
            return False
        code, state = self._keys.popleft()
        self._notify("NotifyKeyboardKeysym", "(oa{sv}iu)", int(code), int(state))
        if self._keys:
            return True
        self._key_pump = 0
        return False
