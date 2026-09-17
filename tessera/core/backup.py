"""New photos and videos copied from the phone as they appear."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from time import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..backends.filetransfer import safe_name, unique_path
from . import platform
from .config import BackupConfig

log = logging.getLogger(__name__)

#: How many of the newest items are looked at per pass.
LIST_LIMIT = 400
#: A pass runs this long after a change is announced, so a burst is one pass.
SETTLE_MS = 5_000
#: And this often regardless, in case an announcement was missed.
PERIOD_MS = 30 * 60 * 1000


class PhotoBackup(QObject):
    """Keeps a folder here up to date with what the phone has taken."""

    changed = Signal()

    def __init__(self, hub, config: BackupConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.hub = hub
        self.config = config
        self.state = "idle"             # idle | listing | copying
        self.copied = 0                 # this session
        self.pending: list[dict] = []
        self.message = ""
        self._manifest: dict[str, dict] = {}
        self._failed: set[str] = set()
        self._current: dict | None = None
        self._load_manifest()

        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self.sync)
        self._period = QTimer(self)
        self._period.setInterval(PERIOD_MS)
        self._period.timeout.connect(self.sync)
        self._period.start()

        hub.companion.connectedChanged.connect(lambda on: self._settle.start() if on else None)
        hub.companion.capabilitiesChanged.connect(lambda _c: self._settle.start())
        hub.companion.mediaLibraryChanged.connect(self._settle.start)

    # -- where and what --------------------------------------------------------

    def folder(self) -> Path:
        chosen = (self.config.folder or "").strip()
        if chosen:
            return Path(chosen).expanduser()
        return self.hub.photos_folder() / "Backup"

    def _manifest_path(self) -> Path:
        return platform.state_dir() / "backup.json"

    def _load_manifest(self) -> None:
        try:
            raw = json.loads(self._manifest_path().read_text("utf-8"))
            self._manifest = raw.get("items", {}) if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            self._manifest = {}

    def _save_manifest(self) -> None:
        path = self._manifest_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"items": self._manifest}), "utf-8")
        except OSError as exc:
            log.debug("could not save the backup manifest: %s", exc)

    @property
    def total(self) -> int:
        """Items copied over all time."""
        return sum(1 for entry in self._manifest.values() if entry.get("path"))

    def enable(self, on: bool, include_existing: bool = False) -> None:
        self.config.enabled = on
        if on:
            self.config.since = 0 if include_existing else int(time() * 1000)
        self.hub.config.save()
        self.hub.apply_features()
        if on:
            self.sync()
        self.changed.emit()

    # -- a pass ---------------------------------------------------------------

    def sync(self) -> None:
        """List the phone's newest items and copy the ones not here yet."""
        if not self.config.enabled or self.state != "idle":
            return
        companion = self.hub.companion
        if not companion.connected or not companion.supports("media"):
            return
        self.state = "listing"
        self.changed.emit()
        companion.request({"t": "media_list", "limit": LIST_LIMIT}, self._listed)

    def _listed(self, reply: dict) -> None:
        if reply.get("t") == "error":
            self.state = "idle"
            self.message = str(reply.get("message") or "The phone would not list its photos.")
            self.changed.emit()
            return
        since = int(self.config.since or 0)
        wanted = []
        for item in reply.get("items") or []:
            media_id = str(item.get("id", ""))
            if not media_id or media_id in self._manifest or media_id in self._failed:
                continue
            if int(item.get("time") or 0) < since:
                continue
            if item.get("video") and not self.config.videos:
                continue
            wanted.append(item)
        # Oldest first, so a backup interrupted halfway is a prefix of the truth.
        self.pending = sorted(wanted, key=lambda i: int(i.get("time") or 0))
        self.message = ""
        self.state = "copying" if self.pending else "idle"
        self.changed.emit()
        self._next()

    def _next(self) -> None:
        if not self.pending or not self.hub.companion.connected:
            self.state = "idle"
            self.pending = []
            self._current = None
            self.changed.emit()
            return
        self._current = item = self.pending.pop(0)
        self.hub.companion.request(
            {"t": "media_get", "id": item.get("id", ""), "thumb": False},
            lambda reply, it=item: self._arrived(it, reply),
        )

    def _arrived(self, item: dict, reply: dict) -> None:
        media_id = str(item.get("id", ""))
        data = reply.get("data")
        if reply.get("t") == "error" or not isinstance(data, (bytes, bytearray)):
            message = str(reply.get("message") or "")
            if "too large" in message:
                # Recorded so it is not asked for on every pass; it can still be
                # shared from the phone.
                self._manifest[media_id] = {"name": item.get("name", ""), "skipped": "too large"}
                self._save_manifest()
            else:
                self._failed.add(media_id)
            log.info("backup skipped %s: %s", item.get("name") or media_id, message)
            self.changed.emit()
            self._next()
            return
        try:
            folder = self.folder()
            folder.mkdir(parents=True, exist_ok=True)
            name = safe_name(str(item.get("name") or f"{media_id}.jpg"))
            target = unique_path(folder, name)
            partial = target.with_name(target.name + ".part")
            partial.write_bytes(bytes(data))
            when = int(item.get("time") or 0) / 1000
            if when > 0:
                os.utime(partial, (when, when))
            partial.replace(target)
        except OSError as exc:
            self.message = f"Could not write {item.get('name') or media_id}: {exc}"
            self._failed.add(media_id)
            log.warning("backup: %s", self.message)
            self.changed.emit()
            self._next()
            return
        self._manifest[media_id] = {"name": target.name, "path": str(target)}
        self._save_manifest()
        self.copied += 1
        self.changed.emit()
        self._next()

    def summary(self) -> str:
        """One line for the settings card."""
        if not self.config.enabled:
            return "Off."
        if self.state == "listing":
            return "Checking the phone for new photos..."
        if self.state == "copying":
            current = (self._current or {}).get("name") or "a file"
            return f"Copying {current}; {len(self.pending)} more waiting."
        if self.message:
            return self.message
        return f"Up to date. {self.total} item{'s' if self.total != 1 else ''} in {self.folder()}."
