"""Bluetooth link to the phone, for call audio and music.

Both features are ordinary Bluetooth profiles rather than anything Tessera
invents; what the app adds is making them one click and explaining the failures,
which BlueZ reports as opaque strings.

The phone offers two roles that matter here:

* **Audio Source** (A2DP) -- the phone streams music to the computer, which
  acts as the sink.
* **Handsfree Audio Gateway** (HFP) -- the computer acts as the headset, so a
  call's audio arrives on the computer's speakers and microphone.

PipeWire enables both of those roles by default, which is visible in the
adapter advertising "Audio Sink" and "Handsfree" UUIDs.

bluetoothctl is driven rather than BlueZ's D-Bus API directly: the object
manager returns deeply nested containers that PySide6's D-Bus bindings
unmarshal poorly, and bluetoothctl's text output is stable and carries the
error strings verbatim.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.proc import have, run

log = logging.getLogger(__name__)

BLUETOOTHCTL = "bluetoothctl"

#: Profile UUIDs, keyed by what they let the computer do.
UUID_A2DP_SOURCE = "0000110a"   # phone sends audio -> music on the computer
UUID_AVRCP_TARGET = "0000110c"  # track metadata and transport controls
UUID_HFP_AG = "0000111f"        # phone is the gateway -> calls on the computer


class BluetoothError(RuntimeError):
    pass


@dataclass
class BtDevice:
    address: str
    name: str = ""
    paired: bool = False
    connected: bool = False
    trusted: bool = False
    uuids: list[str] = field(default_factory=list)

    def _has(self, prefix: str) -> bool:
        return any(u.lower().startswith(prefix) for u in self.uuids)

    @property
    def can_stream_music(self) -> bool:
        return self._has(UUID_A2DP_SOURCE)

    @property
    def can_take_calls(self) -> bool:
        return self._has(UUID_HFP_AG)

    @property
    def has_media_controls(self) -> bool:
        return self._has(UUID_AVRCP_TARGET)

    @property
    def label(self) -> str:
        return self.name or self.address


def available() -> bool:
    return have(BLUETOOTHCTL)


def adapter_ready() -> bool:
    """True when a powered adapter exists."""
    result = run([BLUETOOTHCTL, "show"], timeout=10.0)
    return result.ok and "Powered: yes" in result.stdout


def paired_devices() -> list[BtDevice]:
    """Every paired device, with its capabilities filled in."""
    if not available():
        return []
    result = run([BLUETOOTHCTL, "devices", "Paired"], timeout=15.0)
    if not result.ok:
        return []

    devices = []
    for line in result.stdout.splitlines():
        match = re.match(r"Device\s+([0-9A-F:]{17})\s+(.*)", line.strip(), re.IGNORECASE)
        if match:
            devices.append(device_info(match.group(1), fallback_name=match.group(2)))
    return devices


def device_info(address: str, fallback_name: str = "") -> BtDevice:
    result = run([BLUETOOTHCTL, "info", address], timeout=15.0)
    device = BtDevice(address=address, name=fallback_name)
    if not result.ok:
        return device

    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            device.name = line.split(":", 1)[1].strip()
        elif line.startswith("Paired:"):
            device.paired = line.endswith("yes")
        elif line.startswith("Connected:"):
            device.connected = line.endswith("yes")
        elif line.startswith("Trusted:"):
            device.trusted = line.endswith("yes")
        elif line.startswith("UUID:"):
            match = re.search(r"\(([0-9a-fA-F-]+)\)", line)
            if match:
                device.uuids.append(match.group(1))
    return device


def find_phone(preferred_address: str = "", name_hint: str = "") -> BtDevice | None:
    """Pick the paired device most likely to be the phone.

    Prefers a configured address, then a name match, then any paired device
    that offers both call and music roles -- which headphones do not.
    """
    # Ask about the known address directly. Enumerating every paired device
    # runs a bluetoothctl info per device -- four processes where one will do,
    # on a check that repeats for as long as the app is open.
    if preferred_address:
        device = device_info(preferred_address)
        if device.paired:
            return device

    devices = paired_devices()
    if not devices:
        return None

    if name_hint:
        needle = name_hint.lower()
        for device in devices:
            if needle in device.name.lower():
                return device

    for device in devices:
        if device.can_take_calls and device.can_stream_music:
            return device
    return None


def connect(address: str, timeout: float = 25.0) -> None:
    """Connect, translating BlueZ's error strings into something actionable."""
    if not available():
        raise BluetoothError("bluetoothctl is not installed.")
    result = run([BLUETOOTHCTL, "connect", address], timeout=timeout)
    text = result.text

    if "Connection successful" in text:
        return
    raise BluetoothError(explain(text, address))


