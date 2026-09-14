"""Do Not Disturb synchronisation between the phone and Plasma."""

from __future__ import annotations

import logging
from enum import IntEnum
from typing import Any

from PySide6.QtCore import SLOT, QObject, QTimer, Signal, Slot

from .dbus import QDBusMessage, QDBusVariant, session

from ..core.config import DndConfig
from ..core.proc import have, run, submit
from . import silence
from . import adb

log = logging.getLogger(__name__)

NOTIFY_SERVICE = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
NOTIFY_IFACE = "org.freedesktop.Notifications"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

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


class ZenMode(IntEnum):
    """Android's `global.zen_mode` values."""

    OFF = 0
    PRIORITY = 1
    TOTAL_SILENCE = 2
    ALARMS_ONLY = 3

    @property
    def label(self) -> str:
        return {
            ZenMode.OFF: "Off",
            ZenMode.PRIORITY: "Priority only",
            ZenMode.TOTAL_SILENCE: "Total silence",
            ZenMode.ALARMS_ONLY: "Alarms only",
        }[self]

    @property
    def adb_name(self) -> str:
        """The keyword `cmd notification set_dnd` expects."""
        return {
            ZenMode.OFF: "off",
            ZenMode.PRIORITY: "priority",
            ZenMode.TOTAL_SILENCE: "none",
            ZenMode.ALARMS_ONLY: "alarms",
        }[self]


def read_phone_zen(serial: str) -> ZenMode:
    """Current DND state on the phone. Blocking; run in a worker."""
    raw = adb.shell(serial, "settings get global zen_mode", timeout=10.0)
    raw = raw.strip().lower()
    if raw in ("", "null"):
        return ZenMode.OFF
    try:
        return ZenMode(int(raw))
    except ValueError:
        log.debug("unrecognised zen_mode %r; assuming off", raw)
        return ZenMode.OFF


def write_phone_zen(serial: str, mode: ZenMode) -> None:
    """Set DND on the phone. Blocking; run in a worker."""
    ok, out = adb.try_shell(serial, f"cmd notification set_dnd {mode.adb_name}", timeout=12.0)
    if ok and "error" not in out.lower() and "unknown command" not in out.lower():
        return
    # Older or vendor-modified ROMs may not implement the notification shell
    # command; adb shell holds WRITE_SECURE_SETTINGS, so poke the setting itself.
    log.debug("set_dnd unavailable (%s); falling back to settings put", out)
    adb.shell(serial, f"settings put global zen_mode {int(mode)}", timeout=10.0)


