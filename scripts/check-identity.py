#!/usr/bin/env python3
"""Checks that the sidebar shows the phone, not a part number.

A phone has a name its owner gave it -- "Ruchit's S25" -- and a model number
printed on a label somewhere. The sidebar had been showing the second one. This
covers the name, the model underneath it, and the tile that wears the phone's
own wallpaper (or, where no app may read it, the colour the phone derives from
it).

Run it from the repository root:  python3 scripts/check-identity.py
"""

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
    print("\n-- the tile wears the phone's wallpaper")
    hub, panel = make_panel(app)

    plain = panel.phone_tile.pixmap()
    check("there is something on the tile to begin with", not plain.isNull())

    # A phone that sends a picture.
    image = QImage(64, 128, QImage.Format.Format_RGB32)
    image.fill(0x2E7D32)
    target = hub.wallpaper_path
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(target), "JPG")
    hub.wallpaperChanged.emit(str(target), "#2e7d32")
    app.processEvents()

    painted = panel.phone_tile.pixmap().toImage()
    check("the picture is drawn on it", not painted.isNull())
    middle = painted.pixelColor(painted.width() // 2, painted.height() // 2)
    check("in the wallpaper's colours rather than the theme's",
          middle.green() > middle.red() and middle.green() > middle.blue(),
          middle.name())
    corner = painted.pixelColor(0, 0)
    check("and rounded like the tile it replaces", corner.alpha() == 0,
          f"corner alpha {corner.alpha()}")

    # A phone that will not give up its wallpaper -- a live one -- but does
    # say what colour it is.
    hub.wallpaperChanged.emit("", "#8f0312")
    app.processEvents()
    washed = panel.phone_tile.pixmap().toImage()
    tint = washed.pixelColor(washed.width() // 2, 4)
    check("a colour alone still colours the tile",
          tint.red() > tint.green() and tint.red() > tint.blue(), tint.name())

    # The outline on top has to stay legible on either kind of colour. It is a
    # thin line, so look for the extremes across the tile rather than at one
    # pixel: the middle of the drawn phone is its empty screen, which is the
    # wash showing through, and sampling there tested nothing.
    def extremes(image):
        lightnesses = [
            image.pixelColor(x, y).lightnessF()
            for y in range(0, image.height(), 2)
            for x in range(0, image.width(), 2)
            if image.pixelColor(x, y).alpha() > 200
        ]
        return min(lightnesses), max(lightnesses)

    darkest, _lightest = extremes(washed)
    check("a dark wallpaper gets a light outline drawn on it",
          _lightest > 0.85, f"lightest {_lightest:.2f}")

    hub.wallpaperChanged.emit("", "#f2f2f2")
    app.processEvents()
    light = panel.phone_tile.pixmap().toImage()
    pale_darkest, _ = extremes(light)
    check("and a pale one gets a dark outline instead",
          pale_darkest < 0.4, f"darkest {pale_darkest:.2f}")


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


def main() -> int:
    app = QApplication(sys.argv)
    naming(app)
    wallpaper(app)
    saving(app)

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed:")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("\nall identity checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
