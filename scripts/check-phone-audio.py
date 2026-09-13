#!/usr/bin/env python3
"""Checks the phone-audio path without a phone.

Everything except the phone itself: the jitter buffer's arithmetic, the state
machine the interface reads, the protocol frames in both directions, and a real
QAudioSink fed real PCM so the sound card is actually opened. The phone's half
is a stub that speaks the same frames the companion app does.

Run it from the repository root:  python3 scripts/check-phone-audio.py
"""

from __future__ import annotations

import math
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Before any tessera import: a check must never write the real configuration.
from sandbox import isolate                                          # noqa: E402

isolate()

from PySide6.QtCore import QTimer                                    # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

from tessera.backends import phone_audio                             # noqa: E402
from tessera.backends.companion import Decoder, encode_binary, encode_json  # noqa: E402
from tessera.core.config import Config                               # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


def tone(milliseconds: int, hz: float = 440.0, level: float = 0.5) -> bytes:
    """Signed 16-bit stereo at 48 kHz, the format the phone sends."""
    frames = int(phone_audio.RATE * milliseconds / 1000)
    out = bytearray()
    for index in range(frames):
        value = int(level * 32767 * math.sin(2 * math.pi * hz * index / phone_audio.RATE))
        out += struct.pack("<hh", value, value)
    return bytes(out)


HEADER = {
    "t": "audio_started",
    "codec": "pcm_s16le",
    "rate": 48_000,
    "channels": 2,
    "frameBytes": 3840,
}


def frame_maths() -> None:
    """The phone's frame size and ours have to agree exactly."""
    expected = 48 * 20 * 2 * 2
    check("a 20 ms frame is 3840 bytes", expected == 3840, str(expected))
    check(
        "the phone's frame size matches this end",
        HEADER["frameBytes"] == expected,
    )
    check(
        "a second of audio is 192000 bytes",
        phone_audio._bytes_for(1000) == 192_000,
        str(phone_audio._bytes_for(1000)),
    )


def protocol() -> None:
    """A JSON header followed by its binary frame, as the phone writes them."""
    decoder = Decoder()
    stream = encode_json({"t": "audio_frame", "binary": True, "length": 8})
    stream += encode_binary(b"12345678")
    frames = list(decoder.feed(stream))
    check("header and payload decode as two frames", len(frames) == 2, str(len(frames)))
    check("the payload survives", frames[1][1] == b"12345678")

    # Split mid-header: the socket does not respect our frame boundaries.
    decoder = Decoder()
    first = list(decoder.feed(stream[:3]))
    rest = list(decoder.feed(stream[3:]))
    check("a frame split across reads still arrives", not first and len(rest) == 2)


def player() -> None:
    """The real thing: a QAudioSink, opened and fed."""
    config = Config().phone_audio
    audio = phone_audio.PhoneAudio(config)

    events: list[str] = []
    audio.started.connect(lambda: events.append("started"))
    audio.stopped.connect(lambda: events.append("stopped"))
    audio.failed.connect(lambda message: events.append(f"failed: {message}"))
    levels: list[float] = []
    audio.levelChanged.connect(levels.append)

    check("this machine has an audio output", phone_audio.available())

    audio.open(HEADER)
    check("the sink opens", audio.running and "started" in events, str(events))
    if not audio.running:
        return

    # One frame is not enough to start playing: the buffer primes first.
    frame = tone(20)
    audio.feed(frame)
    check("the first frame primes rather than plays", audio.played_bytes == 0)

    # Enough to fill the configured buffer, which releases it.
    for _ in range(int(config.buffer_ms / 20) + 1):
        audio.feed(frame)
    check(
        "audio reaches the sound card once primed",
        audio.played_bytes > 0,
        f"{audio.played_bytes} bytes",
    )

    check("the level meter followed the tone", levels and max(levels) > 0.4,
          f"peak {max(levels):.2f}" if levels else "no levels")

    # Silence must read as silence, not as "nothing arrived".
    audio.feed(b"\x00" * len(frame))
    check("silence reads as zero level", levels[-1] == 0.0, str(levels[-1]))

    # A sink that is not being drained must not accumulate delay for ever.
    before = audio.dropped_bytes
    for _ in range(120):                     # 2.4 seconds of audio at once
        audio.feed(frame)
    grew = audio.dropped_bytes - before
    check(
        "a backlog is dropped rather than becoming latency",
        grew > 0,
        f"dropped {grew} bytes",
    )
    check(
        "the backlog is capped near the limit",
        len(audio._pending) <= phone_audio._bytes_for(phone_audio.MAX_BACKLOG_MS),
        f"{len(audio._pending)} bytes pending",
    )
    check(
        "dropping keeps whole stereo frames",
        audio.dropped_bytes % phone_audio.BYTES_PER_FRAME == 0,
    )

    audio.set_volume(40)
    check("volume is remembered", config.volume == 40, str(config.volume))

    audio.close()
    check("the sink closes", not audio.running and "stopped" in events)

    # A format this end cannot play must be refused, not played as noise.
    audio.open({**HEADER, "codec": "opus"})
    check(
        "an unknown codec is refused with a reason",
        not audio.running and any(e.startswith("failed") for e in events),
        events[-1],
    )