class DndSync(QObject):
    """Keeps phone and desktop DND in step, in whichever direction is configured."""

    phoneStateChanged = Signal(object)     # ZenMode
    desktopStateChanged = Signal(bool)
    statusChanged = Signal(str)
    errorOccurred = Signal(str)

    def __init__(self, config: DndConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._serial = ""
        self._bus = session()

        self._cookie: int | None = None        # our own inhibition, if any
        #: Whether this notification server implements Plasma's Inhibit, which
        #: is not part of the notification specification. Probed once, lazily.
        self._inhibits: bool | None = None
        #: The desktop's own switch, for the desktops that have no Inhibit.
        self._silencer: silence.Silencer | None = None
        self._looked_for_silencer = False
        #: True when we silenced the desktop through that switch, so that
        #: releasing undoes only what we did -- the cookie's job on Plasma.
        self._held = False
        self._phone: ZenMode | None = None
        self._desktop: bool | None = None
        self._busy = False                     # a phone write is in flight
        #: True while the companion app is reporting the phone's state,
        #: which makes the adb poll redundant. See set_pushed.
        self._pushed = False
        #: A weakref.WeakMethod to a function that sends a desktop change to
        #: the phone another way (the companion), returning False when it cannot.
        self.push_via = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)

        # Plasma reports its own DND toggle through the standard properties
        # signal, which is how a change made in the tray reaches us.
        if not self._bus.connect(
            NOTIFY_SERVICE,
            NOTIFY_PATH,
            PROPS_IFACE,
            "PropertiesChanged",
            self,
            SLOT("_onDesktopProps(QString,QVariantMap,QStringList)"),
        ):
            log.warning("could not watch desktop Do Not Disturb state")

    # -- state ---------------------------------------------------------------

    @property
    def phone_state(self) -> ZenMode | None:
        return self._phone

    @property
    def desktop_state(self) -> bool | None:
        return self._desktop

    @property
    def mode(self) -> str:
        return self._config.mode

    def set_serial(self, serial: str) -> None:
        if serial != self._serial:
            self._serial = serial
            self._phone = None       # force a fresh read against the new device

    def set_pushed(self, pushed: bool) -> None:
        """Whether something else is reporting the phone's state as it changes."""
        if pushed == self._pushed:
            return
        self._pushed = pushed
        if not pushed:
            self._poll()

    def set_mode(self, mode: str) -> None:
        if mode not in MODE_LABELS:
            raise ValueError(f"unknown DND mode {mode!r}")
        self._config.mode = mode
        self.statusChanged.emit(f"Sync mode: {MODE_LABELS[mode]}")
        if mode == MODE_OFF:
            self.release_desktop()
        else:
            self._poll()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        interval = max(2, int(self._config.poll_seconds)) * 1000
        self._timer.start(interval)
        self._desktop = self._read_desktop()
        self._poll()

    def stop(self) -> None:
        self._timer.stop()
        self.release_desktop()

    # -- desktop side --------------------------------------------------------

    def _notify_call(self, method: str, *args: Any) -> Any:
        msg = QDBusMessage.createMethodCall(NOTIFY_SERVICE, NOTIFY_PATH, NOTIFY_IFACE, method)
        if args:
            msg.setArguments(list(args))
        reply = self._bus.call(msg, timeout=5000)
        if reply.type() == QDBusMessage.ErrorMessage:
            raise RuntimeError(reply.errorMessage())
        values = reply.arguments()
        return values[0] if values else None

    def _read_inhibited(self) -> bool | None:
        """The Inhibited property, or None when the server has no such thing."""
        if not self._bus.isConnected():
            return None
        if not self._bus.interface().isServiceRegistered(NOTIFY_SERVICE).value():
            return None
        msg = QDBusMessage.createMethodCall(NOTIFY_SERVICE, NOTIFY_PATH, PROPS_IFACE, "Get")
        msg.setArguments([NOTIFY_IFACE, "Inhibited"])
        reply = self._bus.call(msg, timeout=5000)
        if reply.type() == QDBusMessage.ErrorMessage:
            return None
        values = reply.arguments()
        value = values[0] if values else False
        while isinstance(value, QDBusVariant):
            value = value.variant()
        return bool(value)

    def _inhibit_supported(self) -> bool:
        if self._inhibits is None:
            self._inhibits = self._read_inhibited() is not None
        return bool(self._inhibits)

    @property
    def silencer(self) -> "silence.Silencer | None":
        """This desktop's own notification switch, found once."""
        if not self._looked_for_silencer:
            self._looked_for_silencer = True
            self._silencer = silence.detect()
        return self._silencer

    def desktop_mechanism(self) -> str:
        """What Tessera would drive to silence this desktop, for a message."""
        if self._inhibit_supported():
            return "this desktop's notification server"
        found = self.silencer
        return found.desktop if found else ""

    def desktop_supported(self) -> bool:
        return bool(self.desktop_mechanism())

    def _read_desktop(self) -> bool:
        """Whether desktop notifications are currently silenced."""
        if self._inhibit_supported():
            return bool(self._read_inhibited())
        found = self.silencer
        return found.silenced() if found else False

    def _uninhibit(self, cookie: int) -> bool:
        """Drop inhibition *cookie*."""
        if not have("gdbus"):
            return False
        result = run(
            [
                "gdbus", "call", "--session",
                "-d", NOTIFY_SERVICE,
                "-o", NOTIFY_PATH,
                "-m", f"{NOTIFY_IFACE}.UnInhibit", str(cookie),
            ],
            timeout=5.0,
        )
        if not result.ok:
            log.warning("could not release desktop DND: %s", result.text)
        return result.ok

    def hold_desktop(self, reason: str) -> None:
        """Silence the desktop, if we have not already."""
        if self._cookie is not None or self._held:
            return

        if self._inhibit_supported():
            try:
                cookie = self._notify_call("Inhibit", "tessera", reason, {})
            except RuntimeError as exc:
                self.errorOccurred.emit(f"Could not enable desktop Do Not Disturb: {exc}")
                return
            self._cookie = int(cookie) if cookie is not None else None
            self._note_desktop(True)
            return

        found = self.silencer
        if found is None:
            self.errorOccurred.emit(
                "This desktop offers no way to silence its notifications that "
                "Tessera knows how to drive, so Do Not Disturb can only be "
                "mirrored the other way, from the desktop to the phone."
            )
            return
        try:
            found.set(True)
        except RuntimeError as exc:
            self.errorOccurred.emit(f"Could not silence {found.desktop}: {exc}")
            return
        self._held = True
        self._note_desktop(True)

    def release_desktop(self) -> None:
        """Undo only our own silencing, leaving anything the user set alone."""
        if self._cookie is not None:
            self._uninhibit(self._cookie)
            self._cookie = None
        elif self._held:
            found = self.silencer
            if found is not None:
                try:
                    found.set(False)
                except RuntimeError as exc:
                    log.warning("could not unsilence %s: %s", found.desktop, exc)
            self._held = False
        else:
            return
        self._note_desktop(self._read_desktop())

    def _note_desktop(self, state: bool) -> None:
        """Record a desktop state we caused, so it is not read back as a change."""
        if state != self._desktop:
            self._desktop = state
            self.desktopStateChanged.emit(state)

    @Slot(str, "QVariantMap", "QStringList")
    def _onDesktopProps(self, iface: str, changed: dict, _invalidated: list) -> None:
        if iface != NOTIFY_IFACE or "Inhibited" not in changed:
            return
        value = changed["Inhibited"]
        while isinstance(value, QDBusVariant):
            value = value.variant()
        self._desktop_now(bool(value))

    def _desktop_now(self, state: bool) -> None:
        """Act on the desktop's silence state, wherever it was read from."""
        if state == self._desktop:
            return
        previous, self._desktop = self._desktop, state
        self.desktopStateChanged.emit(state)
        # Only a change we did not cause should travel to the phone.
        if previous is not None and self._cookie is None and not self._held:
            self._push_to_phone(state)

    def _watch_silencer(self) -> None:
        """Notice the user's own toggle, on a desktop with no change signal."""
        if self._inhibit_supported() or self._config.mode == MODE_OFF:
            return
        if self._config.mode not in (MODE_DESKTOP_TO_PHONE, MODE_TWO_WAY):
            return
        found = self.silencer
        if found is not None:
            self._desktop_now(found.silenced())

    # -- phone side ----------------------------------------------------------

    @Slot()
    def _poll(self) -> None:
        # Runs whatever the phone side is doing: a desktop without a change
        # signal has to be looked at, and this is the timer that already ticks.
        self._watch_silencer()

        if self._config.mode == MODE_OFF or not self._serial or self._busy:
            return
        if self._pushed:
            return
        submit(
            read_phone_zen,
            self._serial,
            on_done=self._on_phone_read,
            on_error=lambda msg: log.debug("zen poll failed: %s", msg),
        )

    def _on_phone_read(self, zen: ZenMode) -> None:
        if zen == self._phone:
            return
        first_read = self._phone is None
        self._phone = zen
        self.phoneStateChanged.emit(zen)
        # The first successful read establishes a baseline; acting on it would
        # clobber whichever side the user last set by hand.
        if first_read and self._config.mode == MODE_TWO_WAY:
            return
        if self._config.mode in (MODE_PHONE_TO_DESKTOP, MODE_TWO_WAY):
            self._apply_to_desktop(zen)

    def _apply_to_desktop(self, zen: ZenMode) -> None:
        if not self._is_dnd(zen):
            self.release_desktop()
        else:
            self.hold_desktop(f"{zen.label} on your phone")

    def _is_dnd(self, zen: ZenMode) -> bool:
        if zen == ZenMode.OFF:
            return False
        if zen == ZenMode.TOTAL_SILENCE:
            return True
        return self._config.treat_partial_as_dnd

    def _push_to_phone(self, desktop_on: bool) -> None:
        if self._config.mode not in (MODE_DESKTOP_TO_PHONE, MODE_TWO_WAY):
            return
        target = ZenMode.PRIORITY if desktop_on else ZenMode.OFF
        push = self.push_via() if self.push_via is not None else None
        if push is not None and push(target):
            return
        if not self._serial:
            return
        if target == self._phone:
            return
        self._busy = True

        def done(_result: Any) -> None:
            self._busy = False
            self._phone = target
            self.phoneStateChanged.emit(target)
            self.statusChanged.emit(
                f"Phone Do Not Disturb turned {'on' if desktop_on else 'off'}"
            )

        def failed(msg: str) -> None:
            self._busy = False
            self.errorOccurred.emit(f"Could not change Do Not Disturb on the phone: {msg}")

        submit(write_phone_zen, self._serial, target, on_done=done, on_error=failed)

    def set_phone(self, zen: ZenMode) -> None:
        """Explicitly set the phone's DND state (from the UI)."""
        if not self._serial:
            self.errorOccurred.emit("No phone connected over adb.")
            return
        self._busy = True

        def done(_result: Any) -> None:
            self._busy = False
            self._phone = zen
            self.phoneStateChanged.emit(zen)
            self.statusChanged.emit(f"Phone set to {zen.label}")
            if self._config.mode in (MODE_PHONE_TO_DESKTOP, MODE_TWO_WAY):
                self._apply_to_desktop(zen)

        def failed(msg: str) -> None:
            self._busy = False
            self.errorOccurred.emit(msg)

        submit(write_phone_zen, self._serial, zen, on_done=done, on_error=failed)
