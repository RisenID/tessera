"""Bluetooth audio from the phone on Windows, through AudioPlaybackConnection.

Windows 10 2004 and later can be the speaker a paired phone plays to. Pairing
stays Windows' own business; this only lets the phone send its audio here.

Two steps, which line up with the two connects on Linux:

* started -- the phone may pick this computer as its output, and nothing moves
  until it does. This is the quiet connect.
* opened -- the phone is asked to send its audio here now.

The names match backends.bluetooth, which takes these in place of its own on
Windows, so the hub and the Audio page have one path for both.
"""

from __future__ import annotations

import logging
import re
import threading
import time

# bluetooth imports this module at its end, once these three exist.
from .bluetooth import UUID_A2DP_SOURCE_FULL, BluetoothError, BtDevice

log = logging.getLogger(__name__)

#: The phone's radio address inside an audio endpoint's device id:
#: ``...#b&3430c306&0&08023C81E037_C00000000#{...}``.
_ADDRESS = re.compile(r"&([0-9A-Fa-f]{12})_C[0-9A-Fa-f]*#")

#: AudioPlaybackConnectionState.OPENED
STATE_OPENED = 1

#: AudioPlaybackConnectionOpenResultStatus, other than SUCCESS (0).
OPEN_FAILURES = {
    1: "The phone did not answer in time. Check that Bluetooth is on and the "
       "phone is in range.",
    2: "Windows refused the audio connection. Check that Bluetooth is on, and "
       "that the phone is paired in Windows' Bluetooth settings.",
    3: "Windows could not open the audio connection to the phone.",
}

_lock = threading.Lock()
#: Connections this app holds, by address. Holding one is being connected.
_connections: dict[str, object] = {}
#: Endpoint ids, by address, as last listed.
_ids: dict[str, str] = {}


def _winrt():
    """The two WinRT classes this needs, imported only when used."""
    from winrt.windows.devices.enumeration import DeviceInformation
    from winrt.windows.media.audio import AudioPlaybackConnection

    return DeviceInformation, AudioPlaybackConnection


def address_of(device_id: str) -> str:
    """AA:BB:CC:DD:EE:FF from an endpoint id, or '' when it carries none."""
    match = _ADDRESS.search(device_id)
    if not match:
        return ""
    raw = match.group(1).upper()
    return ":".join(raw[index:index + 2] for index in range(0, 12, 2))


def available() -> bool:
    """Whether this Windows, and this build, can receive Bluetooth audio."""
    try:
        _winrt()
    except (ImportError, OSError):
        return False
    return True


def adapter_ready() -> bool:
    return available()


#: Bluetooth Classic association endpoints, with their live connection state.
_PAIRED_AQS = (
    'System.Devices.Aep.ProtocolId:="{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}" AND '
    "System.Devices.Aep.IsPaired:=System.StructuredQueryType.Boolean#True"
)
_ADDRESS_KEY = "System.Devices.Aep.DeviceAddress"
_CONNECTED_KEY = "System.Devices.Aep.IsConnected"


def link_states() -> dict[str, bool]:
    """Whether each paired device's radio link is up, by address. Empty if unknown."""
    try:
        from winrt.system import unbox_boolean, unbox_string
        from winrt.windows.devices.enumeration import DeviceInformation, DeviceInformationKind

        found = DeviceInformation.find_all_async_with_kind_aqs_filter_and_additional_properties(
            _PAIRED_AQS, [_CONNECTED_KEY, _ADDRESS_KEY], DeviceInformationKind.ASSOCIATION_ENDPOINT
        ).get()
        states = {}
        for info in found:
            props = info.properties
            if props.has_key(_ADDRESS_KEY) and props.has_key(_CONNECTED_KEY):
                address = unbox_string(props.lookup(_ADDRESS_KEY)).upper()
                states[address] = bool(unbox_boolean(props.lookup(_CONNECTED_KEY)))
        return states
    except (ImportError, OSError, AttributeError, TypeError) as exc:
        log.debug("could not read Bluetooth link states: %s", exc)
        return {}


def paired_devices() -> list[BtDevice]:
    """Every paired device Windows would take audio from."""
    if not available():
        return []
    information, factory = _winrt()
    try:
        found = information.find_all_async_aqs_filter(
            factory.get_device_selector()
        ).get()
    except OSError as exc:
        log.debug("could not list Bluetooth audio sources: %s", exc)
        return []

    links = link_states()
    devices = []
    for info in found:
        address = address_of(info.id) or info.id
        with _lock:
            _ids[address] = info.id
            # A started connection is only a permission; the radio link says
            # whether the phone is actually there.
            connected = address in _connections and links.get(address.upper(), True)
        devices.append(BtDevice(
            address=address,
            name=info.name,
            paired=True,
            connected=connected,
            trusted=True,
            # Listed at all means it can send audio here. Calls and track
            # controls stay with Windows, so nothing else is claimed.
            uuids=[UUID_A2DP_SOURCE_FULL],
        ))
    return devices