def disconnect(address: str, timeout: float = 20.0) -> None:
    result = run([BLUETOOTHCTL, "disconnect", address], timeout=timeout)
    if "Successful disconnected" not in result.text and not result.ok:
        raise BluetoothError(result.text or "Could not disconnect.")


#: The phone's A2DP source role. Connecting this is what makes Android move
#: its media output to this computer; disconnecting it hands playback back to
#: whatever the phone was using before.
UUID_A2DP_SOURCE_FULL = "0000110a-0000-1000-8000-00805f9b34fb"


#: The adapter BlueZ falls back to when nothing better can be determined.
FALLBACK_ADAPTER = "hci0"


def _device_suffix(address: str) -> str:
    return "/dev_" + address.replace(":", "_").upper()


def _device_path_in(objects: dict, address: str) -> str:
    """The device's D-Bus object path, taken from objects already in hand."""
    suffix = _device_suffix(address)
    for path in objects:
        if path.startswith("/org/bluez/") and path.endswith(suffix):
            return path
    return ""


def device_path(address: str) -> str:
    """The device's D-Bus object path, on whichever adapter holds it.

    Not /org/bluez/hci0/dev_XX. The adapter is hci0 on most machines and is
    not on plenty of others: plug in a USB Bluetooth dongle, or have had one
    plugged in once, and the built-in radio can be hci1. Assuming hci0 there
    does not fail loudly -- ConnectProfile is sent to an object that does not
    exist, and the transport and codec lookups simply find nothing, so music
    never arrives and nothing says why.

    Falls back to the first adapter the kernel lists, then to hci0, so the
    answer is never worse than the assumption it replaces.
    """
    found = _device_path_in(_managed_objects(), address)
    if found:
        return found

    adapters = sorted(p.name for p in Path("/sys/class/bluetooth").glob("hci*"))
    adapter = adapters[0] if adapters else FALLBACK_ADAPTER
    return f"/org/bluez/{adapter}{_device_suffix(address)}"


def _profile(address: str, method: str, uuid: str) -> bool:
    path = device_path(address)
    result = run(
        ["busctl", "--system", "call", "org.bluez", path,
         "org.bluez.Device1", method, "s", uuid],
        timeout=20.0,
    )
    if not result.ok:
        log.debug("%s %s failed: %s", method, uuid, result.text)
    return result.ok


def release_audio(address: str) -> bool:
    """Hand playback back to the phone's previous output.

    Connecting a phone connects every profile it offers, and Android promotes a
    newly connected A2DP device to be the active output -- which is what pulls
    music off a pair of headphones. Dropping just that profile leaves the
    device connected while returning the audio.
    """
    return _profile(address, "DisconnectProfile", UUID_A2DP_SOURCE_FULL)


#: The phone's hands-free gateway role, which carries calls but no media.
UUID_HFP_AG_FULL = "0000111f-0000-1000-8000-00805f9b34fb"


