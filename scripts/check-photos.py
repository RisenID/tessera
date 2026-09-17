#!/usr/bin/env python3
"""Checks the photo grid's fullscreen viewer without a phone."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sandbox import isolate                                          # noqa: E402

isolate()

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent             # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

from tessera.core import hub as hub_module                           # noqa: E402
from tessera.core.config import Config                               # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


def jpeg(width: int, height: int) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0x3366AA)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG")
    return bytes(data)


def main() -> int:
    app = QApplication(sys.argv)
    from tessera.ui.pages.photos import PhotosPage
    from tessera.ui.theme import detect_palette

    hub_module.Hub._apply_codec_preference = lambda self: None
    hub_module.Hub._watch_bluetooth = lambda self: None
    hub = hub_module.Hub(Config())
    asked: list[dict] = []
    replies: dict[str, object] = {}

    def request(message, on_reply):
        asked.append(message)
        replies[f"{message.get('id')}:{message.get('thumb')}"] = on_reply

    hub.companion.request = request
    hub.companion._authenticated = True
    hub.companion._socket = object()
    page = PhotosPage(hub, detect_palette(app))
    items = [{"id": "1", "name": "one.jpg"}, {"id": "2", "name": "two.jpg"},
             {"id": "3", "name": "clip.mp4", "video": True}]
    page._render(items)
    replies["1:True"]({"data": jpeg(40, 30)})

    print("-- clicking a photo")
    tile = page._tiles["1"]
    click = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(5, 5), QPointF(5, 5),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    tile.mouseReleaseEvent(click)
    viewer = page.viewer
    check("opens the viewer", viewer is not None)
    check("fullscreen", viewer is not None and viewer.isFullScreen())
    check("shows the thumbnail straight away", not viewer._pixmap.isNull())
    check("and asks for the full picture",
          any(m.get("id") == "1" and m.get("thumb") is False for m in asked))

    replies["1:False"]({"data": jpeg(800, 600)})
    check("the full picture replaces it", viewer._pixmap.width() == 800, str(viewer._pixmap.width()))

    print("\n-- moving and closing")
    viewer.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
    check("right arrow moves to the next photo", viewer.index == 1, str(viewer.index))
    check("which is fetched too", any(m.get("id") == "2" and m.get("thumb") is False for m in asked))
    viewer.step(1)
    # Videos play in the viewer now, so the whole file is asked for like a photo.
    check("a video is fetched to play",
          any(m.get("id") == "3" and m.get("thumb") is False for m in asked))
    check("and the copy button is put away for it", viewer.copy_button.isHidden())
    viewer.step(1)
    check("it stops at the last item", viewer.index == 2)
    viewer.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    app.processEvents()
    check("Esc closes it", page.viewer is None and not viewer.isVisible())

    print("\n-- recent photos on the overview")
    from tessera.ui.pages.home import HomePage

    home = HomePage(hub, detect_palette(app))
    home._render_photos(items[:2])
    replies["1:True"]({"data": jpeg(40, 30)})
    holder = home._thumb_labels["1"]
    holder.mouseReleaseEvent(click)
    check("clicking a recent photo opens the viewer",
          home.viewer is not None and home.viewer.isFullScreen())
    check("with its thumbnail", home.viewer is not None and not home.viewer._pixmap.isNull())
    replies["1:False"]({"data": jpeg(640, 480)})
    check("then the full picture", home.viewer._pixmap.width() == 640, str(home.viewer._pixmap.width()))
    home.viewer.step(1)
    check("and moves through the recent photos", home.viewer.index == 1)
    home.viewer.close()
    app.processEvents()
    check("and closes", home.viewer is None)

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed")
        return 1
    print("\nall photo checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