def device_info(address: str, fallback_name: str = "") -> BtDevice:
    for device in paired_devices():
        if device.address.lower() == address.lower():
            return device
    return BtDevice(address=address, name=fallback_name)


def find_phone(preferred_address: str = "", name_hint: str = "") -> BtDevice | None:
    """Pick the audio source most likely to be the phone."""
    devices = paired_devices()
    if preferred_address:
        for device in devices:
            if device.address.lower() == preferred_address.lower():
                return device
    if name_hint:
        needle = name_hint.lower()
        for device in devices:
            if needle in device.name.lower():
                return device
    # Headphones do not send audio, so a lone source is almost always the phone.
    return devices[0] if len(devices) == 1 else None


def _close(connection) -> None:
    try:
        connection.close()
    except OSError as exc:
        log.debug("closing the audio connection: %s", exc)


def _started(address: str):
    """The connection for *address*, started if it was not already."""
    with _lock:
        existing = _connections.get(address)
    if existing is not None:
        return existing
    if not available():
        raise BluetoothError(
            "This computer cannot receive Bluetooth audio: it needs Windows 10 "
            "version 2004 or later."
        )
    if address not in _ids:
        paired_devices()
    device_id = _ids.get(address)
    if device_id is None:
        raise BluetoothError(
            f"{address} is not paired with this computer. Pair the phone in "
            "Windows' Bluetooth settings first."
        )

    _information, factory = _winrt()
    try:
        connection = factory.try_create_from_id(device_id)
    except OSError as exc:
        raise BluetoothError(f"Windows could not reach the phone: {exc}") from exc
    if connection is None:
        raise BluetoothError(
            "Windows would not make an audio connection to the phone. Check that "
            "it is paired and that Bluetooth is on."
        )
    try:
        connection.start_async().get()
    except OSError as exc:
        _close(connection)
        raise BluetoothError(f"Windows could not start the audio connection: {exc}") from exc
    with _lock:
        _connections[address] = connection
    return connection


def connect_quietly(address: str, timeout: float = 25.0) -> None:
    """Let the phone choose this computer as its speaker, moving nothing yet."""
    _started(address)


def connect(address: str, timeout: float = 25.0) -> None:
    """Connect, and ask the phone to send its audio here now."""
    connection = _started(address)
    try:
        result = connection.open_async().get()
    except OSError as exc:
        raise BluetoothError(f"Windows could not open the audio connection: {exc}") from exc
    status = int(result.status)
    if status != 0:
        raise BluetoothError(OPEN_FAILURES.get(status, OPEN_FAILURES[3]))


#: Replaced in checks, so retries do not wait for real.
_sleep = time.sleep


def claim_audio(address: str, attempts: int = 3) -> bool:
    """Ask the phone to send its audio here, until it really is."""
    for attempt in range(attempts):
        if attempt:
            # A connection that will not open is usually stale: start afresh.
            disconnect(address)
            _sleep(1.0)
        try:
            connect(address)
        except BluetoothError as exc:
            log.info("claim attempt %d: %s", attempt + 1, exc)
            continue
        for _ in range(30):
            if audio_transport(address):
                return True
            _sleep(0.1)
    return False


def release_audio(address: str) -> bool:
    """Send the audio back to the phone, staying ready for it to come back."""
    with _lock:
        connection = _connections.get(address)
        if connection is None or int(connection.state) != STATE_OPENED:
            return True
        del _connections[address]
    # A connection cannot be half closed: close it, and start a fresh one.
    _close(connection)
    try:
        _started(address)
    except BluetoothError as exc:
        log.debug("could not start again after releasing: %s", exc)
        return False
    return True


def disconnect(address: str, timeout: float = 20.0) -> None:
    with _lock:
        connection = _connections.pop(address, None)
    if connection is not None:
        _close(connection)


def forget(address: str) -> None:
    """Windows owns the pairing, so say where to remove it."""
    disconnect(address)
    raise BluetoothError(
        "Remove the phone under Settings, Bluetooth & devices, then pair it "
        "again on both devices."
    )


def audio_transport(address: str) -> str:
    """'active' while the phone's audio is coming here, else ''."""
    with _lock:
        connection = _connections.get(address)
    if connection is None:
        return ""
    return "active" if int(connection.state) == STATE_OPENED else ""


def audio_connected(device: "BtDevice | str") -> bool:
    address = device if isinstance(device, str) else device.address
    return bool(audio_transport(address))


def media_state(address: str) -> tuple[str, list[str]]:
    """The transport, and no codecs: Windows does not say which it chose."""
    return audio_transport(address), []


def remote_codecs(address: str) -> list[str]:
    return []


def open_addresses() -> list[str]:
    """Phones whose audio is arriving here right now."""
    with _lock:
        return [
            address for address, connection in _connections.items()
            if int(connection.state) == STATE_OPENED
        ]
