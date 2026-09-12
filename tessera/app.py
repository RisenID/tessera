"""Application entry point."""

from __future__ import annotations

import logging
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

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


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    verbose = "-v" in argv or "--verbose" in argv
    configure_logging(verbose)

    app = QApplication(argv)
    app.setApplicationName("Tessera")
    app.setApplicationDisplayName("Tessera")
    app.setDesktopFileName("dev.tessera.Tessera")
    # Closing the window hides to the tray, so Qt must not quit with it.
    app.setQuitOnLastWindowClosed(False)

    log.info("Tessera starting (Qt %s)", __import__("PySide6").QtCore.qVersion())

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
