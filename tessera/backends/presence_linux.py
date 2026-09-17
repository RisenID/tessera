"""Watching for the phone's Bluetooth beacon through BlueZ."""

from __future__ import annotations

import json
import logging

from ..core.proc import have, run

log = logging.getLogger(__name__)

BLUEZ = "org.bluez"


def available() -> bool:
    return have("busctl")


def _busctl(*args: str, timeout: float = 8.0):
    return run(["busctl", "--system", "--json=short", *args], timeout=timeout)


def _objects() -> dict:
    result = _busctl("call", BLUEZ, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    if not result.ok:
        return {}
    try:
        return json.loads(result.stdout)["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return {}


def adapter_path() -> str:
    for path, interfaces in _objects().items():
        if "org.bluez.Adapter1" in interfaces:
            return path
    return ""


class BluezWatcher:
    """Runs an LE scan filtered to the beacon's UUID, and reads the phone's RSSI."""

    def __init__(self, uuid: str, tag: str) -> None:
        self.uuid = uuid.lower()
        self.tag = tag
        self._adapter = ""
        self._scanning = False

    def start(self) -> str:
        """Begin scanning. Empty when it worked, else why not."""
        self._adapter = adapter_path()
        if not self._adapter:
            return "No Bluetooth adapter was found."
        filter_result = run([
            "busctl", "--system", "call", BLUEZ, self._adapter, "org.bluez.Adapter1", "SetDiscoveryFilter",
            "a{sv}", "3", "UUIDs", "as", "1", self.uuid, "Transport", "s", "le",
            "DuplicateData", "b", "true",
        ], timeout=8.0)
        if not filter_result.ok:
            log.debug("discovery filter: %s", filter_result.text)
        started = run(["busctl", "--system", "call", BLUEZ, self._adapter, "org.bluez.Adapter1", "StartDiscovery"],
                      timeout=8.0)
        if not started.ok and "InProgress" not in started.text:
            return f"Bluetooth scanning could not start: {started.text.strip()[:120]}"
        self._scanning = True
        return ""

    def stop(self) -> None:
        if not self._scanning or not self._adapter:
            return
        self._scanning = False
        run(["busctl", "--system", "call", BLUEZ, self._adapter, "org.bluez.Adapter1", "StopDiscovery"], timeout=8.0)
        run(["busctl", "--system", "call", BLUEZ, self._adapter, "org.bluez.Adapter1", "SetDiscoveryFilter",
             "a{sv}", "0"], timeout=8.0)

    def rssi(self) -> int | None:
        """The phone's signal strength now, or None when it is not being heard."""
        for _path, interfaces in _objects().items():
            device = interfaces.get("org.bluez.Device1")
            if not device:
                continue
            uuids = [str(u).lower() for u in _value(device.get("UUIDs")) or []]
            data = _value(device.get("ServiceData")) or {}
            if self.uuid not in uuids and self.uuid not in {str(k).lower() for k in data}:
                continue
            if self.tag:
                payload = data.get(self.uuid) or data.get(self.uuid.upper())
                if isinstance(payload, dict):
                    payload = payload.get("data")
                if isinstance(payload, list):
                    heard = bytes(int(b) for b in payload).decode("ascii", "replace")
                    if heard != self.tag:
                        continue
            reading = _value(device.get("RSSI"))
            if isinstance(reading, int):
                return reading
        return None


def _value(field):
    """busctl wraps each property as {"type": ..., "data": ...}."""
    if isinstance(field, dict) and "data" in field:
        return field["data"]
    return field