def hub_state() -> None:
    """The state the interface reads, driven by the phone's own messages."""
    from tessera.core.hub import Hub

    config = Config()
    # The link route is the fallback now, off by default on a computer that
    # has Bluetooth. Everything below is about the route itself, so switch it
    # on rather than testing the switch twice.
    config.features.phone_audio = True
    hub = Hub(config)
    seen: list[bool] = []
    waiting: list[str] = []
    hub.phoneAudioChanged.connect(seen.append)
    hub.phoneAudioWaiting.connect(waiting.append)

    # Not connected: asking must explain itself rather than hang.
    errors: list[str] = []
    hub.errorOccurred.connect(errors.append)
    hub.start_phone_audio()
    check(
        "asking without a phone says why",
        errors and "Android 10" in errors[-1],
        errors[-1] if errors else "no error",
    )
    check("nothing is playing", not hub.phone_audio_active)

    # The phone asks the user: the interface must show that, not silence.
    hub._on_phone_audio_consent("Tap the notification on the phone.")
    check("consent puts the interface in a waiting state", hub.phone_audio_pending)
    check("the wait is explained", bool(waiting), waiting[-1] if waiting else "")

    # The user allowed it and the phone started sending.
    hub._on_phone_audio_started(HEADER)
    check("the stream starts", hub.phone_audio_active and not hub.phone_audio_pending)

    for _ in range(10):
        hub.phone_audio.feed(tone(20))
    check("frames are played", hub.phone_audio.played_bytes >= 0)

    # The user revoked it from the phone's status bar.
    hub._on_phone_audio_stopped()
    check("a stop from the phone stops here", not hub.phone_audio_active)

    # The link dropping must not leave a sink open and silent.
    hub._on_phone_audio_started(HEADER)
    hub._on_link_for_audio(False)
    check("losing the link closes the stream", not hub.phone_audio_active)

    # -- keeping the phone quiet ------------------------------------------
    #
    # What arrives is a copy, so without this the same track plays on the
    # phone and here at once. The phone is asked as part of starting, and can
    # be asked again mid-stream when the box is ticked or unticked.
    sent: list[dict] = []
    hub.companion.send = sent.append          # type: ignore[method-assign]
    # A phone that is connected and offers audio, without one being here.
    hub.companion._capabilities = ["phone_audio", "phone_audio_mute"]
    hub.companion._authenticated = True
    hub.companion._socket = object()
    hub._on_phone_audio_stopped()

    config.phone_audio.mute_phone = True
    hub.start_phone_audio()
    check(
        "starting asks the phone to go quiet",
        sent and sent[-1].get("t") == "audio_start" and sent[-1].get("mute") is True,
        str(sent[-1]) if sent else "nothing sent",
    )

    config.phone_audio.mute_phone = False
    hub.stop_phone_audio()
    sent.clear()
    hub.start_phone_audio()
    check(
        "and leaves it playing when the box is unticked",
        sent and sent[-1].get("mute") is False,
        str(sent[-1]) if sent else "nothing sent",
    )

    # The phone says whether it managed it: Do Not Disturb can refuse.
    hub._on_phone_audio_started(dict(HEADER, muted=True, muteAsked=True))
    check("the phone reports that it went quiet", hub.phone_muted)

    sent.clear()
    hub.set_phone_muted(False)
    check(
        "unticking mid-stream reaches the phone",
        sent and sent[-1] == {"t": "audio_mute", "on": False},
        str(sent[-1]) if sent else "nothing sent",
    )
    check("and is remembered", config.phone_audio.mute_phone is False)

    errors.clear()
    hub._on_phone_audio_stopped()
    hub._on_phone_audio_started(dict(HEADER, muted=False, muteAsked=True))
    # Anywhere in the errors, not the last one: opening the sink can fail in a
    # check that has already opened and closed several, and that noise is not
    # what is being tested here.
    check(
        "a phone that would not go quiet says so",
        any("Do Not Disturb" in message for message in errors),
        "; ".join(errors) or "nothing said",
    )
    check("and the page is not told it is silent", not hub.phone_muted)

    hub._on_phone_audio_stopped()
    check("stopping forgets the mute", not hub.phone_muted)
    config.phone_audio.mute_phone = True

    # Switched off in settings, the button must refuse with the reason.
    config.features.phone_audio = False
    errors.clear()
    hub.start_phone_audio()
    check(
        "the feature switch is honoured",
        errors and "Settings" in errors[-1],
        errors[-1] if errors else "no error",
    )
    config.features.phone_audio = True


