"""Persistent settings, stored as JSON under XDG_CONFIG_HOME."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

log = logging.getLogger(__name__)

APP_ID = "tessera"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / APP_ID


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / APP_ID


@dataclass
class DndConfig:
    #: off | phone_to_desktop | desktop_to_phone | two_way
    mode: str = "phone_to_desktop"
    #: seconds between polls of the phone's zen_mode (adb has no push channel)
    poll_seconds: int = 5
    #: mirror "priority only" and "alarms only" as desktop DND, not just silence
    treat_partial_as_dnd: bool = True


@dataclass
class WebcamConfig:
    #: camera | screen -- a phone lens, or a mirror of the phone display
    source: str = "camera"
    facing: str = "back"           # back | front | external
    #: Exact camera id reported by the phone. A phone has several lenses per
    #: facing, so the id is what actually picks the intended one.
    camera_id: str = ""
    size: str = "1920x1080"
    fps: int = 30
    device: str = ""               # empty -> pick the first free loopback node
    audio: bool = False


@dataclass
class HotspotConfig:
    ssid: str = ""
    passphrase: str = ""
    band: str = "2.4"              # 2.4 | 5
    #: wifi | usb -- USB tethering is the reliable fallback on locked-down ROMs
    method: str = "wifi"
    autoconnect: bool = True
    #: seconds to wait for the phone's AP to show up in a scan
    scan_timeout: int = 30


@dataclass
class CompanionConfig:
    """Everything needed to reconnect to the phone without pairing again."""

    host: str = ""
    port: int = 8765
    token: str = ""
    fingerprint: str = ""     # SHA-256 of the phone's TLS certificate
    name: str = ""
    device_id: str = ""


@dataclass
class FeatureConfig:
    """Which optional features are active.

    Every one costs something -- a permission on the phone, a poll, a
    subscription, or a background process -- so each can be turned off
    independently rather than being all-or-nothing.
    """

    notifications: bool = True
    otp: bool = True             # surface one-time passcodes from notifications
    notification_popups: bool = True
    messages: bool = True
    photos: bool = True
    clipboard: bool = True
    dnd_sync: bool = True
    webcam: bool = True
    screen: bool = True          # mirroring and per-app windows
    hotspot: bool = True
    bluetooth_audio: bool = True
    calls: bool = True


@dataclass
class BluetoothConfig:
    #: Address of the phone's Bluetooth radio, learned on first use.
    address: str = ""
    #: Start streaming as soon as the phone connects. Off by default: taking
    #: over the audio path uninvited interrupts whatever is already playing,
    #: and connecting is often only wanted for track info and call control.
    auto_stream: bool = False
    #: Move call audio to this computer when a call starts. Also off by
    #: default, for the same reason -- it silently reroutes a headset.
    route_calls: bool = False
    #: Which codecs to offer the phone for music. See backends.btcodecs.
    codec: str = "auto"
    #: What the phone said it can send, read from its Bluetooth endpoints the
    #: first time it connected. Remembered because "best available" has to
    #: narrow the offer to one codec to work at all, and narrowing it to one
    #: the phone cannot manage would leave nothing but SBC. BlueZ only
    #: publishes these while the phone is connected, so without a copy here
    #: the offer would swing between wide and narrow on every launch, and each
    #: swing restarts the audio service.
    phone_codecs: list[str] = field(default_factory=list)


@dataclass
class ClipboardConfig:
    #: off | phone_to_desktop | desktop_to_phone | two_way
    mode: str = "two_way"


@dataclass
class MirrorConfig:
    max_size: int = 1600
    fps: int = 60
    bitrate: str = "8M"
    audio: bool = True
    stay_awake: bool = True
    turn_screen_off: bool = False
    app_window_size: str = "1280x800"
    show_system_apps: bool = False


@dataclass
class Config:
    device_id: str = ""            # KDE Connect device id
    adb_serial: str = ""           # empty -> use the only attached device
    adb_wireless_host: str = ""    # host:port remembered from a previous pairing
    start_minimised: bool = False
    notification_popups: bool = True
    companion: CompanionConfig = field(default_factory=CompanionConfig)
    dnd: DndConfig = field(default_factory=DndConfig)
    mirror: MirrorConfig = field(default_factory=MirrorConfig)
    clipboard: ClipboardConfig = field(default_factory=ClipboardConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    webcam: WebcamConfig = field(default_factory=WebcamConfig)
    hotspot: HotspotConfig = field(default_factory=HotspotConfig)

    # -- persistence ---------------------------------------------------------

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "config.json"

    @classmethod
    def load(cls) -> "Config":
        path = cls.path()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable config %s: %s", path, exc)
            return cls()
        if not isinstance(raw, dict):
            log.warning("ignoring malformed config %s", path)
            return cls()
        return _from_dict(cls, raw)

    def save(self) -> None:
        """Write atomically so a crash mid-save cannot truncate the config."""
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix=".config-", delete=False
            ) as handle:
                tmp = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(path)
        except OSError as exc:
            log.error("could not save config: %s", exc)
            if tmp is not None and tmp.exists():
                tmp.unlink(missing_ok=True)


def _from_dict(cls: type, raw: dict[str, Any]) -> Any:
    """Rebuild a dataclass from JSON, dropping unknown or mistyped keys.

    Being lenient here means a config written by a newer version, or hand-edited
    slightly wrong, degrades to defaults instead of refusing to start.
    """
    # `from __future__ import annotations` makes Field.type a string, so the
    # annotations have to be resolved before they can be compared against.
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        declared = hints.get(f.name, Any)
        value = raw[f.name]
        if is_dataclass(declared) and isinstance(value, dict):
            kwargs[f.name] = _from_dict(declared, value)
        elif get_origin(declared) is list and isinstance(value, list):
            # list[str] is not a type, so the isinstance check below silently
            # rejects it and the field falls back to its default. That is how
            # the phone's codec list was being lost on every launch, which in
            # turn made the app re-advertise the wide codec set and restart the
            # audio service twice before settling back on the narrow one.
            item = (get_args(declared) or (Any,))[0]
            kwargs[f.name] = [
                entry for entry in value
                if item is Any or (isinstance(entry, item) and not isinstance(entry, bool))
            ]
        elif declared is float and isinstance(value, (int, float)) and not isinstance(value, bool):
            kwargs[f.name] = float(value)
        elif isinstance(declared, type) and isinstance(value, declared):
            # bool is a subclass of int; do not let `true` satisfy an int field.
            if declared is not bool and isinstance(value, bool):
                log.debug("dropping config key %s: expected %s", f.name, declared.__name__)
                continue
            kwargs[f.name] = value
        else:
            log.debug("dropping config key %s with unexpected value %r", f.name, value)
    try:
        return cls(**kwargs)
    except TypeError as exc:  # pragma: no cover - defensive
        log.warning("could not apply saved config: %s", exc)
        return cls()
