"""Application entry point."""

from __future__ import annotations

import logging
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QStyleFactory

from .core import platform
from .core.config import Config, state_dir
from .core.hub import Hub
from .core.proc import wait_for_idle
from .ui import appicon, glyphs
from .ui.main_window import MainWindow
from .ui.theme import detect_palette, stylesheet

log = logging.getLogger(__name__)


def configure_logging(verbose: bool = False) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        from logging.handlers import RotatingFileHandler

        handlers.append(RotatingFileHandler(
            directory / "tessera.log", maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
        ))
    except OSError:
        pass  # a read-only home should not stop the app starting
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def _register_with_windows() -> None:
    """Our own taskbar identity, named "Tessera" in notifications."""
    try:
        import ctypes
        import winreg

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(platform.APP_ID)
        picture = state_dir() / "tessera.png"
        appicon.tile(256).save(str(picture), "PNG")
        key_path = rf"Software\Classes\AppUserModelId\{platform.APP_ID}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, platform.APP_NAME)
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(picture))
    except Exception as exc:      # not Windows, or an old shell32
        log.debug("could not register the app id: %s", exc)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    # `tessera notify ...` and friends talk to the running app and exit.
    from . import cli

    if cli.is_command(argv):
        return cli.main(argv[1:])
    verbose = "-v" in argv or "--verbose" in argv
    configure_logging(verbose)

    app = QApplication(argv)
    from .core import ipc

    # One copy: a second launch brings the first one forward.
    if ipc.running():
        log.info("Tessera is already running; showing it")
        ipc.call({"cmd": "show"}, timeout_ms=3_000)
        return 0
    app.setApplicationName("Tessera")
    app.setApplicationDisplayName("Tessera")
    app.setDesktopFileName("dev.tessera.Tessera")
    app.setWindowIcon(appicon.icon())
    if platform.IS_WINDOWS:
        # Qt's own Windows style; Breeze is not there, and Fusion looks
        # like neither platform.
        for style in ("windows11", "windowsvista", "windows"):
            if style in {s.lower() for s in QStyleFactory.keys()}:
                app.setStyle(style)
                break
        _register_with_windows()
    # Closing the window hides to the tray, so Qt must not quit with it.
    app.setQuitOnLastWindowClosed(False)

    log.info(
        "Tessera starting on %s (Qt %s)",
        platform.describe(), __import__("PySide6").QtCore.qVersion(),
    )

    palette = detect_palette(app)
    glyphs.set_ink(palette.text)
    app.setStyleSheet(stylesheet(palette))

    config = Config.load()
    hub = Hub(config)
    window = MainWindow(hub, palette)

    # Minimised means "in the tray", so it needs a tray to be in. A desktop
    # without one would leave the app running with no way to reach it.
    if config.start_minimised and window.tray.isSystemTrayAvailable():
        log.info("starting minimised to the tray")
    else:
        window.show()

    hub.start()
    hub.dnd.start()

    from .core.commands import Commands

    commands = Commands(hub, window)
    server = ipc.IpcServer(app)
    commands.register(server)
    server.listen()
    app.aboutToQuit.connect(server.close)

    # Let Ctrl+C through: without a timer, Python signal handlers never run
    # while Qt owns the event loop. Only from a terminal: a packaged build has
    # no Ctrl+C to let through, and the tick would keep the CPU awake for it.
    heartbeat = QTimer()
    if not getattr(sys, "frozen", False):
        signal.signal(signal.SIGINT, lambda *_: window._quit())
        heartbeat.start(1000)
        heartbeat.timeout.connect(lambda: None)

    try:
        code = app.exec()
    finally:
        hub.stop()
        wait_for_idle(3000)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
