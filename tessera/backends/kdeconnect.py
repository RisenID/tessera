"""KDE Connect bridge."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import SLOT, QObject, Signal, Slot

from ..core import platform
from .dbus import (
    HAVE_QTDBUS,
    QDBusMessage,
    QDBusServiceWatcher,
    QDBusVariant,
    session,
)

log = logging.getLogger(__name__)

SERVICE = "org.kde.kdeconnect"
DAEMON_PATH = "/modules/kdeconnect"
DAEMON_IFACE = "org.kde.kdeconnect.daemon"
DEVICE_IFACE = "org.kde.kdeconnect.device"
NOTIFICATIONS_IFACE = "org.kde.kdeconnect.device.notifications"
NOTIFICATION_IFACE = "org.kde.kdeconnect.device.notifications.notification"
BATTERY_IFACE = "org.kde.kdeconnect.device.battery"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

#: Signals we mirror from the selected device. Each entry is
#: (interface, D-Bus signal name, Qt slot signature) -- the slot signature must
#: match a @Slot-decorated method on KdeConnect, because QDBusConnection.connect
#: binds by moc signature rather than by Python callable.
_DEVICE_SIGNALS: tuple[tuple[str, str, str], ...] = (
    (DEVICE_IFACE, "reachableChanged", "_onReachableChanged(bool)"),
    (DEVICE_IFACE, "nameChanged", "_onNameChanged(QString)"),
    (DEVICE_IFACE, "pluginsChanged", "_onPluginsChanged()"),
    (NOTIFICATIONS_IFACE, "notificationPosted", "_onNotificationPosted(QString)"),
    (NOTIFICATIONS_IFACE, "notificationRemoved", "_onNotificationRemoved(QString)"),
    (NOTIFICATIONS_IFACE, "allNotificationsRemoved", "_onNotificationsCleared()"),
    (BATTERY_IFACE, "refreshed", "_onBatteryRefreshed(bool,int)"),
)

#: Daemon-level signals, same shape as above.
_DAEMON_SIGNALS: tuple[tuple[str, str, str], ...] = (
    (DAEMON_IFACE, "deviceListChanged", "_onDeviceListChanged()"),
    (DAEMON_IFACE, "deviceAdded", "_onDeviceIdChanged(QString)"),
    (DAEMON_IFACE, "deviceRemoved", "_onDeviceIdChanged(QString)"),
    (DAEMON_IFACE, "deviceVisibilityChanged", "_onVisibilityChanged(QString,bool)"),
)


def device_path(device_id: str) -> str:
    return f"{DAEMON_PATH}/devices/{device_id}"


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    reachable: bool
    paired: bool
    type: str = "phone"

    @property
    def label(self) -> str:
        return self.name or self.id


@dataclass
class Notification:
    """One notification mirrored from the phone."""

    id: str
    app_name: str = ""
    title: str = ""
    text: str = ""
    ticker: str = ""
    icon_path: str = ""
    dismissable: bool = False
    silent: bool = False
    reply_id: str = ""
    received_at: float = field(default_factory=time.time)

    @property
    def repliable(self) -> bool:
        return bool(self.reply_id)

    @property
    def summary(self) -> str:
        """Best single-line description, however sparse the payload is."""
        if self.title and self.text:
            return f"{self.title}: {self.text}"
        return self.title or self.text or self.ticker or self.app_name


def _unwrap(value: Any) -> Any:
    """Strip QDBusVariant wrappers that org.freedesktop.DBus.Properties.Get adds."""
    while isinstance(value, QDBusVariant):
        value = value.variant()
    return value


class KdeConnectError(RuntimeError):
    pass


class KdeConnect(QObject):
    """Session-bus client for kdeconnectd, scoped to one selected device."""

    serviceAvailabilityChanged = Signal(bool)
    deviceListChanged = Signal()
    deviceStateChanged = Signal()          # selected device reachability/name
    notificationPosted = Signal(object)    # Notification
    notificationRemoved = Signal(str)
    notificationsCleared = Signal()
    batteryChanged = Signal(int, bool)     # charge %, charging

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = session()
        self._device_id = ""
        self._subscribed_path = ""
        #: Nothing to watch and nothing to call without a bus, which is the
        #: normal state on Windows rather than an error.
        self._usable = HAVE_QTDBUS and platform.supported("kdeconnect")
        if not self._usable:
            log.info("KDE Connect is not available on this platform")
            return
        if not self._bus.isConnected():
            log.error("no session bus; KDE Connect features are unavailable")

        self._watcher = QDBusServiceWatcher(
            SERVICE,
            self._bus,
            QDBusServiceWatcher.WatchForRegistration
            | QDBusServiceWatcher.WatchForUnregistration,
            self,
        )
        self._watcher.serviceRegistered.connect(self._on_service_up)
        self._watcher.serviceUnregistered.connect(self._on_service_down)

        self._connect_daemon_signals()

    # -- low level -----------------------------------------------------------

    def _call(
        self, path: str, iface: str, method: str, *args: Any, quiet: bool = False
    ) -> Any:
        """Make a blocking D-Bus call, returning the first reply argument."""
        msg = QDBusMessage.createMethodCall(SERVICE, path, iface, method)
        if args:
            msg.setArguments(list(args))
        reply = self._bus.call(msg, timeout=8000)
        if reply.type() == QDBusMessage.ErrorMessage:
            if not quiet:
                log.debug("dbus %s.%s failed: %s", iface, method, reply.errorMessage())
            raise KdeConnectError(reply.errorMessage() or "D-Bus call failed")
        values = reply.arguments()
        return _unwrap(values[0]) if values else None

    def _prop(self, path: str, iface: str, name: str, default: Any = None) -> Any:
        try:
            value = _unwrap(self._call(path, PROPS_IFACE, "Get", iface, name, quiet=True))
        except KdeConnectError:
            return default
        return default if value is None else value

    def _subscribe(self, path: str, iface: str, signal: str, slot_sig: str) -> None:
        if not self._bus.connect(SERVICE, path, iface, signal, self, SLOT(slot_sig)):
            log.debug("could not subscribe to %s.%s on %s", iface, signal, path)

    def _unsubscribe(self, path: str, iface: str, signal: str, slot_sig: str) -> None:
        self._bus.disconnect(SERVICE, path, iface, signal, self, SLOT(slot_sig))

    # -- service lifecycle ---------------------------------------------------

    @property
    def available(self) -> bool:
        if not self._usable or not self._bus.isConnected():
            return False
        return self._bus.interface().isServiceRegistered(SERVICE).value()

    def _on_service_up(self, _name: str) -> None:
        log.info("kdeconnectd appeared")
        self._connect_daemon_signals()
        if self._device_id:
            self.select_device(self._device_id, force=True)
        self.serviceAvailabilityChanged.emit(True)
        self.deviceListChanged.emit()

    def _on_service_down(self, _name: str) -> None:
        log.warning("kdeconnectd went away")
        self._subscribed_path = ""
        self.serviceAvailabilityChanged.emit(False)
        self.deviceListChanged.emit()

    def _connect_daemon_signals(self) -> None:
        for iface, signal, slot_sig in _DAEMON_SIGNALS:
            self._subscribe(DAEMON_PATH, iface, signal, slot_sig)

    @Slot()
    def _onDeviceListChanged(self) -> None:
        self.deviceListChanged.emit()

    @Slot(str)
    def _onDeviceIdChanged(self, _device_id: str) -> None:
        self.deviceListChanged.emit()

    @Slot(str, bool)
    def _onVisibilityChanged(self, device_id: str, _visible: bool) -> None:
        if device_id == self._device_id:
            self.deviceStateChanged.emit()
        self.deviceListChanged.emit()

    def start_discovery(self) -> None:
        """Nudge the daemon to re-announce itself on the network."""
        try:
            self._call(DAEMON_PATH, DAEMON_IFACE, "forceOnNetworkChange")
        except KdeConnectError as exc:
            log.debug("discovery nudge failed: %s", exc)

    # -- devices -------------------------------------------------------------

    def devices(self, only_reachable: bool = False, only_paired: bool = True) -> list[Device]:
        try:
            ids = self._call(DAEMON_PATH, DAEMON_IFACE, "devices", only_reachable, only_paired)
        except KdeConnectError:
            return []
        return [self.device_info(i) for i in (ids or [])]

    def device_info(self, device_id: str) -> Device:
        path = device_path(device_id)
        return Device(
            id=device_id,
            name=self._prop(path, DEVICE_IFACE, "name", "") or "",
            reachable=bool(self._prop(path, DEVICE_IFACE, "isReachable", False)),
            paired=bool(self._prop(path, DEVICE_IFACE, "isPaired", False)),
            type=self._prop(path, DEVICE_IFACE, "type", "phone") or "phone",
        )

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def selected(self) -> Device | None:
        if not self._device_id:
            return None
        return self.device_info(self._device_id)

    def select_device(self, device_id: str, force: bool = False) -> None:
        """Point the bridge at *device_id*, moving all signal subscriptions."""
        if device_id == self._device_id and not force:
            return
        self._teardown_device_signals()
        self._device_id = device_id
        if device_id:
            self._setup_device_signals(device_path(device_id))
        self.deviceStateChanged.emit()

    def _setup_device_signals(self, path: str) -> None:
        self._subscribed_path = path
        for iface, signal, slot_sig in _DEVICE_SIGNALS:
            self._subscribe(path, iface, signal, slot_sig)

    def _teardown_device_signals(self) -> None:
        path = self._subscribed_path
        if not path:
            return
        for iface, signal, slot_sig in _DEVICE_SIGNALS:
            self._unsubscribe(path, iface, signal, slot_sig)
        self._subscribed_path = ""

    @Slot(bool)
    def _onReachableChanged(self, _reachable: bool) -> None:
        self.deviceStateChanged.emit()

    @Slot(str)
    def _onNameChanged(self, _name: str) -> None:
        self.deviceStateChanged.emit()

    @Slot()
    def _onPluginsChanged(self) -> None:
        self.deviceStateChanged.emit()

    # -- notifications -------------------------------------------------------

    def active_notifications(self) -> list[Notification]:
        if not self._device_id:
            return []
        path = device_path(self._device_id)
        try:
            ids = self._call(path, NOTIFICATIONS_IFACE, "activeNotifications")
        except KdeConnectError:
            return []
        out = []
        for nid in ids or []:
            note = self.notification(nid)
            if note is not None:
                out.append(note)
        return out

    def notification(self, notification_id: str) -> Notification | None:
        if not self._device_id:
            return None
        path = f"{device_path(self._device_id)}/notifications/{notification_id}"
        app_name = self._prop(path, NOTIFICATION_IFACE, "appName")
        if app_name is None:
            # The notification was dismissed between the signal and this read.
            return None
        return Notification(
            id=notification_id,
            app_name=app_name or "",
            title=self._prop(path, NOTIFICATION_IFACE, "title", "") or "",
            text=self._prop(path, NOTIFICATION_IFACE, "text", "") or "",
            ticker=self._prop(path, NOTIFICATION_IFACE, "ticker", "") or "",
            icon_path=self._prop(path, NOTIFICATION_IFACE, "iconPath", "") or "",
            dismissable=bool(self._prop(path, NOTIFICATION_IFACE, "dismissable", False)),
            silent=bool(self._prop(path, NOTIFICATION_IFACE, "silent", False)),
            reply_id=self._prop(path, NOTIFICATION_IFACE, "replyId", "") or "",
        )

    def dismiss(self, notification_id: str) -> None:
        path = f"{device_path(self._device_id)}/notifications/{notification_id}"
        self._call(path, NOTIFICATION_IFACE, "dismiss")

    def reply(self, notification_id: str, message: str) -> None:
        path = f"{device_path(self._device_id)}/notifications/{notification_id}"
        self._call(path, NOTIFICATION_IFACE, "reply", message)

    def dismiss_all(self) -> None:
        for note in self.active_notifications():
            if note.dismissable:
                try:
                    self.dismiss(note.id)
                except KdeConnectError as exc:
                    log.debug("could not dismiss %s: %s", note.id, exc)

    @Slot(str)
    def _onNotificationPosted(self, notification_id: str) -> None:
        note = self.notification(notification_id)
        if note is not None:
            self.notificationPosted.emit(note)

    @Slot(str)
    def _onNotificationRemoved(self, notification_id: str) -> None:
        self.notificationRemoved.emit(notification_id)

    @Slot()
    def _onNotificationsCleared(self) -> None:
        self.notificationsCleared.emit()

    # -- battery / extras ----------------------------------------------------

    def battery(self) -> tuple[int, bool] | None:
        if not self._device_id:
            return None
        path = device_path(self._device_id)
        charge = self._prop(path, BATTERY_IFACE, "charge")
        if charge is None or int(charge) < 0:
            return None
        return int(charge), bool(self._prop(path, BATTERY_IFACE, "isCharging", False))

    @Slot(bool, int)
    def _onBatteryRefreshed(self, is_charging: bool, charge: int) -> None:
        self.batteryChanged.emit(int(charge), bool(is_charging))

    def plugins(self) -> list[str]:
        if not self._device_id:
            return []
        try:
            return list(self._call(device_path(self._device_id), DEVICE_IFACE, "loadedPlugins") or [])
        except KdeConnectError:
            return []

    def has_plugin(self, name: str) -> bool:
        """True when *name* (e.g. 'notifications') is loaded for this device."""
        if not self._device_id:
            return False
        full = name if name.startswith("kdeconnect_") else f"kdeconnect_{name}"
        try:
            return bool(self._call(device_path(self._device_id), DEVICE_IFACE, "hasPlugin", full))
        except KdeConnectError:
            return False

    def supported_plugins(self) -> list[str]:
        """Plugins the paired phone advertises, readable even while offline."""
        if not self._device_id:
            return []
        return list(
            self._prop(device_path(self._device_id), DEVICE_IFACE, "supportedPlugins", []) or []
        )

    def ring(self) -> None:
        self._call(
            device_path(self._device_id), f"{DEVICE_IFACE}.findmyphone", "ring"
        )

    def ping(self) -> None:
        self._call(device_path(self._device_id), f"{DEVICE_IFACE}.ping", "sendPing")

    def send_file(self, url: str) -> None:
        self._call(device_path(self._device_id), f"{DEVICE_IFACE}.share", "shareUrl", url)

    # -- SMS -----------------------------------------------------------------

    def sms_available(self) -> bool:
        return self.has_plugin("sms")

    def send_sms(self, address: str, body: str) -> None:
        """Send an SMS through KDE Connect's Android app."""
        if not self._device_id:
            raise KdeConnectError("No phone selected.")

        base = device_path(self._device_id)
        iface = f"{DEVICE_IFACE}.sms"
        attempts = (
            # Current: addresses as a list, plus attachments and a SIM id.
            (f"{base}/sms", [[address], body, [], -1]),
            (f"{base}/sms", [[address], body, []]),
            # Older builds took a bare number.
            (f"{base}/sms", [address, body]),
            (base, [[address], body, [], -1]),
            (base, [address, body]),
        )

        problems = []
        for path, args in attempts:
            try:
                self._call(path, iface, "sendSms", *args, quiet=True)
            except KdeConnectError as exc:
                problems.append(f"{path} {len(args)} args: {exc}")
                continue
            return
        raise KdeConnectError(
            "KDE Connect would not send the message. Tried:\n- " + "\n- ".join(problems)
        )

    def launch_sms_app(self) -> None:
        """Open KDE Connect's own SMS window, if it is installed."""
        self._call(f"{device_path(self._device_id)}/sms", f"{DEVICE_IFACE}.sms", "launchApp")
