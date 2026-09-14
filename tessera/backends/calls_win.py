"""The phone's call audio on Windows, through PhoneLineTransportDevice."""

from __future__ import annotations

import logging

from .bluetooth_win import address_of

log = logging.getLogger(__name__)

#: DeviceAccessStatus.ALLOWED
ACCESS_ALLOWED = 1


def _winrt():
    from winrt.windows.applicationmodel.calls import PhoneLineTransport, PhoneLineTransportDevice
    from winrt.windows.devices.enumeration import DeviceInformation

    return DeviceInformation, PhoneLineTransportDevice, PhoneLineTransport


def available() -> bool:
    try:
        _winrt()
    except (ImportError, OSError):
        return False
    return True


def _selector(device_class, transport) -> str:
    for name in ("get_device_selector_with_transport", "get_device_selector_for_transport"):
        method = getattr(device_class, name, None)
        if method is not None:
            return method(transport.BLUETOOTH)
    return device_class.get_device_selector()


def device(address: str):
    """The hands-free line for *address*, or None."""
    if not available():
        return None
    information, device_class, transport = _winrt()
    try:
        for info in information.find_all_async_aqs_filter(_selector(device_class, transport)).get():
            if address_of(info.id).lower() == address.lower():
                return device_class.from_id(info.id)
    except OSError as exc:
        log.debug("could not list phone lines: %s", exc)
    return None


def can_take_calls(address: str) -> bool:
    return device(address) is not None


def take_calls(address: str) -> None:
    """Make this computer the phone's hands-free device. Raises RuntimeError."""
    line = device(address)
    if line is None:
        raise RuntimeError(
            "Windows does not list the phone as a hands-free device. Pair it in "
            "Bluetooth settings, and allow calls on the phone."
        )
    try:
        if not line.is_registered():
            if int(line.request_access_async().get()) != ACCESS_ALLOWED:
                raise RuntimeError("Windows did not allow Tessera to take the phone's calls.")
            line.register_app()
        if not line.connect_async().get():
            raise RuntimeError("Windows could not connect the phone's call audio.")
    except OSError as exc:
        raise RuntimeError(f"Windows could not connect the phone's call audio: {exc}") from exc
