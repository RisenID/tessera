"""Now-playing information for a Bluetooth-connected phone."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject

from ..core import platform
from . import mpris_server
from .dbus import HAVE_QTDBUS, QDBusMessage, QDBusVariant, session

from ..core.proc import ManagedProcess, have, run

log = logging.getLogger(__name__)

MPRIS_PREFIX = "org.mpris.MediaPlayer2."


def _sanitise(name: str) -> str:
    """Match how mpris-proxy turns a device name into a bus name."""
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE = "org.freedesktop.DBus.Properties"


def _plain(node: Any) -> Any:
    """Strip busctl's {"type": ..., "data": ...} wrappers, recursively."""
    if isinstance(node, dict):
        if set(node.keys()) == {"type", "data"}:
            return _plain(node["data"])
        return {key: _plain(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_plain(item) for item in node]
    return node


def _unwrap(value: Any) -> Any:
    while isinstance(value, QDBusVariant):
        value = value.variant()
    return value


PLACEHOLDER_TITLES = {"not provided", "unknown", "no title"}


@dataclass
class Track:
    title: str = ""
    artist: str = ""
    album: str = ""
    status: str = "Stopped"

    @property
    def playing(self) -> bool:
        return self.status == "Playing"

    @property
    def summary(self) -> str:
        if not self.title:
            return "Nothing playing"
        return f"{self.title} — {self.artist}" if self.artist else self.title


class MprisPlayer(QObject):
    """Reads and controls whichever MPRIS player belongs to the phone."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        #: MPRIS is a desktop bus interface.
        self._usable = HAVE_QTDBUS and platform.supported("mpris")
        self._bus = session()
        self._proxy = ManagedProcess(self)

    # -- the bluez bridge ----------------------------------------------------

    def proxy_available(self) -> bool:
        return self._usable and have("mpris-proxy")

    @property
    def proxy_running(self) -> bool:
        return self._proxy.running

    def start_proxy(self) -> None:
        """Run bluez's AVRCP-to-MPRIS bridge."""
        if self._proxy.running:
            return
        if not self.proxy_available():
            raise RuntimeError(
                "mpris-proxy is missing; it ships with the bluez package."
            )
        self._proxy.start(["mpris-proxy"])

    # -- discovery -----------------------------------------------------------

    def _services(self) -> list[str]:
        if not self._usable or not self._bus.isConnected():
            return []
        reply = self._bus.interface().registeredServiceNames()
        names = reply.value() if hasattr(reply, "value") else []
        # Not our own: Tessera publishes the phone as a player too, and reading
        # that back would be this app talking to itself -- a transport button
        # that sends a command to the thing that sent it.
        return [
            n for n in names
            if n.startswith(MPRIS_PREFIX) and n != mpris_server.SERVICE
        ]

    def find_player(self, address: str = "", device_name: str = "") -> str:
        """The MPRIS service for the phone, or '' when none is present."""
        candidates = self._services()
        if not candidates:
            return ""

        for needle in (_sanitise(device_name), address.replace(":", "_").lower()):
            if not needle:
                continue
            for name in candidates:
                if needle in name.lower():
                    return name

        for name in candidates:
            if "bluez" in name.lower():
                return name

        # mpris-proxy owns exactly one service per connected device, and the
        # desktop's own players are not it; prefer a service this process
        # started over guessing wrongly.
        for name in candidates:
            if self._is_proxy_service(name):
                return name
        return ""

    def _is_proxy_service(self, service: str) -> bool:
        """Whether *service* is published by bluez's mpris-proxy."""
        try:
            owner = self._bus.interface().serviceOwner(service)
            owner_name = owner.value() if hasattr(owner, "value") else ""
        except Exception:  # noqa: BLE001 - a missing name is simply not ours
            return False
        return bool(owner_name) and self._proxy.running

    # -- reading and controlling ---------------------------------------------

    def _call(self, service: str, iface: str, method: str, *args: Any) -> Any:
        message = QDBusMessage.createMethodCall(service, MPRIS_PATH, iface, method)
        if args:
            message.setArguments(list(args))
        reply = self._bus.call(message, timeout=4000)
        if reply.type() == QDBusMessage.ErrorMessage:
            raise RuntimeError(reply.errorMessage())
        values = reply.arguments()
        return _unwrap(values[0]) if values else None

    def _property(self, service: str, name: str) -> Any:
        """Read one MPRIS property, decoded."""
        result = run(
            [
                "busctl", "--user", "--json=short", "get-property",
                service, MPRIS_PATH, PLAYER_IFACE, name,
            ],
            timeout=5.0,
        )
        if not result.ok:
            log.debug("could not read %s: %s", name, result.text)
            return None
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            return None
        return _plain(payload)

    def track(self, service: str) -> Track:
        if not service:
            return Track()

        metadata = self._property(service, "Metadata")
        status = self._property(service, "PlaybackStatus")
        metadata = metadata if isinstance(metadata, dict) else {}

        def text(key: str) -> str:
            value = metadata.get(key, "")
            if isinstance(value, (list, tuple)):
                return ", ".join(str(v) for v in value if str(v))
            return str(value) if value is not None else ""

        title = text("xesam:title")
        # AVRCP players report a placeholder when the phone sends no metadata.
        if title.strip().lower() in PLACEHOLDER_TITLES:
            title = ""
        return Track(
            title=title,
            artist=text("xesam:artist"),
            album=text("xesam:album"),
            status=str(status or "Stopped"),
        )

    def control(self, service: str, action: str) -> None:
        """Send a transport command: PlayPause, Next, Previous, Stop."""
        if not service:
            raise RuntimeError("The phone is not showing a media player.")
        self._call(service, PLAYER_IFACE, action)
