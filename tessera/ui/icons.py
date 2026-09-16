"""App icons fetched from the phone, cached on disk by package name."""

from __future__ import annotations

import functools
import hashlib
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

from ..core.config import state_dir

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def cache_dir() -> Path:
    path = state_dir() / "icons"
    path.mkdir(parents=True, exist_ok=True)
    return path


class IconStore(QObject):
    """Resolves package names to pixmaps, asking the phone when needed."""

    iconReady = Signal(str, QPixmap)   # package, pixmap

    def __init__(self, client, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._cache: dict[str, QPixmap] = {}
        self._pending: set[str] = set()
        self._missing: set[str] = set()

    def _path(self, package: str) -> Path:
        # Package names are filesystem-safe in practice, but hashing keeps this
        # true for anything odd a notification might carry.
        digest = hashlib.sha1(package.encode("utf-8")).hexdigest()[:16]
        return cache_dir() / f"{digest}.png"

    def path_for(self, package: str) -> Path | None:
        """The cached icon file, for something that wants a path not a pixmap."""
        if not package:
            return None
        path = self._path(package)
        if path.exists():
            return path
        self.request(package)
        return None

    def get(self, package: str) -> QPixmap | None:
        """Return the icon if known, otherwise request it and return None."""
        if not package:
            return None

        cached = self._cache.get(package)
        if cached is not None:
            return cached

        path = self._path(package)
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self._cache[package] = pixmap
                return pixmap
            path.unlink(missing_ok=True)

        self.request(package)
        return None

    def request(self, package: str) -> None:
        """Ask the phone for an icon we do not have yet."""
        if not package or package in self._pending or package in self._missing:
            return
        if not getattr(self._client, "connected", False):
            return
        self._pending.add(package)
        self._client.request(
            {"t": "icon_get", "icon": package},
            lambda reply, pkg=package: self._on_reply(pkg, reply),
        )

    def _on_reply(self, package: str, reply: dict) -> None:
        self._pending.discard(package)
        data = reply.get("data")
        if not isinstance(data, (bytes, bytearray)):
            # The phone has no icon for this package; do not keep asking.
            self._missing.add(package)
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(bytes(data)):
            self._missing.add(package)
            return

        self._cache[package] = pixmap
        try:
            self._path(package).write_bytes(bytes(data))
        except OSError as exc:
            log.debug("could not cache icon for %s: %s", package, exc)
        self.iconReady.emit(package, pixmap)

    def forget_missing(self) -> None:
        """Retry icons that failed or were cut off, e.g. after reconnecting."""
        self._missing.clear()
        self._pending.clear()
