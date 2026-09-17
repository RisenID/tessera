"""Watching for the phone's Bluetooth beacon on Windows, through WinRT."""

from __future__ import annotations

import logging
import threading
import time
import uuid as _uuid

log = logging.getLogger(__name__)


def available() -> bool:
    try:
        import winrt.windows.devices.bluetooth.advertisement  # noqa: F401
    except ImportError:
        return False
    return True


class WinrtWatcher:
    """A BluetoothLEAdvertisementWatcher filtered to the beacon's UUID."""

    def __init__(self, uuid: str, tag: str) -> None:
        self.uuid = uuid
        self.tag = tag
        self._watcher = None
        self._lock = threading.Lock()
        self._last: tuple[float, int] | None = None
        self._token = None

    def start(self) -> str:
        try:
            from winrt.windows.devices.bluetooth.advertisement import (
                BluetoothLEAdvertisementWatcher,
                BluetoothLEScanningMode,
            )
        except ImportError:
            return "The Bluetooth LE package (winrt-Windows.Devices.Bluetooth.Advertisement) is not installed."
        watcher = BluetoothLEAdvertisementWatcher()
        watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE
        wanted = _uuid.UUID(self.uuid)
        watcher.advertisement_filter.advertisement.service_uuids.append(wanted)
        self._token = watcher.add_received(self._received)
        watcher.start()
        self._watcher = watcher
        return ""

    def _received(self, _sender, args) -> None:
        try:
            if self.tag:
                sections = args.advertisement.data_sections
                matched = False
                for section in sections:
                    if section.data_type != 0x21:      # 128-bit service data
                        continue
                    raw = bytes(section.data)
                    if raw[16:].decode("ascii", "replace") == self.tag:
                        matched = True
                        break
                if sections and not matched:
                    # A scan response with our tag arrives separately; keep the
                    # reading if no data section contradicted it.
                    if any(s.data_type == 0x21 for s in sections):
                        return
            with self._lock:
                self._last = (time.monotonic(), int(args.raw_signal_strength_in_d_bm))
        except Exception as exc:  # noqa: BLE001 - a callback must not raise into WinRT
            log.debug("beacon: %s", exc)

    def stop(self) -> None:
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            try:
                if self._token is not None:
                    watcher.remove_received(self._token)
                watcher.stop()
            except Exception as exc:  # noqa: BLE001
                log.debug("stopping the watcher: %s", exc)

    def rssi(self) -> int | None:
        with self._lock:
            last = self._last
        if last is None or time.monotonic() - last[0] > 12.0:
            return None
        return last[1]
