#!/usr/bin/env python3
"""Checks the Bluetooth connection without touching a Bluetooth adapter."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Before any tessera import: a check must never write the real configuration.
from sandbox import isolate                                          # noqa: E402

isolate()

from PySide6.QtWidgets import QApplication                           # noqa: E402

from tessera.backends import bluetooth                               # noqa: E402
from tessera.backends import audio as bt_audio                       # noqa: E402
from tessera.core import hub as hub_module                           # noqa: E402
from tessera.core.config import BluetoothConfig, Config              # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


class Radio:
    """Stands in for BlueZ, recording what was asked of it."""

    def __init__(self, connected: bool = False, brings_up_audio: bool = False):
        self.calls: list[str] = []
        self.connected = connected
        #: Some phones bring the media profile up regardless; the connect has
        #: to notice and hand it straight back.
        self.brings_up_audio = brings_up_audio

    def install(self) -> None:
        radio = self

        class Device:
            address = "AA:BB:CC:DD:EE:FF"
            label = "Test phone"
            name = "Test phone"

            @property
            def connected(self) -> bool:
                return radio.connected

        def find_phone(preferred_address: str = "", name_hint: str = ""):
            radio.calls.append("find_phone")
            return Device()

        def connect(address, timeout=25.0):
            radio.calls.append("connect")          # the full one: brings up A2DP
            radio.connected = True

        def connect_quietly(address, timeout=25.0):
            radio.calls.append("connect_quietly")
            radio.connected = True

        def disconnect(address, timeout=20.0):
            radio.calls.append("disconnect")
            radio.connected = False

        def release_audio(address):
            radio.calls.append("release_audio")
            return True

        def audio_connected(device):
            return radio.brings_up_audio

        bluetooth.find_phone = find_phone
        bluetooth.connect = connect
        bluetooth.connect_quietly = connect_quietly
        bluetooth.disconnect = disconnect
        bluetooth.release_audio = release_audio
        bluetooth.audio_connected = audio_connected
        bt_audio.ready_to_receive = lambda address: radio.calls.append("ready_to_receive")


def connects(radio: "Radio") -> list[str]:
    """Connection attempts only."""
    return [call for call in radio.calls if call.startswith("connect")]


def settle(app: QApplication, rounds: int = 60) -> None:
    """Let the worker finish and its callback arrive on this thread."""
    from time import sleep
    for _ in range(rounds):
        app.processEvents()
        sleep(0.01)


def make_hub(**overrides):
    config = Config()
    for key, value in overrides.items():
        setattr(config.bluetooth, key, value)
    config.bluetooth.address = "AA:BB:CC:DD:EE:FF"
    # The codec machinery writes WirePlumber configuration and restarts the
    # audio service; neither belongs in a check.
    hub_module.Hub._apply_codec_preference = lambda self: None
    return hub_module.Hub(config), config


def defaults() -> None:
    print("-- what a fresh install does")
    check("Bluetooth connects on its own", BluetoothConfig().autoconnect is True)
    check(
        "and does not start streaming when it does",
        BluetoothConfig().auto_stream is False,
    )
    check("nor reroute a call by itself", BluetoothConfig().route_calls is False)


def connecting(app: QApplication) -> None:
    print("\n-- connecting moves no audio")
    radio = Radio()
    radio.install()
    hub, _config = make_hub()
    hub.connect_bluetooth()
    settle(app)

    check(
        "the quiet connect is used, never the full one",
        "connect_quietly" in radio.calls and "connect" not in radio.calls,
        ", ".join(radio.calls),
    )
    check(
        "the card is left able to accept a stream",
        "ready_to_receive" in radio.calls,
        ", ".join(radio.calls),
    )

    # A phone that brings A2DP up anyway must have it handed back.
    radio = Radio(brings_up_audio=True)
    radio.install()
    hub, _config = make_hub()
    hub.connect_bluetooth()
    settle(app)
    check(
        "a media profile that comes up uninvited is handed back",
        "release_audio" in radio.calls,
        ", ".join(radio.calls),
    )

    # The setting for people who want the opposite is still honoured.
    radio = Radio()
    radio.install()
    hub, _config = make_hub(auto_stream=True)
    hub.connect_bluetooth()
    settle(app)
    check(
        "auto_stream still connects fully, for those who ask for it",
        "connect" in radio.calls and "connect_quietly" not in radio.calls,
        ", ".join(radio.calls),
    )


def intent(app: QApplication) -> None:
    print("\n-- a phone disconnected on purpose stays disconnected")
    radio = Radio(connected=True)
    radio.install()
    hub, _config = make_hub()
    hub._bluetooth_settled = True

    hub.disconnect_bluetooth()
    settle(app)
    check("disconnecting reaches the radio", "disconnect" in radio.calls)

    radio.calls.clear()
    hub._bluetooth_tried = 0.0        # as though the wait had passed
    hub._autoconnect_bluetooth()
    settle(app)
    check(
        "and the automatic connect does not undo it",
        not connects(radio),
        ", ".join(radio.calls),
    )

    # Pressing connect says the opposite, and is obeyed.
    radio.calls.clear()
    hub.connect_bluetooth()
    settle(app)
    check("pressing connect asks for it again", "connect_quietly" in radio.calls)


def automatic(app: QApplication) -> None:
    print("\n-- connecting by itself, without wearing the phone out")
    radio = Radio()
    radio.install()
    hub, config = make_hub()
    hub._bluetooth_settled = False

    hub._bluetooth_tried = 0.0
    hub._autoconnect_bluetooth()
    settle(app)
    check("a phone that is not connected gets connected", "connect_quietly" in radio.calls)

    radio.calls.clear()
    hub._bluetooth_settled = False
    hub._autoconnect_bluetooth()
    settle(app)
    check(
        "a second attempt straight away is not made",
        not connects(radio),
        ", ".join(radio.calls),
    )

    radio.calls.clear()
    hub._bluetooth_settled = True
    hub._bluetooth_tried = 0.0
    hub._autoconnect_bluetooth()
    settle(app)
    check("a connected phone is left alone", not connects(radio),
          ", ".join(radio.calls))

    # Switched off, in either of the two ways it can be.
    radio.calls.clear()
    hub._bluetooth_settled = False
    hub._bluetooth_tried = 0.0
    config.bluetooth.autoconnect = False
    hub._autoconnect_bluetooth()
    settle(app)
    check("the setting is honoured", not connects(radio), ", ".join(radio.calls))

    radio.calls.clear()
    config.bluetooth.autoconnect = True
    config.features.bluetooth_audio = False
    hub._bluetooth_tried = 0.0
    hub._autoconnect_bluetooth()
    settle(app)
    check("so is the feature switch", not connects(radio), ", ".join(radio.calls))


def quiet_failure(app: QApplication) -> None:
    print("\n-- a phone that is not there says nothing")
    radio = Radio()
    radio.install()

    def missing(preferred_address: str = "", name_hint: str = ""):
        radio.calls.append("find_phone")
        return None

    bluetooth.find_phone = missing
    hub, _config = make_hub()
    shouted: list[str] = []
    hub.errorOccurred.connect(shouted.append)

    hub._bluetooth_settled = False
    hub._bluetooth_tried = 0.0
    hub._autoconnect_bluetooth()
    settle(app)
    check(
        "an automatic attempt that fails is not put on screen",
        not shouted,
        "; ".join(shouted),
    )

    # Pressed by hand, the same failure has to be visible.
    shouted.clear()
    hub.connect_bluetooth()
    settle(app)
    check(
        "but pressing the button and failing is",
        any("No paired phone" in message for message in shouted),
        "; ".join(shouted) or "nothing said",
    )


def button(app: QApplication) -> None:
    print("\n-- the button in the sidebar")
    from tessera.ui.panel import DevicePanel
    from tessera.ui.theme import detect_palette

    radio = Radio()
    radio.install()
    hub, config = make_hub()
    panel = DevicePanel(hub, detect_palette(app))

    check("the button is there", panel.bluetooth_button is not None)
    check("it is next to the refresh button",
          panel.bluetooth_button.parent() is panel.reconnect_button.parent())
    check(
        "it promises not to move any audio",
        "does not move any audio" in panel.bluetooth_button.toolTip(),
        panel.bluetooth_button.toolTip(),
    )

    panel.bluetooth_button.click()
    settle(app)
    check("pressing it connects", "connect_quietly" in radio.calls, ", ".join(radio.calls))

    radio.calls.clear()
    hub._note_bluetooth({"connected": True, "name": "Test phone"})
    app.processEvents()
    check(
        "then it offers to disconnect",
        panel.bluetooth_button.toolTip() == "Disconnect Bluetooth",
        panel.bluetooth_button.toolTip(),
    )
    panel.bluetooth_button.click()
    settle(app)
    check("and does", "disconnect" in radio.calls, ", ".join(radio.calls))

    # Switched off in settings, the button has nothing to offer.
    config.features.bluetooth_audio = False
    panel.refresh_header()
    check(
        "it goes away with the feature",
        not panel.bluetooth_button.isVisibleTo(panel),
    )


def main() -> int:
    app = QApplication(sys.argv)
    defaults()
    connecting(app)
    intent(app)
    automatic(app)
    quiet_failure(app)
    button(app)

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed:")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("\nall Bluetooth checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