def connect_quietly(address: str, timeout: float = 25.0) -> None:
    """Connect without taking over the phone's media output.

    A plain connect brings up every profile the phone offers, and Android
    promotes a newly connected A2DP device to be the active output -- music
    jumps off whatever headphones are in use. Connecting only the hands-free
    profile leaves A2DP alone entirely, so playback never moves; the audio
    profile is claimed later, and only if asked for.

    Falls back to a full connect followed by releasing the audio, for devices
    that refuse a single-profile connect.
    """
    if not available():
        raise BluetoothError("bluetoothctl is not installed.")

    if _profile(address, "ConnectProfile", UUID_HFP_AG_FULL):
        return

    log.info("single-profile connect refused; connecting fully and releasing audio")
    connect(address, timeout=timeout)
    release_audio(address)


def claim_audio(address: str) -> bool:
    """Ask the phone to send its audio here, for an explicit stream.

    Android moves playback to a media device when that device *connects*, not
    while it sits there connected: asking again for a profile that is already
    up changes nothing, and the phone keeps playing to its own speaker. So a
    profile that is already connected is dropped first, which turns the request
    into the connection Android acts on.
    """
    import time

    if audio_transport(address):
        release_audio(address)
        time.sleep(2.0)
    return _profile(address, "ConnectProfile", UUID_A2DP_SOURCE_FULL)


def forget(address: str) -> None:
    """Remove the pairing, so the phone can be paired again from scratch."""
    run([BLUETOOTHCTL, "remove", address], timeout=20.0)


def explain(message: str, address: str) -> str:
    """Turn a BlueZ failure into an instruction.

    These strings are the only diagnosis a user gets, and on their own they are
    close to meaningless.
    """
    lowered = message.lower()

    if "key-missing" in lowered or "authentication failed" in lowered:
        return (
            "The computer and phone still list each other as paired, but the "
            "stored security key no longer matches — usually because the "
            "pairing was removed on one side.\n\n"
            "Forget the device here and on the phone, then pair again."
        )
    if "page-timeout" in lowered or "timeout" in lowered:
        return (
            "The phone did not answer. Check that Bluetooth is on, the phone is "
            "in range, and it is not connected to something else."
        )
    if "not available" in lowered or "does not exist" in lowered:
        return f"{address} is not known to this computer. Pair the phone first."
    if "in progress" in lowered:
        return "A connection attempt is already running."
    if "br-connection-profile-unavailable" in lowered:
        return (
            "The phone refused the audio profiles. On the phone, open the "
            "Bluetooth settings for this computer and enable Call audio and "
            "Media audio."
        )
    return message.strip() or "The Bluetooth connection failed."


#: Transport states BlueZ reports for an audio link. "idle" means the profile
#: is connected and configured but no audio is being carried -- the normal
#: state while the phone is selected as the output but nothing is playing.
TRANSPORT_STREAMING = ("pending", "active", "broadcasting")


def _managed_objects() -> dict:
    """Everything BlueZ is currently publishing, as one dict.

    The whole object tree in a single call. Both things worth knowing about a
    phone -- what its media transport is doing, and which codecs it can send --
    live in here, and reading it twice to answer them separately was a second
    process and a second parse for data already in hand.
    """
    result = run(
        ["busctl", "--system", "--json=short", "call", "org.bluez", "/",
         "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"],
        timeout=15.0,
    )
    if not result.ok:
        return {}
    try:
        return json.loads(result.stdout)["data"][0]
    except (ValueError, KeyError, IndexError):
        return {}


def _transport_of(objects: dict, address: str) -> str:
    device = _device_path_in(objects, address)
    if not device:
        return ""
    best = ""
    for path, interfaces in objects.items():
        if not path.startswith(device):
            continue
        transport = interfaces.get("org.bluez.MediaTransport1")
        if not transport:
            continue
        state = str((transport.get("State") or {}).get("data", ""))
        # A device can offer several endpoints; a streaming one is the answer.
        if state in TRANSPORT_STREAMING:
            return state
        best = best or state
    return best


