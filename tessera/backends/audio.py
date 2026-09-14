"""Audio routing for the Bluetooth link."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..core import packages
from ..core.proc import have, run

log = logging.getLogger(__name__)

PACTL = "pactl"

#: Profile names vary by codec and role, so descriptions are matched first.
MUSIC_PROFILE_HINTS = ("a2dp-sink", "a2dp_sink", "a2dp-source", "audio-gateway")
CALL_PROFILE_HINTS = ("headset-head-unit", "handsfree-head-unit", "hfp", "hsp",
                      "audio-gateway")
MUSIC_DESCRIPTION_HINTS = ("a2dp",)
CALL_DESCRIPTION_HINTS = ("hfp", "hsp", "handsfree", "headset")


@dataclass
class BtCard:
    name: str
    index: int
    active_profile: str = ""
    profiles: list[str] = field(default_factory=list)
    description: str = ""
    #: Profile name -> its human description, which names the roles carried.
    profile_descriptions: dict[str, str] = field(default_factory=dict)

    def _match(self, name_hints: tuple[str, ...], text_hints: tuple[str, ...]) -> str:
        """Best available profile for a purpose."""
        for profile in self.profiles:
            if profile.lower().startswith("off"):
                continue
            description = self.profile_descriptions.get(profile, "").lower()
            if any(hint in description for hint in text_hints):
                return profile

        for profile in self.profiles:
            lowered = profile.lower()
            if any(hint in lowered for hint in name_hints) and not lowered.startswith("off"):
                return profile
        return ""

    @property
    def music_profile(self) -> str:
        return self._match(MUSIC_PROFILE_HINTS, MUSIC_DESCRIPTION_HINTS)

    @property
    def call_profile(self) -> str:
        return self._match(CALL_PROFILE_HINTS, CALL_DESCRIPTION_HINTS)

    @property
    def mode(self) -> str:
        """'music', 'call', 'off' or 'other'."""
        lowered = (self.active_profile or "").lower()
        if any(hint in lowered for hint in MUSIC_PROFILE_HINTS):
            return "music"
        if any(hint in lowered for hint in CALL_PROFILE_HINTS):
            return "call"
        if lowered in ("off", ""):
            return "off"
        return "other"


def available() -> bool:
    return have(PACTL)


def _cards() -> list[dict]:
    if not available():
        return []
    result = run([PACTL, "-f", "json", "list", "cards"], timeout=15.0)
    if not result.ok:
        return []
    try:
        cards = json.loads(result.stdout)
    except ValueError as exc:
        log.debug("could not parse pactl card list: %s", exc)
        return []
    return cards if isinstance(cards, list) else []


def bluetooth_card(address: str = "") -> BtCard | None:
    """The PipeWire card for a connected Bluetooth device."""
    normalised = address.replace(":", "_").lower()
    for card in _cards():
        name = card.get("name", "")
        if not name.startswith("bluez_card."):
            continue
        if normalised and normalised not in name.lower():
            continue
        properties = card.get("properties", {}) or {}
        return BtCard(
            name=name,
            index=int(card.get("index", -1)),
            active_profile=card.get("active_profile", "") or "",
            profiles=list((card.get("profiles") or {}).keys()),
            profile_descriptions={
                name: (entry or {}).get("description", "")
                for name, entry in (card.get("profiles") or {}).items()
            },
            description=properties.get("device.description", "") or name,
        )
    return None


#: The profile that carries no audio.
OFF_PROFILE = "off"


def ready_to_receive(address: str) -> str:
    """Make sure the card can accept audio, and say which profile it uses."""
    card = bluetooth_card(address)
    if card is None:
        return ""
    profile = card.music_profile
    if profile and card.active_profile != profile:
        set_profile(card.name, profile)
    return profile


def set_profile(card: str, profile: str) -> None:
    """Switch a card between music and call profiles."""
    result = run([PACTL, "set-card-profile", card, profile], timeout=20.0)
    if not result.ok:
        raise RuntimeError(result.text or f"Could not select {profile}.")


def set_default_sink(name: str) -> None:
    run([PACTL, "set-default-sink", name], timeout=10.0)


def set_default_source(name: str) -> None:
    run([PACTL, "set-default-source", name], timeout=10.0)


@dataclass
class Stream:
    """A stream of audio arriving from the phone."""

    node: str = ""
    profile: str = ""
    codec: str = ""
    rate: int = 0
    channels: int = 0
    #: The width PipeWire decodes into, such as S24LE. Kept for diagnosis; it
    #: is not the audio's resolution -- see quality.
    sample_format: str = ""

    def __bool__(self) -> bool:
        return bool(self.node)

    @property
    def is_music(self) -> bool:
        return self.profile.startswith("a2dp")

    @property
    def quality(self) -> str:
        """The sample rate the audio actually arrives at."""
        return f"{self.rate / 1000:g} kHz" if self.rate else ""


#: Audio received over the hands-free profile is telephone quality -- 8 or 16
#: kHz, mono -- because that profile carries a call, not music.
MUSIC_PROFILE_PREFIX = "a2dp"


def phone_streams() -> list[Stream]:
    """Every stream the phone is currently sending."""
    result = run(["pw-dump"], timeout=10.0)
    if not result.ok:
        return []
    try:
        objects = json.loads(result.stdout)
    except ValueError:
        return []

    streams = []
    for entry in objects:
        props = ((entry.get("info") or {}).get("props")) or {}
        name = str(props.get("node.name", ""))
        if not name.startswith("bluez_input"):
            continue
        # node.rate reads "1/44100": the clock period, not the rate.
        rate = str(props.get("node.rate", ""))
        _, _, denominator = rate.partition("/")
        formats = ((entry.get("info") or {}).get("params") or {}).get("Format") or []
        shape = formats[0] if formats else {}
        streams.append(
            Stream(
                node=name,
                profile=str(props.get("api.bluez5.profile", "")),
                codec=str(props.get("api.bluez5.codec", "")),
                rate=int(shape.get("rate") or (denominator if denominator.isdigit() else 0)),
                channels=int(shape.get("channels") or props.get("audio.channels", 0) or 0),
                sample_format=str(shape.get("format", "")),
            )
        )
    return streams


def music_stream() -> Stream:
    """The stream carrying music, never the one carrying a call."""
    for stream in phone_streams():
        if stream.is_music:
            return stream
    return Stream()


def phone_stream() -> str:
    """Name of the music stream's node, or empty while none is flowing."""
    return music_stream().node