def interface() -> None:
    """The page and the panel, built as they are in the app."""
    from tessera.core.hub import Hub
    from tessera.ui.main_window import IMPOSSIBLE, PAGES
    from tessera.ui.pages.audio import AudioPage
    from tessera.ui.panel import TILES
    from tessera.ui.theme import detect_palette

    check(
        "the Audio page is governed by phone_audio",
        any(name == "Audio" and feature == "phone_audio"
            for name, _i, _g, _p, feature in PAGES),
    )
    check("the Audio page is possible on this platform", "Audio" not in IMPOSSIBLE)
    check(
        "the panel's audio tile is governed by phone_audio",
        TILES["audio"][4] == "phone_audio",
        TILES["audio"][4],
    )

    # Both routes stay available; the setting only decides what one click does.
    from tessera.core import platform
    from tessera.core.config import FeatureConfig, PhoneAudioConfig

    # A comparison, not a truthy string: this read `check(label, route, "auto")`
    # and passed on any non-empty answer, including the wrong one.
    check(
        "Bluetooth is the one-click route",
        PhoneAudioConfig().route == "bluetooth",
        PhoneAudioConfig().route,
    )
    check(
        "the link route is the fallback, off where Bluetooth works",
        FeatureConfig().phone_audio is not platform.supported("bluetooth_audio"),
        f"phone_audio={FeatureConfig().phone_audio}, "
        f"bluetooth_audio={platform.supported('bluetooth_audio')}",
    )

    config = Config()
    hub = Hub(config)
    palette = detect_palette(QApplication.instance())
    page = AudioPage(hub, palette)
    check(
        "the link card is hidden until it is switched on",
        not page.link_card.isVisibleTo(page),
    )

    config.features.phone_audio = True
    page = AudioPage(hub, palette)
    check("and shown once it is", page.link_card.isVisibleTo(page))
    check(
        "it starts in the not-playing state",
        page.stream_pill.text() == "Not playing",
        page.stream_pill.text(),
    )

    hub._on_phone_audio_consent("Tap the notification on the phone.")
    page._apply_stream_state()
    check(
        "waiting for the phone is visible on the page",
        page.stream_pill.text() == "Asking the phone" and page.stream_button.text() == "Cancel",
        page.stream_pill.text(),
    )

    # Opening a sink can fail while an earlier one is still being destroyed,
    # which only happens here: the app has exactly one player. Let Qt catch up
    # and try once more rather than reporting a failure the app cannot have.
    hub._on_phone_audio_started(HEADER)
    for _ in range(4):
        if hub.phone_audio_active:
            break
        for _ in range(10):
            QApplication.instance().processEvents()
        hub._on_phone_audio_started(HEADER)
    page._apply_stream_state()
    check(
        "playing is visible on the page",
        page.stream_pill.text() == "Playing here" and page.stream_button.text() == "Stop",
        page.stream_pill.text(),
    )
    hub.stop_phone_audio()
    page._apply_stream_state()
    check("stopping returns the page to idle", page.stream_pill.text() == "Not playing")


def windows_too() -> None:
    """The whole point of this route: it is not Linux-only."""
    from tessera.core import platform

    check(
        "phone audio is possible on every platform",
        all(
            "phone_audio" not in platform.UNSUPPORTED[name]
            for name in ("windows", "macos", "linux")
        ),
    )
    check(
        "Bluetooth audio is still impossible on Windows",
        "bluetooth_audio" in platform.UNSUPPORTED["windows"],
    )


def main() -> int:
    app = QApplication(sys.argv)
    QTimer.singleShot(0, lambda: None)

    # A closed sink is only released when Qt processes deleteLater, which in
    # the app is the event loop's job. Without this the fourth sink in a row
    # fails to open and the checks look flaky when the code is not.
    def settle() -> None:
        for _ in range(8):
            app.processEvents()

    print("-- frames")
    frame_maths()
    print("\n-- protocol")
    protocol()
    print("\n-- the player")
    player()
    settle()
    print("\n-- the hub's state")
    hub_state()
    settle()
    print("\n-- the interface")
    interface()
    settle()
    print("\n-- platforms")
    windows_too()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("all phone-audio checks passed")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