def audio_transport(address: str) -> str:
    """State of the phone's media transport: '', 'idle', 'pending', 'active'.

    An empty string means the phone has not selected this computer as an audio
    output at all, which is a different problem from a phone that has selected
    it and simply is not playing anything. Telling those two apart is the
    difference between a useful message and a misleading one.
    """
    return _transport_of(_managed_objects(), address)


#: A2DP codec identifiers, as they appear in an endpoint's Codec property.
#: 0xFF means the codec is vendor-defined and the first six capability bytes
#: say which one: a little-endian 32-bit vendor id then a 16-bit codec id.
CODEC_SBC = 0
CODEC_MPEG = 1
CODEC_AAC = 2
CODEC_VENDOR = 0xFF

VENDOR_CODECS: dict[tuple[int, int], str] = {
    (0x0000004F, 0x0001): "aptx",        # APT Ltd
    (0x000000D7, 0x0024): "aptx_hd",     # Qualcomm
    (0x0000000A, 0x0001): "faststream",  # CSR
    (0x0000012D, 0x00AA): "ldac",        # Sony
    (0x000000D7, 0x00AD): "aptx_adaptive",
    (0x00000075, 0x0103): "samsung_hd",  # Samsung Scalable
}


def _codec_name(codec: int, capabilities: list[int]) -> str:
    if codec == CODEC_SBC:
        return "sbc"
    if codec == CODEC_AAC:
        return "aac"
    if codec == CODEC_MPEG:
        return "mpeg"
    if codec != CODEC_VENDOR or len(capabilities) < 6:
        return ""
    vendor = int.from_bytes(bytes(capabilities[0:4]), "little")
    identifier = int.from_bytes(bytes(capabilities[4:6]), "little")
    return VENDOR_CODECS.get((vendor, identifier), "")


def _codecs_of(objects: dict, address: str) -> list[str]:
    device = _device_path_in(objects, address)
    if not device:
        return []
    found: list[str] = []
    for path, interfaces in sorted(objects.items()):
        if not path.startswith(device + "/"):
            continue
        endpoint = interfaces.get("org.bluez.MediaEndpoint1")
        if not endpoint:
            continue
        # Only the endpoints where the phone is the source: the other
        # direction is this computer sending to the phone, which is not what
        # any of this is about.
        if not str((endpoint.get("UUID") or {}).get("data", "")).startswith(UUID_A2DP_SOURCE):
            continue
        name = _codec_name(
            int((endpoint.get("Codec") or {}).get("data", -1)),
            list((endpoint.get("Capabilities") or {}).get("data") or []),
        )
        if name and name not in found:
            found.append(name)
    return found


def remote_codecs(address: str) -> list[str]:
    """The codecs the phone can send, straight from the phone.

    BlueZ publishes the remote device's stream endpoints as MediaEndpoint1
    objects under the device path -- .../dev_XX_.../sep1, sep2 and so on --
    once a connection has discovered them. Each names a codec the phone is
    willing to source, which is the only reliable way to know what it can
    actually do: a Galaxy S25 turns out to offer SBC, AAC, aptX, LDAC and
    Samsung's own codec, and no aptX HD at all, which is why asking it for
    aptX HD got plain SBC.

    Empty while the phone has never connected. Callers must treat that as "not
    known yet" rather than "supports nothing".
    """
    return _codecs_of(_managed_objects(), address)


def media_state(address: str) -> tuple[str, list[str]]:
    """The transport state and the phone's codecs, from one query.

    For the periodic check, which wants both and should not pay twice.
    """
    objects = _managed_objects()
    return _transport_of(objects, address), _codecs_of(objects, address)


def audio_connected(device: "BtDevice | str") -> bool:
    """Whether the phone's media profile is up, whoever brought it up.

    A transport object exists only once A2DP is connected, which is also the
    moment Android moves its playback here -- so this is the test for "the
    audio has been taken over", not the mere fact of being connected.
    """
    address = device if isinstance(device, str) else device.address
    return bool(audio_transport(address))
