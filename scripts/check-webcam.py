#!/usr/bin/env python3
"""Checks the webcam pipeline without a phone or a camera app."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Before any tessera import: a check must never write the real configuration.
from sandbox import escaped, isolate, only_on                        # noqa: E402

ROOT = Path(isolate())

from PySide6.QtWidgets import QApplication                           # noqa: E402

from tessera.backends import webcam                                  # noqa: E402
from tessera.core.config import Config                               # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


def raised(action) -> str:
    try:
        action()
    except (RuntimeError, OSError) as exc:
        return str(exc)
    return ""


@only_on("linux")
def loopback() -> None:
    print("-- Linux: v4l2loopback")
    argv = webcam.CompanionCamera(Config().webcam).build_command("/dev/video9")
    check("ffmpeg decodes H.264 from the link", argv[argv.index("-i") + 1] == "pipe:0")
    check("and writes to the loopback device", argv[-3:] == ["-f", "v4l2", "/dev/video9"],
          str(argv[-3:]))


@only_on("windows")
def virtual_camera() -> None:
    print("-- Windows: the virtual camera")
    from tessera.backends import webcam_win as win
    from tessera.core import platform

    check("the webcam is offered", platform.supported("webcam"))

    config = Config().webcam
    config.size = "1281x721"
    camera = win.WindowsCamera(config)
    decoder, presenter = camera.commands()
    check("sizes are made even, as NV12 needs", camera.size() == (1280, 720), str(camera.size()))
    check("ffmpeg reads H.264 from the link",
          decoder[decoder.index("-i") - 2:decoder.index("-i") + 2] == ["-f", "h264", "-i", "pipe:0"])
    check("and writes raw NV12 at that size",
          "scale=1280:720" in decoder
          and decoder[-5:] == ["-pix_fmt", "nv12", "-f", "rawvideo", "pipe:1"], str(decoder[-7:]))
    check("the camera process is told the same size",
          presenter[1:] == ["1280", "720", win.CAMERA_NAME], str(presenter))

    print("-- registering, once, as administrator")
    os.environ["ProgramData"] = str(ROOT / "programdata")
    dll = ROOT / "TesseraCamera.dll"
    dll.write_bytes(b"one build")
    target = win.target_for(dll)
    check("the DLL goes under ProgramData, where Frame Server can read it",
          target.parent == ROOT / "programdata" / "Tessera", str(target))
    dll.write_bytes(b"a newer build")
    check("a new build gets a new name, so a DLL in use is never overwritten",
          win.target_for(dll) != target)
    target = win.target_for(dll)

    script = win.registration_script(dll, target)
    check("the script copies it there", "Copy-Item" in script and str(target) in script)
    check("lets Local Service read it", "*S-1-5-19:(RX)" in script)
    check("and registers it", "regsvr32.exe" in script and "/s" in script)

    real = (win.installed_source, win._elevate, win.windows_build, win.have)
    try:
        win.installed_source = lambda: ""
        check("nothing registered is not registered", not win.registered(dll))
        win.installed_source = lambda: str(target)
        check("a registration whose DLL is gone does not count", not win.registered(dll))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(dll.read_bytes())
        check("this build, registered, counts", win.registered(dll))
        dll.write_bytes(b"newer still")
        check("an older build's registration does not", not win.registered(dll))

        win._elevate = lambda _script: win.ERROR_CANCELLED
        said = raised(lambda: win.register(dll))
        check("declining the approval says so", "declined" in said, said)

        win.windows_build = lambda: 19045
        said = raised(camera.start)
        check("Windows 10 is told it needs Windows 11", "Windows 11" in said, said)
        win.windows_build = lambda: 26200
        win.have = lambda _name: False
        said = raised(camera.start)
        check("a missing ffmpeg says how to get it", "ffmpeg" in said, said)
    finally:
        win.installed_source, win._elevate, win.windows_build, win.have = real

    print("-- the camera component")
    native = win.native_dir()
    helper, source = native / win.HELPER_EXE, native / win.SOURCE_DLL
    if not (helper.is_file() and source.is_file()):
        check("it is built (native/win-camera/build.ps1)", False, str(native))
        return
    result = subprocess.run([str(helper), "--self-test", str(source)],
                            capture_output=True, text=True, timeout=60)
    check("the DLL delivers the frames it is given", result.returncode == 0,
          (result.stdout + result.stderr).strip())


def hub_route() -> None:
    print("\n-- the hub")
    from tessera.core import hub as hub_module
    from tessera.core import platform

    hub_module.Hub._apply_codec_preference = lambda self: None
    hub_module.Hub._watch_bluetooth = lambda self: None
    hub = hub_module.Hub(Config())
    expected = "WindowsCamera" if platform.IS_WINDOWS else "CompanionCamera"
    check("the right camera for this system",
          type(hub.companion_camera).__name__ == expected, type(hub.companion_camera).__name__)
    if platform.IS_WINDOWS:
        said = raised(hub.start_camera)
        check("without the companion app, Windows says what it needs", "companion" in said, said)


def main() -> int:
    QApplication(sys.argv)
    loopback()
    virtual_camera()
    hub_route()
    check("no exception escaped into Qt", not escaped(), "; ".join(escaped()))
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed:")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("\nall webcam checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
