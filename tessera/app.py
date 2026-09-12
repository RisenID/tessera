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
from .ui.main_window import MainWindow
from .ui.theme import detect_palette, stylesheet

log = logging.getLogger(__name__)


def configure_logging(verbose: bool = False) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(logging.FileHandler(directory / "tessera.log"))
    except OSError:
        pass  # a read-only home should not stop the app starting
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def _register_with_windows() -> None:
    """Give the taskbar an application identity of our own.

    Without it Windows groups the window under the Python interpreter, which
    also means the pinned icon and the notifications are attributed to it.
    """
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            platform.APP_ID
        )
    except Exception as exc:      # not Windows, or an old shell32
        log.debug("could not set the app id: %s", exc)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    verbose = "-v" in argv or "--verbose" in argv
    configure_logging(verbose)

    app = QApplication(argv)
    app.setApplicationName("Tessera")
    app.setApplicationDisplayName("Tessera")
    app.setDesktopFileName("dev.tessera.Tessera")
    if platform.IS_WINDOWS:
        # Qt's own Windows style; Breeze is not there, and Fusion looks like
        # neither platform. Also tell the shell this is its own application so
        # the taskbar groups it and the tray icon gets a name.
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

    # Let Ctrl+C through: without a timer, Python signal handlers never run
    # while Qt owns the event loop.
    signal.signal(signal.SIGINT, lambda *_: window._quit())
    heartbeat = QTimer()
    heartbeat.start(400)
    heartbeat.timeout.connect(lambda: None)

    try:
        code = app.exec()
    finally:
        hub.stop()
        wait_for_idle(3000)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
