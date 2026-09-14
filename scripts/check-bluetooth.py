#!/usr/bin/env python3
"""Checks the Bluetooth connection without touching a Bluetooth adapter."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Before any tessera import: a check must never write the real configuration.
from sandbox import escaped, isolate, only_on                        # noqa: E402

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


def raised(action) -> str:
    """What *action* raised, or '' when it did not."""
    try:
        action()
    except (RuntimeError, OSError) as exc:
        return str(exc)
    return ""


class Radio:
    """Stands in for BlueZ, recording what was asked of it."""

    def __init__(self, connected: bool = False, brings_up_audio: bool | int = False):
        self.calls: list[str] = []
        self.connected = connected
        #: How many times the phone brings the media profile up by itself.
        self.audio_ups = int(brings_up_audio)
        self.transport = ""

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
            radio.audio_ups = max(0, radio.audio_ups - 1)
            radio.transport = ""
            return True

        def audio_connected(device):
            return radio.audio_ups > 0

        bluetooth.find_phone = find_phone
        bluetooth.connect = connect
        bluetooth.connect_quietly = connect_quietly
        bluetooth.disconnect = disconnect
        bluetooth.release_audio = release_audio
        bluetooth.audio_connected = audio_connected
        bluetooth.media_state = lambda address: (radio.transport, [])
        bt_audio.ready_to_receive = lambda address: radio.calls.append("ready_to_receive")
        bt_audio.phone_stream = lambda: ""


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
    hub_module.Hub.BLUETOOTH_SETTLE_SECONDS = 0.04
    hub_module.Hub.RESUME_DELAY_MS = 10
    return hub_module.Hub(config), config


def fake_phone_link(hub, playing: bool) -> list[dict]:
    """A connected companion link that records what is sent."""
    sent: list[dict] = []
    hub.companion._authenticated = True
    hub.companion._socket = object()
    hub.companion.send = sent.append
    hub._on_media({"t": "media", "playing": playing})
    return sent


# -- Linux: BlueZ and PipeWire ------------------------------------------------


@only_on("linux")
def bluez() -> None:
    print("-- Linux: BlueZ and PipeWire")
    from tessera.core import platform

    check("BlueZ answers for the Bluetooth module",
          bluetooth.connect_quietly.__module__ == "tessera.backends.bluetooth",
          bluetooth.connect_quietly.__module__)
    check("the codec can be chosen", platform.supported("bluetooth_codecs"))
    check("and a call's audio moved here", platform.supported("bluetooth_calls"))

    said = bluetooth.explain(
        "Failed to connect: org.bluez.Error.Failed br-connection-key-missing", "AA"
    )
    check("a stale pairing says to pair again", "pair again" in said, said)
    check("LDAC is recognised from its vendor id",
          bluetooth._codec_name(bluetooth.CODEC_VENDOR, [0x2D, 0x01, 0, 0, 0xAA, 0]) == "ldac")

    device = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    objects = {
        device: {},
        device + "/sep1/fd0": {"org.bluez.MediaTransport1": {"State": {"data": "idle"}}},
        device + "/sep2/fd1": {"org.bluez.MediaTransport1": {"State": {"data": "active"}}},
    }
    check("a streaming endpoint wins over an idle one",
          bluetooth._transport_of(objects, "AA:BB:CC:DD:EE:FF") == "active")

    card = bt_audio.BtCard(
        name="bluez_card.AA_BB_CC_DD_EE_FF", index=1,
        profiles=["off", "a2dp-sink", "headset-head-unit"],
        profile_descriptions={
            "a2dp-sink": "High Fidelity Playback (A2DP Sink)",
            "headset-head-unit": "Headset Head Unit (HSP/HFP)",
        },
    )
    check("the music profile is found by its description",
          card.music_profile == "a2dp-sink", card.music_profile)
    check("and the call profile", card.call_profile == "headset-head-unit", card.call_profile)


# -- Windows: AudioPlaybackConnection -----------------------------------------

#: An endpoint id as Windows 11 lists it for a Galaxy S25.
ENDPOINT = (
    "\\\\?\\BTHENUM#{0000110a-0000-1000-8000-00805f9b34fb}_VID&00010075_PID&0100"
    "#b&3430c306&0&08023C81E037_C00000000#{6994ad04-93ef-11d0-a3cc-00a0c9223196}\\SNK"
)
PHONE = "08:02:3C:81:E0:37"


class Done:
    """A WinRT operation that has already finished."""

    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value


class Connection:
    """An AudioPlaybackConnection, recording what was done to it."""

    def __init__(self, device_id: str, open_status: int):
        self.device_id = device_id
        self.open_status = open_status
        self.state = 0
        self.started = False
        self.closed = False

    def start_async(self):
        self.started = True
        return Done()

    def open_async(self):
        if self.open_status == 0:
            self.state = 1
        return Done(type("Result", (), {"status": self.open_status})())

    def close(self):
        self.closed, self.state = True, 0


class WinRT:
    """DeviceInformation and AudioPlaybackConnection, without Windows."""

    def __init__(self):
        self.devices: list[tuple[str, str]] = []
        self.made: list[Connection] = []
        self.open_status = 0
        self.refuse = False

    def install(self, win) -> None:
        fake = self

        class DeviceInformation:
            @staticmethod
            def find_all_async_aqs_filter(_selector):
                return Done([type("Info", (), {"id": i, "name": n})() for i, n in fake.devices])

        class AudioPlaybackConnection:
            @staticmethod
            def get_device_selector():
                return "System.Devices.InterfaceClassGuid"

            @staticmethod
            def try_create_from_id(device_id):
                if fake.refuse:
                    return None
                made = Connection(device_id, fake.open_status)
                fake.made.append(made)
                return made

        win._winrt = lambda: (DeviceInformation, AudioPlaybackConnection)
        # Radio links by address; one not listed is taken as up.
        self.links: dict[str, bool] = {}
        win.link_states = lambda: dict(fake.links)
        win._connections.clear()
        win._ids.clear()


@only_on("windows")
def audio_playback_connection() -> None:
    print("-- Windows: AudioPlaybackConnection")
    from tessera.backends import bluetooth_win as win
    from tessera.core import platform

    check("WinRT answers for the Bluetooth module",
          bluetooth.connect_quietly is win.connect_quietly,
          bluetooth.connect_quietly.__module__)
    check("Bluetooth audio is offered", platform.supported("bluetooth_audio"))
    check("the codec is Windows' to choose", not platform.supported("bluetooth_codecs"))
    check("but a call's audio can come here", platform.supported("bluetooth_calls"))
    check("this Python can reach WinRT", win.available(),
          "install the winrt-Windows.Media.Audio wheels, as build-windows.ps1 does")
    if win.available():
        from winrt.windows.foundation import IAsyncAction, IAsyncOperation

        check("WinRT work can be waited for on a worker thread",
              hasattr(IAsyncAction, "get") and hasattr(IAsyncOperation, "get"))

    check("the radio address is read from the endpoint id",
          win.address_of(ENDPOINT) == PHONE, win.address_of(ENDPOINT))
    check("an id without one gives none", win.address_of("SWD\\MMDEVAPI\\{0.0.0}") == "")

    fake = WinRT()
    fake.install(win)
    win._sleep = lambda _seconds: None
    fake.devices = [(ENDPOINT, "Ruchit's S25")]
    phone = bluetooth.find_phone()
    check("a lone audio source is taken to be the phone",
          phone is not None and phone.address == PHONE, str(phone))
    check("one that can send music here", phone is not None and phone.can_stream_music)
    check("and is not connected until asked", phone is not None and not phone.connected)

    fake.devices.append((ENDPOINT.replace("08023C81E037", "A0B1C2D3E4F5"), "Tablet"))
    check("with two and nothing to go on, neither is guessed", bluetooth.find_phone() is None)
    check("the phone's name picks it",
          getattr(bluetooth.find_phone(name_hint="S25"), "address", "") == PHONE)
    check("so does its remembered address",
          getattr(bluetooth.find_phone(preferred_address="a0:b1:c2:d3:e4:f5"), "name", "")
          == "Tablet")

    print("-- connecting moves no audio, on Windows too")
    bluetooth.connect_quietly(PHONE)
    check("the quiet connect starts a connection",
          len(fake.made) == 1 and fake.made[0].started)
    check("and opens nothing, so no audio moves",
          fake.made[0].state == 0 and bluetooth.audio_transport(PHONE) == "")
    check("the phone now reads as connected", bluetooth.find_phone(name_hint="S25").connected)
    fake.links[PHONE] = False
    check("but not once its radio link is down",
          not bluetooth.find_phone(name_hint="S25").connected)
    fake.links.clear()
    bluetooth.connect_quietly(PHONE)
    check("connecting again uses the same one", len(fake.made) == 1, f"{len(fake.made)} made")

    check("asking for the audio opens it",
          bluetooth.claim_audio(PHONE) and bluetooth.audio_transport(PHONE) == "active")
    check("which reads as the media profile being up", bluetooth.audio_connected(PHONE))
    from tessera.backends import calls_win

    taken: list[str] = []
    calls_win.can_take_calls = lambda address: True
    calls_win.take_calls = taken.append
    card = bt_audio.bluetooth_card(PHONE)
    check("the Audio page gets a card with a music profile",
          card is not None and card.music_profile == bt_audio.WINDOWS_PROFILE, str(card))
    check("and a call profile where Windows lists the phone's calls",
          card is not None and card.call_profile == bt_audio.WINDOWS_CALLS, str(card))
    bt_audio.set_profile(card.name, card.call_profile)
    check("choosing it hands the calls to Windows", taken == [PHONE], str(taken))
    calls_win.can_take_calls = lambda address: False
    check("no call profile where Windows lists none",
          bt_audio.bluetooth_card(PHONE).call_profile == "")
    stream = bt_audio.music_stream()
    check("the open connection is the phone's stream", bool(stream) and stream.is_music,
          str(stream))
    check("already audible, with nothing to link", bt_audio.stream_linked(stream.node)
          and not bt_audio.tools_missing())

    bluetooth.release_audio(PHONE)
    check("handing the audio back closes the open connection", fake.made[0].closed)
    check("and starts a fresh one, so the phone can come back",
          len(fake.made) == 2 and fake.made[1].started and fake.made[1].state == 0)
    check("with nothing arriving here",
          bluetooth.audio_transport(PHONE) == "" and not bt_audio.music_stream())

    bluetooth.claim_audio(PHONE)
    handed = bluetooth.keep_audio_on_phone(PHONE, settle=0.04)
    check("audio nobody asked for is handed back",
          handed >= 1 and bluetooth.audio_transport(PHONE) == "", f"handed back {handed}")

    bluetooth.disconnect(PHONE)
    check("disconnecting closes it", fake.made[-1].closed)
    check("and the phone reads as disconnected",
          not bluetooth.find_phone(name_hint="S25").connected)

    print("-- when Windows says no")
    fake.open_status = 2
    check("a refused open is a False, not an exception, when claiming",
          bluetooth.claim_audio(PHONE) is False)
    said = raised(lambda: bluetooth.connect(PHONE))
    check("and a reason when connecting", "Windows refused" in said, said)
    bluetooth.disconnect(PHONE)
    fake.open_status = 0

    fake.refuse = True
    said = raised(lambda: bluetooth.connect_quietly(PHONE))
    check("a connection Windows will not make says so", "paired" in said, said)
    fake.refuse = False

    said = raised(lambda: bluetooth.connect_quietly("11:22:33:44:55:66"))
    check("a phone Windows does not know is sent to pair it", "Pair the phone" in said, said)

    said = raised(lambda: bluetooth.forget(PHONE))
    check("forgetting points at Windows' own settings", "Bluetooth & devices" in said, said)
    win._connections.clear()


# -- the hub, on either system ------------------------------------------------


def defaults() -> None:
    print("\n-- what a fresh install does")
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


def handback(app: QApplication) -> None:
    print("\n-- the phone keeps its audio, and its music keeps playing")
    radio = Radio(brings_up_audio=2)
    radio.install()
    hub, _config = make_hub()
    hub.connect_bluetooth()
    settle(app)
    check(
        "a media profile that comes back is handed back again",
        radio.calls.count("release_audio") == 2,
        ", ".join(radio.calls),
    )
    check("and is watched for a while afterwards",
          hub._bluetooth_guard_until > hub_module.monotonic())

    # A late one, after the connect has finished.
    radio.calls.clear()
    radio.transport = "active"
    hub._watch_bluetooth()
    settle(app)
    check("a media profile that turns up later is handed back",
          "release_audio" in radio.calls, ", ".join(radio.calls))

    # Unless the user asked for a stream.
    radio.calls.clear()
    radio.transport = "active"
    hub.end_bluetooth_guard()
    hub._bluetooth_settled = True
    hub._watch_bluetooth()
    settle(app)
    check("a stream the user asked for is left alone",
          "release_audio" not in radio.calls, ", ".join(radio.calls))

    # Music that was playing is resumed once the connection settles.
    radio = Radio(brings_up_audio=True)
    radio.install()
    hub, _config = make_hub()
    sent = fake_phone_link(hub, playing=True)
    hub.connect_bluetooth()
    hub._on_media({"t": "media", "playing": False})      # the connection paused it
    settle(app)
    check("music paused by connecting is resumed",
          {"t": "media_command", "action": "play"} in sent, str(sent))

    radio = Radio(brings_up_audio=True)
    radio.install()
    hub, _config = make_hub()
    sent = fake_phone_link(hub, playing=False)
    hub.connect_bluetooth()
    settle(app)
    check("music that was not playing is not started", not sent, str(sent))

    # Autoconnect waits for the phone to say what is playing.
    radio = Radio()
    radio.install()
    hub, _config = make_hub()
    hub.companion.phone.token = "paired"
    hub._bluetooth_settled = False
    hub._bluetooth_tried = 0.0
    hub._autoconnect_bluetooth()
    settle(app, rounds=5)
    check("autoconnect waits for the media state first", not connects(radio),
          ", ".join(radio.calls))
    hub._on_media({"t": "media", "playing": False})
    settle(app, rounds=150)
    check("and connects once it has it", "connect_quietly" in radio.calls,
          ", ".join(radio.calls))


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
    # First, while the Bluetooth module is still the real one: the hub checks
    # below replace its functions with a stand-in radio.
    bluez()
    audio_playback_connection()
    defaults()
    connecting(app)
    handback(app)
    intent(app)
    automatic(app)
    quiet_failure(app)
    button(app)
    check("no exception escaped into Qt", not escaped(), "; ".join(escaped()))

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed:")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("\nall Bluetooth checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
