#!/usr/bin/env python3
"""Checks that the sidebar shows the phone, not a part number."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Before any tessera import: a check must never write the real configuration.
from sandbox import isolate                                          # noqa: E402

isolate()

from PySide6.QtGui import QImage                                     # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

from tessera.backends.companion import PairedPhone                   # noqa: E402
from tessera.core import hub as hub_module                           # noqa: E402
from tessera.core.config import Config                               # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


def make_panel(app: QApplication):
    from tessera.ui.panel import DevicePanel
    from tessera.ui.theme import detect_palette

    hub_module.Hub._apply_codec_preference = lambda self: None
    hub_module.Hub._watch_bluetooth = lambda self: None
    hub = hub_module.Hub(Config())
    hub.companion.phone.name = "Ruchit's S25"
    hub.companion.phone.model = "SM-S931B"
    panel = DevicePanel(hub, detect_palette(app))
    return hub, panel


def naming(app: QApplication) -> None:
    print("-- the name, and the model under it")
    check("a phone carries both", hasattr(PairedPhone(), "model"))
    check("and remembers them between runs",
          hasattr(Config().companion, "model"))

    hub, panel = make_panel(app)
    panel.refresh_header()
    check("the sidebar shows the name", panel.brand.text() == "Ruchit's S25",
          panel.brand.text())
    check("with the model underneath, not instead",
          panel.device_label.text() == "SM-S931B", panel.device_label.text())

    # A phone that only ever sent a model still shows something sensible.
    hub.companion.phone.name = "SM-S931B"
    hub.companion.phone.model = "SM-S931B"
    panel.refresh_header()
    check("a phone with no name of its own is not written twice",
          panel.device_label.text() == "", repr(panel.device_label.text()))


def wallpaper(app: QApplication) -> None:
    print("\n-- the tile is a phone showing a wallpaper")
    hub, panel = make_panel(app)

    def tile() -> QImage:
        return panel.phone_tile.pixmap().toImage()

    check("it is phone-shaped, not square",
          panel.phone_tile.height() > panel.phone_tile.width() * 1.6,
          f"{panel.phone_tile.width()}x{panel.phone_tile.height()}")

    # With no wallpaper at all there must still be a wallpaper: the default.
    plain = tile()
    check("a phone that shares nothing still gets a picture", not plain.isNull())
    middle = plain.pixelColor(plain.width() // 2, plain.height() // 2)
    check("the default is a wallpaper rather than a flat fill",
          middle.alpha() == 255 and middle.lightnessF() > 0.05,
          middle.name())
    top = plain.pixelColor(plain.width() // 2, plain.height() // 6)
    bottom = plain.pixelColor(plain.width() // 2, plain.height() * 5 // 6)
    check("and it has the shading of one, not one colour",
          abs(top.lightnessF() - bottom.lightnessF()) > 0.04,
          f"{top.name()} to {bottom.name()}")

    corner = plain.pixelColor(0, 0)
    check("the corners are rounded like a phone", corner.alpha() == 0,
          f"corner alpha {corner.alpha()}")
    edge = plain.pixelColor(plain.width() // 2, 1)
    check("with a bezel around the screen", edge.lightnessF() < 0.2, edge.name())

    # A phone that does share one shows it.
    image = QImage(64, 128, QImage.Format.Format_RGB32)
    image.fill(0x2E7D32)
    target = hub.wallpaper_path
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(target), "JPG")
    hub.wallpaperChanged.emit(str(target), "")
    app.processEvents()

    painted = tile()
    shown = painted.pixelColor(painted.width() // 2, painted.height() // 2)
    check("the phone's own wallpaper replaces the default",
          shown.green() > shown.red() and shown.green() > shown.blue(),
          shown.name())

    # A colour with no picture is not painted with: that was the version
    # before this one, and it looked like a coloured brick.
    hub.wallpaperChanged.emit("", "#8f0312")
    app.processEvents()
    reverted = tile()
    spot = reverted.pixelColor(reverted.width() // 2, reverted.height() // 2)
    check("a colour on its own goes back to the default, not a red block",
          spot.red() < 120, spot.name())


def saving(app: QApplication) -> None:
    print("\n-- how it arrives")
    hub, _panel = make_panel(app)
    seen: list[tuple[str, str]] = []
    hub.wallpaperChanged.connect(lambda path, colour: seen.append((path, colour)))

    # The phone sends a picture as a binary reply.
    tiny = QImage(8, 8, QImage.Format.Format_RGB32)
    tiny.fill(0x123456)
    scratch = Path(os.environ["XDG_STATE_HOME"]) / "tiny.jpg"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    tiny.save(str(scratch), "JPG")
    hub._on_wallpaper({"t": "wallpaper", "colour": "#123456",
                       "data": scratch.read_bytes()})
    check("a picture is saved where the panel can find it",
          hub.wallpaper_path.is_file(), str(hub.wallpaper_path))
    check("nothing is left half-written",
          not hub.wallpaper_path.with_suffix(".part").exists())
    check("and the panel is told", bool(seen), str(seen))

    # A phone with only a colour to offer.
    seen.clear()
    hub._on_wallpaper({"t": "wallpaper", "colour": "#8f0312"})
    check("a colour on its own is passed on too",
          seen and seen[-1][1] == "#8f0312", str(seen))

    # A phone with neither says nothing rather than blanking the tile.
    seen.clear()
    hub._on_wallpaper({"t": "wallpaper"})
    check("and a phone with neither changes nothing", not seen, str(seen))


def link_state(app: QApplication) -> None:
    print("\n-- the header follows the link")
    hub, panel = make_panel(app)
    client = hub.companion

    class Socket:
        def blockSignals(self, _on): pass
        def abort(self): pass
        def deleteLater(self): pass

    client._want_connection = False
    client._socket, client._authenticated = Socket(), True
    client.connectedChanged.emit(True)
    app.processEvents()
    check("connected shows as connected", panel.link_pill.text() == "Connected",
          panel.link_pill.text())

    # The phone closing the socket, e.g. the app being reinstalled.
    client._on_disconnected()
    app.processEvents()
    check("the phone hanging up takes the header offline",
          panel.link_pill.text() != "Connected", panel.link_pill.text())

    print("\n-- an empty Bluetooth player")
    from tessera.backends import mpris

    player = mpris.MprisPlayer()
    props = {"Metadata": {"xesam:title": "Not Provided", "xesam:artist": [""]},
             "PlaybackStatus": "Stopped"}
    player._property = lambda _service, name: props[name]
    track = player.track("org.mpris.MediaPlayer2.phone")
    check("a placeholder title reads as nothing playing",
          track.summary == "Nothing playing", track.summary)


def main() -> int:
    app = QApplication(sys.argv)
    naming(app)
    wallpaper(app)
    saving(app)
    link_state(app)

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed:")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("\nall identity checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
