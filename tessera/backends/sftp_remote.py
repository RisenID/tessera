"""The phone's SFTP server, trusting only the key the paired link sent.

Adapted from Sefirah's SftpReadService and SftpReadWriteService
(https://github.com/shrimqy/Sefirah, GPL-3.0) by shrimqy.
"""

from __future__ import annotations

import base64
import posixpath
import stat
import threading
from dataclasses import dataclass

from .storage import ServerInfo

#: Never listed: Android keeps these from other apps, and SFTP lists the dots.
SKIPPED_NAMES = {".", "..", "#Recycle"}
SKIPPED_DIRS = {"Android/data", "Android/obb"}

TIMEOUT_SECONDS = 15.0


@dataclass
class Entry:
    name: str
    relative: str       # "DCIM/Camera/x.jpg"; "" is the top
    is_dir: bool
    size: int
    mtime: float


def _explain(exc: Exception) -> str:
    text = str(exc).lower()
    if "bad host key" in text or "host key" in text:
        return ("The file server did not present the key the phone said it would, "
                "so it was not trusted.")
    if "authentication" in text:
        return "The phone refused the password its own server was given."
    return f"The phone's file server could not be reached: {exc}"


class Remote:
    """One SSH connection; a lock keeps its SFTP session to one caller at a time."""

    def __init__(self, info: ServerInfo):
        self.info = info
        self._lock = threading.RLock()
        self._transport = None
        self._sftp = None

    # -- connection ------------------------------------------------------------

    def connect(self) -> None:
        import paramiko

        key_type, _sep, data = self.info.host_key.partition(" ")
        if not key_type or not data:
            raise RuntimeError("The phone did not say which key its file server uses.")
        expected = paramiko.PKey.from_type_string(key_type, base64.b64decode(data.split()[0]))
        with self._lock:
            self.close()
            try:
                transport = paramiko.Transport((self.info.host, self.info.port))
            except OSError as exc:
                raise RuntimeError(_explain(exc)) from exc
            transport.banner_timeout = transport.auth_timeout = TIMEOUT_SECONDS
            transport.get_security_options().key_types = [key_type]
            try:
                transport.connect(hostkey=expected, username=self.info.user,
                                  password=self.info.password)
                sftp = paramiko.SFTPClient.from_transport(transport)
            except (paramiko.SSHException, OSError) as exc:
                transport.close()
                raise RuntimeError(_explain(exc)) from exc
            self._transport, self._sftp = transport, sftp

    @property
    def connected(self) -> bool:
        return self._transport is not None and self._transport.is_active()

    def close(self) -> None:
        with self._lock:
            for thing in (self._sftp, self._transport):
                try:
                    if thing is not None:
                        thing.close()
                except Exception:                           # noqa: BLE001
                    pass
            self._transport = self._sftp = None

    def path(self, relative: str) -> str:
        return posixpath.join(self.info.path, relative) if relative else self.info.path

    # -- reading ---------------------------------------------------------------

    def _entry(self, relative: str, attrs) -> Entry:
        return Entry(posixpath.basename(relative), relative, stat.S_ISDIR(attrs.st_mode or 0),
                     int(attrs.st_size or 0), float(attrs.st_mtime or 0))

    def list(self, relative: str) -> list[Entry]:
        with self._lock:
            try:
                listed = self._sftp.listdir_attr(self.path(relative))
            except PermissionError:
                return []
        entries = []
        for attrs in listed:
            name = attrs.filename
            child = posixpath.join(relative, name) if relative else name
            mode = attrs.st_mode or 0
            if name in SKIPPED_NAMES or child in SKIPPED_DIRS:
                continue
            if stat.S_ISDIR(mode) or stat.S_ISREG(mode):
                entries.append(self._entry(child, attrs))
        return entries

    def stat(self, relative: str) -> Entry | None:
        with self._lock:
            try:
                return self._entry(relative, self._sftp.stat(self.path(relative)))
            except FileNotFoundError:
                return None

    def exists(self, relative: str) -> bool:
        return self.stat(relative) is not None

    def reader(self):
        """A separate SFTP session, so a download never waits on a listing."""
        import paramiko

        return paramiko.SFTPClient.from_transport(self._transport)

    # -- writing ---------------------------------------------------------------

    def _make_parents(self, relative: str) -> None:
        parent = posixpath.dirname(relative)
        if parent and not self.exists(parent):
            self._make_parents(parent)
            self._sftp.mkdir(self.path(parent))

    def upload(self, local: str, relative: str, mtime: float) -> None:
        with self._lock:
            self._make_parents(relative)
            self._sftp.put(local, self.path(relative))
            self._sftp.utime(self.path(relative), (mtime, mtime))

    def mkdir(self, relative: str) -> None:
        with self._lock:
            if not self.exists(relative):
                self._make_parents(relative)
                self._sftp.mkdir(self.path(relative))

    def rename(self, old: str, new: str) -> None:
        with self._lock:
            self._make_parents(new)
            try:
                self._sftp.posix_rename(self.path(old), self.path(new))
            except OSError:
                self._sftp.rename(self.path(old), self.path(new))

    def remove(self, relative: str) -> None:
        with self._lock:
            entry = self.stat(relative)
            if entry is None:
                return
            if entry.is_dir:
                for child in self.list(relative):
                    self.remove(child.relative)
                self._sftp.rmdir(self.path(relative))
            else:
                self._sftp.remove(self.path(relative))
