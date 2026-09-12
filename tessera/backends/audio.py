"""Audio routing for the Bluetooth link.

Once the phone is connected, PipeWire exposes it as a card with two useful
profiles:

* **a2dp-sink** -- high quality, one direction. Music from the phone plays on
  the computer, and the microphone is not involved.
* **headset-head-unit** (HFP/HSP) -- both directions at telephone quality, with
  the microphone live. This is what a call needs.

They are mutually exclusive because Bluetooth only carries one at a time, which
is why switching profiles is an explicit action rather than something the app
can quietly do for both at once.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..core.proc import have, run

log = logging.getLogger(__name__)

PACTL = "pactl"

#: Profile names vary with the negotiated codec (a2dp-sink-sbc, a2dp-sink-aptx)
#: and with which end plays which role, so names alone are unreliable: a phone
#: streaming to this computer shows up as "audio-gateway", whose description
#: reads "Audio Gateway (A2DP Source & HSP/HFP AG)" -- those are the *phone's*
#: roles. Descriptions state the roles plainly, so they are matched first.
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
        """Best available profile for a purpose.

        Descriptions are consulted first because they say which roles a profile
        actually carries; names are a fallback for devices that give terse ones.
        Profiles keep PipeWire's own ordering, which puts better codecs first.
        """
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
    """The PipeWire card for a connected Bluetooth device.

    Returns None while the device is disconnected: PipeWire only creates the
    card once the audio link is up, so its absence is the normal state rather
    than an error.
    """
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
#:
#: Tessera does not select it any more. WirePlumber remembers the last profile
#: chosen for a card and restores it on every later connection, so parking this
#: way left "off" stored as the device's default: the phone would start its
#: stream, find nothing on this side willing to take it, and give up after a
#: few seconds. That is why the audio never arrived. Handing playback back to
#: the phone is done by dropping the Bluetooth profile instead, which is the
#: level Android actually listens to.
OFF_PROFILE = "off"


def ready_to_receive(address: str) -> str:
    """Make sure the card can accept audio, and say which profile it uses.

    Called when the phone connects rather than when a stream is wanted: the
    phone offers its stream immediately on connecting and withdraws it within
    seconds, which is far too fast for a profile switch made in response.
    """
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
        """The sample rate the audio actually arrives at.

        Deliberately not a bit depth. sample_format is the width of the words
        PipeWire decodes *into*, which says nothing about the audio: aptX
        carries 16-bit samples and lands in S24LE, and LDAC decoded here is
        16 bit whichever width it is widened to. Printing that number reads as
        a claim about resolution and would be wrong nearly every time. Sample
        rate is real, and the codec's bit rate -- which the Audio page pairs
        with it -- is the figure that actually differs between codecs.
        """
        return f"{self.rate / 1000:g} kHz" if self.rate else ""


#: Audio received over the hands-free profile is telephone quality -- 8 or 16
#: kHz, mono -- because that profile carries a call, not music. Linking it to
#: the speakers as though it were music is a mistake worth refusing: it sounds
#: broken, and on the phone's side bringing up the call profile suspends its
#: music entirely.
MUSIC_PROFILE_PREFIX = "a2dp"


def phone_streams() -> list[Stream]:
    """Every stream the phone is currently sending.

    PipeWire does not present these as sources. Each appears as a
    Stream/Output/Audio node named bluez_input.<address>.<n> -- effectively a
    playback stream, as though the phone were an application on this machine.
    They exist only while audio is actually flowing.
    """
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
        # Worth saying plainly. These live in a separate package that a
        # PipeWire system does not necessarily have, and without this the
        # failure surfaces as the bare words "pw-link: not found".
        raise RuntimeError(
            f"{' and '.join(missing)} {'are' if len(missing) > 1 else 'is'} missing, "
            "so the phone's audio cannot be connected to the speakers. "
            "Install them with: sudo dnf install pipewire-utils"
        )
    target = sink or default_sink()
    if not target:
        raise RuntimeError("No output device to play the phone through.")
    result = run(["pw-link", node, target], timeout=15.0)
    # Re-linking an existing link is not an error worth surfacing.
    if not result.ok and "exists" not in result.text.lower():
        raise RuntimeError(result.text or "Could not route the phone's audio.")


def stream_linked(node: str) -> bool:
    """Whether a stream node is already connected to something audible.

    WirePlumber links the node itself when its policy allows it, so checking
    first keeps Tessera from stacking a second, duplicate link on top.
    """
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
    """The sink and source names belonging to *card_name*.

    Used to make the phone the default device when a call starts, so the audio
    lands somewhere the user can actually hear.
    """
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