def wait_for_stream(attempts: int = 20) -> Stream:
    """Wait for the phone to actually start sending music."""
    import time

    for _attempt in range(attempts):
        stream = music_stream()
        if stream:
            return stream
        time.sleep(0.5)
    return Stream()


#: Received Bluetooth audio is not a PipeWire source, so pactl cannot see it
#: at all -- these two are the only way to find the node and connect it.
PW_TOOLS = ("pw-dump", "pw-link")


def tools_missing() -> list[str]:
    """PipeWire command-line tools that are needed and absent."""
    return [tool for tool in PW_TOOLS if not have(tool)]


def link_to_sink(node: str, sink: str = "") -> None:
    """Play a received stream through a sink."""
    missing = tools_missing()
    if missing:
        # Worth saying plainly.
        raise RuntimeError(
            f"{' and '.join(missing)} {'are' if len(missing) > 1 else 'is'} missing, "
            "so the phone's audio cannot be connected to the speakers. "
            + packages.advice("pipewire-tools")
        )
    target = sink or default_sink()
    if not target:
        raise RuntimeError("No output device to play the phone through.")
    result = run(["pw-link", node, target], timeout=15.0)
    # Re-linking an existing link is not an error worth surfacing.
    if not result.ok and "exists" not in result.text.lower():
        raise RuntimeError(result.text or "Could not route the phone's audio.")


def stream_linked(node: str) -> bool:
    """Whether a stream node is already connected to something audible."""
    if not node:
        return False
    result = run(["pw-link", "-l", node], timeout=10.0)
    return result.ok and "|->" in result.stdout


def unlink_from_sink(node: str, sink: str = "") -> None:
    target = sink or default_sink()
    if node and target:
        run(["pw-link", "-d", node, target], timeout=10.0)


def default_sink() -> str:
    result = run([PACTL, "get-default-sink"], timeout=8.0)
    return result.stdout.strip() if result.ok else ""


def nodes_for(card_name: str) -> tuple[str, str]:
    """The sink and source names belonging to *card_name*."""
    device = card_name.replace("bluez_card.", "")
    sink = source = ""

    result = run([PACTL, "-f", "json", "list", "sinks"], timeout=15.0)
    if result.ok:
        try:
            for entry in json.loads(result.stdout):
                if device in entry.get("name", ""):
                    sink = entry["name"]
                    break
        except ValueError:
            pass

    result = run([PACTL, "-f", "json", "list", "sources"], timeout=15.0)
    if result.ok:
        try:
            for entry in json.loads(result.stdout):
                name = entry.get("name", "")
                # Skip the monitor of the sink; it is not the phone's mic.
                if device in name and not name.endswith(".monitor"):
                    source = name
                    break
        except ValueError:
            pass
    return sink, source


# -- Windows -----------------------------------------------------------------
#
# No PipeWire, no cards and no nodes to link: an open AudioPlaybackConnection
# plays through Windows' default output by itself. What is left is one profile,
# the stream while it is open, and nothing to route.
from ..core import platform as _platform                              # noqa: E402

#: The phone's music, played here.
WINDOWS_PROFILE = "a2dp-sink"
#: The phone's calls, through PhoneLineTransportDevice.
WINDOWS_CALLS = "handsfree-head-unit"

if _platform.IS_WINDOWS:
    def available() -> bool:                                          # noqa: F811
        return True

    def bluetooth_card(address: str = "") -> BtCard | None:          # noqa: F811
        from . import bluetooth, calls_win

        device = bluetooth.find_phone(preferred_address=address)
        if device is None or not device.connected:
            return None
        profiles = {WINDOWS_PROFILE: "A2DP"}
        if calls_win.can_take_calls(device.address):
            profiles[WINDOWS_CALLS] = "HFP"
        return BtCard(
            name=device.address,
            index=0,
            active_profile=(WINDOWS_PROFILE if bluetooth.audio_transport(device.address)
                            else OFF_PROFILE),
            profiles=list(profiles),
            profile_descriptions=profiles,
            description=device.label,
        )

    def ready_to_receive(address: str) -> str:                       # noqa: F811
        # A started connection is already ready; there is no card to switch.
        return WINDOWS_PROFILE

    def set_profile(card: str, profile: str) -> None:                # noqa: F811
        from . import calls_win

        if profile == WINDOWS_CALLS:
            calls_win.take_calls(card)
        elif profile != WINDOWS_PROFILE:
            raise RuntimeError(f"Windows has no {profile} profile.")

    def set_default_sink(name: str) -> None:                         # noqa: F811
        pass

    def set_default_source(name: str) -> None:                       # noqa: F811
        pass

    def phone_streams() -> list[Stream]:                              # noqa: F811
        from . import bluetooth_win

        return [
            Stream(node=f"bluetooth:{address}", profile="a2dp_sink")
            for address in bluetooth_win.open_addresses()
        ]

    def tools_missing() -> list[str]:                                 # noqa: F811
        return []

    def link_to_sink(node: str, sink: str = "") -> None:              # noqa: F811
        pass

    def stream_linked(node: str) -> bool:                             # noqa: F811
        return bool(node)

    def unlink_from_sink(node: str, sink: str = "") -> None:          # noqa: F811
        pass

    def default_sink() -> str:                                        # noqa: F811
        return ""

    def nodes_for(card_name: str) -> tuple[str, str]:                 # noqa: F811
        return "", ""
