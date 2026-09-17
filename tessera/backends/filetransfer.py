"""Files, both ways, over the connection the app already has."""

from __future__ import annotations

import logging
import mimetypes
import os
import queue
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

#: 256 KiB.
CHUNK = 256 * 1024

#: How much unsent data to allow in the socket before pausing.
HIGH_WATER = 4 * CHUNK

#: Anything larger is refused rather than filling a disk by surprise.
MAX_SIZE = 16 * 1024 * 1024 * 1024

SENDING, RECEIVING = "sending", "receiving"
WAITING, RUNNING, FINISHING, DONE, FAILED, CANCELLED = (
    "waiting", "running", "finishing", "done", "failed", "cancelled",
)

#: How long to wait for the phone to confirm a file it has been
#: sent every byte of.
CONFIRM_SECONDS = 120

#: Progress bars redraw this often at most; every 256 kB chunk was too often.
PROGRESS_SECONDS = 0.1

#: Chunks waiting for the disk before the receiver is made to wait.
WRITE_QUEUE = 16


class _Writer:
    """Writes an incoming file on its own thread, in order, off the GUI."""

    def __init__(self, handle) -> None:
        self._handle = handle
        #: Bounded: a disk slower than the network stalls the reader rather
        #: than holding the file in memory.
        self._queue: queue.Queue = queue.Queue(maxsize=WRITE_QUEUE)
        self.error = ""
        self._thread = threading.Thread(target=self._run, name="tessera-file-write", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            payload = self._queue.get()
            if payload is None:
                break
            if not self.error:
                try:
                    self._handle.write(payload)
                except OSError as exc:
                    self.error = str(exc)
        try:
            self._handle.close()
        except OSError as exc:
            self.error = self.error or str(exc)

    def write(self, payload: bytes) -> None:
        self._queue.put(payload)

    def close(self) -> None:
        """Finish every queued write and close the file. Blocks until done."""
        self._queue.put(None)
        self._thread.join()


def default_directory() -> Path:
    """Where received files go unless the settings say otherwise."""
    from ..core import platform
    from ..core.proc import run

    home = Path.home()
    if platform.IS_WINDOWS:
        # Downloads can be moved (to D:\, to OneDrive), and only the shell's
        # known-folder table knows where it went.
        known = _windows_downloads() if platform.REAL == "windows" else None
        if known is not None and known.is_dir():
            return known
        fallback = home / "Downloads"
        return fallback if fallback.is_dir() else home
    result = run(["xdg-user-dir", "DOWNLOAD"], timeout=3.0)
    if result.ok and result.stdout.strip():
        path = Path(result.stdout.strip()).expanduser()
        # xdg-user-dir answers with the home directory when it has nothing
        # configured, and dropping files loose in someone's home is not an
        # answer -- treat that as no answer at all.
        if path.is_dir() and path != home:
            return path
    fallback = home / "Downloads"
    return fallback if fallback.is_dir() else home


def _windows_downloads() -> Path | None:                    # pragma: no cover
    """FOLDERID_Downloads, through SHGetKnownFolderPath."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    # {374DE290-123F-4565-9164-39C4925E467B}
    folder = GUID(0x374DE290, 0x123F, 0x4565,
                  (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
    out = ctypes.c_wchar_p()
    try:
        shell32 = ctypes.windll.shell32                     # type: ignore[attr-defined]
        if shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(out)) != 0:
            return None
        return Path(out.value) if out.value else None
    except OSError:
        return None
    finally:
        if out:
            ctypes.windll.ole32.CoTaskMemFree(out)          # type: ignore[attr-defined]


def unique_path(directory: Path, name: str) -> Path:
    """A path in *directory* that is not already taken."""
    safe = safe_name(name)
    candidate = directory / safe

    def free(path: Path) -> bool:
        # A file still arriving under this name holds it too.
        return not path.exists() and not partial_path(path).exists()

    if free(candidate):
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if free(candidate):
            return candidate
    return directory / f"{stem} ({secrets.token_hex(4)}){suffix}"


def safe_name(name: str) -> str:
    """Reduce a name from the phone to something that cannot escape a folder."""
    name = name.replace("\\", "/").split("/")[-1].strip()
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*')
    name = name.lstrip(".") or "file"
    return name[:180]


def partial_path(destination) -> Path:
    """The name a file has while it is still arriving."""
    return Path(f"{destination}.part")


@dataclass
class Transfer:
    """One file on its way, in either direction."""

    id: str
    name: str
    size: int
    direction: str                     # SENDING | RECEIVING
    state: str = WAITING
    done: int = 0
    path: Path | None = None           # where it is, or will be
    mime: str = ""
    error: str = ""
    started: float = field(default_factory=time.monotonic)
    finished: float = 0.0
    #: The connection this transfer lives on. Replies go back the way the
    #: transfer came, and only that connection dropping can end it.
    link: object = field(default=None, repr=False, compare=False)

    @property
    def fraction(self) -> float:
        if self.size <= 0:
            return 0.0
        return min(1.0, self.done / self.size)

    @property
    def percent(self) -> int:
        return int(round(self.fraction * 100))

    @property
    def active(self) -> bool:
        return self.state in (WAITING, RUNNING, FINISHING)

    @property
    def rate(self) -> float:
        """Bytes per second so far, for what it is worth."""
        elapsed = (self.finished or time.monotonic()) - self.started
        return self.done / elapsed if elapsed > 0.05 else 0.0

    @property
    def summary(self) -> str:
        if self.state == DONE:
            return f"{human(self.size)} · {human(self.rate)}/s"
        if self.state == FAILED:
            return self.error or "Failed"
        if self.state == CANCELLED:
            return "Cancelled"
        if self.state == WAITING:
            return "Waiting for the phone"
        if self.state == FINISHING:
            # Every byte has been handed to the socket, which is not the same
            # as every byte having arrived: a watermark's worth is still in
            # flight, and the phone has still to write it down.
            return "Sent — waiting for the phone to save it"
        return f"{human(self.done)} of {human(self.size)} · {human(self.rate)}/s"


def human(size: float) -> str:
    """Sizes as people write them, which is what a progress line needs."""
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class FileTransfers(QObject):
    """The state machine for files in both directions."""

    #: Any change worth redrawing: a new transfer, progress, an ending.
    changed = Signal(object)           # Transfer
    #: A file finished arriving. Carries the Transfer, whose path now exists.
    received = Signal(object)
    #: Something went wrong that the user should be told about once.
    failed = Signal(str)

    def __init__(
        self, client, config, parent: QObject | None = None, *, fallback=None,
    ) -> None:
        super().__init__(parent)
        #: Where files go, best first: the connection that carries nothing but
        #: files, then the main link for a phone too old to open a second one.
        self._links = [link for link in (client, fallback) if link is not None]
        self._config = config
        self._transfers: dict[str, Transfer] = {}
        #: Files this end is sending, by transfer id: the open handle and how
        #: far through it we are.
        self._outgoing: dict[str, object] = {}
        self._incoming: dict[str, object] = {}
        #: Queued while the link is busy with an earlier file.
        self._queue: list[Path] = []
        self._sending = ""
        #: When each transfer's progress was last announced.
        self._progress_at: dict[str, float] = {}

        for link in self._links:
            link.fileEvent.connect(lambda message, l=link: self.on_event(message, l))
            link.fileChunk.connect(self.on_chunk)
            link.flushed.connect(self._pump)
            link.connectedChanged.connect(
                lambda connected, l=link: self._on_link(connected, l)
            )

    def _progress(self, transfer: Transfer) -> None:
        """Announce progress, at most a few times a second per file."""
        now = time.monotonic()
        if now - self._progress_at.get(transfer.id, 0.0) < PROGRESS_SECONDS:
            return
        self._progress_at[transfer.id] = now
        self.changed.emit(transfer)

    def _link(self):
        """The best connection that is up right now, or None."""
        for link in self._links:
            if getattr(link, "connected", False):
                return link
        return None

    # -- what the interface reads --------------------------------------------

    @property
    def transfers(self) -> list[Transfer]:
        """Newest first, which is the order they are worth looking at in."""
        return sorted(self._transfers.values(), key=lambda t: -t.started)

    @property
    def busy(self) -> bool:
        return bool(self._sending) or bool(self._incoming)

    def directory(self) -> Path:
        saved = (self._config.files.save_to or "").strip()
        if saved:
            path = Path(saved).expanduser()
            if path.is_dir():
                return path
        return default_directory()

    # -- sending -------------------------------------------------------------

    def send(self, paths) -> None:
        """Queue *paths* for the phone. Directories are skipped, not walked."""
        for raw in paths:
            path = Path(raw).expanduser()
            if path.is_dir():
                self.failed.emit(f"{path.name} is a folder, and folders are not sent.")
                continue
            if not path.is_file():
                self.failed.emit(f"{path} is not a file.")
                continue
            if path.stat().st_size > MAX_SIZE:
                self.failed.emit(f"{path.name} is larger than this app will send.")
                continue
            self._queue.append(path)
        self._start_next()

    def _start_next(self) -> None:
        if self._sending or not self._queue:
            return
        link = self._link()
        if link is None:
            self.failed.emit("The phone is not connected.")
            self._queue.clear()
            return

        path = self._queue.pop(0)
        try:
            size = path.stat().st_size
            handle = path.open("rb")
        except OSError as exc:
            self.failed.emit(f"Could not read {path.name}: {exc}")
            self._start_next()
            return

        transfer = Transfer(
            id=secrets.token_hex(8),
            name=path.name,
            size=size,
            direction=SENDING,
            path=path,
            mime=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            link=link,
        )
        self._transfers[transfer.id] = transfer
        self._outgoing[transfer.id] = handle
        self._sending = transfer.id
        self.changed.emit(transfer)

        # Offered rather than pushed: the phone picks the destination and can
        # refuse before a single byte is sent, which is the difference between
        # a transfer that fails at the start and one that fails at the end.
        link.send({
            "t": "file_offer",
            "id": transfer.id,
            "name": transfer.name,
            "size": transfer.size,
            "mime": transfer.mime,
        })

    def _pump(self) -> None:
        """Send as much as the socket will take without hoarding memory."""
        transfer = self._transfers.get(self._sending)
        if transfer is None or transfer.state != RUNNING:
            return
        handle = self._outgoing.get(transfer.id)
        if handle is None:
            return

        # A budget for this pass rather than a check per chunk.
        link = transfer.link
        budget = HIGH_WATER - link.pending_bytes
        while budget > 0:
            try:
                chunk = handle.read(CHUNK)
            except OSError as exc:
                self._abort_send(transfer, f"Could not read the file: {exc}")
                return

            if not chunk:
                # Sent, but only done once the phone confirms it saved.
                transfer.state = FINISHING
                handle.close()                              # type: ignore[union-attr]
                self._outgoing.pop(transfer.id, None)
                self.changed.emit(transfer)
                link.send({"t": "file_done", "id": transfer.id})
                QTimer.singleShot(
                    CONFIRM_SECONDS * 1000,
                    lambda: self._confirm_timeout(transfer.id),
                )
                return

            try:
                link.send_binary({"t": "file_chunk", "id": transfer.id}, chunk)
            except Exception as exc:                        # noqa: BLE001
                self._abort_send(transfer, str(exc))
                return
            transfer.done += len(chunk)
            budget -= len(chunk)
            self._progress(transfer)

    def _abort_send(self, transfer: Transfer, message: str) -> None:
        transfer.state = FAILED
        transfer.error = message
        transfer.finished = time.monotonic()
        self._close_send(transfer.id)
        self.changed.emit(transfer)
        self.failed.emit(f"{transfer.name}: {message}")
        self._start_next()

    def _close_send(self, transfer_id: str) -> None:
        handle = self._outgoing.pop(transfer_id, None)
        if handle is not None:
            try:
                handle.close()                              # type: ignore[union-attr]
            except OSError:
                pass
        if self._sending == transfer_id:
            self._sending = ""

    def cancel(self, transfer_id: str) -> None:
        transfer = self._transfers.get(transfer_id)
        if transfer is None or not transfer.active:
            return
        transfer.state = CANCELLED
        transfer.finished = time.monotonic()
        if transfer.link is not None:
            transfer.link.send({"t": "file_cancel", "id": transfer_id})
        if transfer.direction == SENDING:
            self._close_send(transfer_id)
            self._start_next()
        else:
            self._discard_incoming(transfer_id)
        self.changed.emit(transfer)

    # -- receiving -----------------------------------------------------------

    def on_event(self, message: dict, link=None) -> None:
        kind = message.get("t", "")
        if kind == "file_offer":
            # An offer is the one message that starts something, so it is
            # the one that has to know which connection to answer on.
            self._offered(message, link if link is not None else self._links[0])
            return
        handler = {
            "file_offer": self._offered,
            "file_accept": self._accepted,
            "file_reject": self._rejected,
            "file_done": self._completed,
            "file_saved": self._saved,
            "file_cancel": self._cancelled,
        }.get(kind)
        if handler is not None:
            handler(message)

    def _offered(self, message: dict, link) -> None:
        """The phone wants to send us something."""
        transfer_id = str(message.get("id") or "")
        if not transfer_id:
            return
        name = safe_name(str(message.get("name") or "file"))
        size = int(message.get("size") or 0)

        if size > MAX_SIZE:
            link.send({
                "t": "file_reject", "id": transfer_id,
                "message": "That file is larger than this computer will accept.",
            })
            return

        directory = self.directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            destination = unique_path(directory, name)
            # Not with_suffix: a name ending in a dot, or with no extension at
            # all, needs the same "<whatever it is>.part" either way, and this
            # is the spelling the completion step undoes.
            handle = _Writer(partial_path(destination).open("wb"))
        except OSError as exc:
            link.send({
                "t": "file_reject", "id": transfer_id,
                "message": f"This computer could not open a file to write: {exc}",
            })
            self.failed.emit(f"Could not save {name}: {exc}")
            return

        transfer = Transfer(
            id=transfer_id, name=destination.name, size=size,
            direction=RECEIVING, state=RUNNING, path=destination,
            mime=str(message.get("mime") or ""), link=link,
        )
        self._transfers[transfer_id] = transfer
        self._incoming[transfer_id] = handle
        self.changed.emit(transfer)
        link.send({"t": "file_accept", "id": transfer_id})

    def on_chunk(self, header: dict, payload: bytes) -> None:
        transfer_id = str(header.get("id") or "")
        transfer = self._transfers.get(transfer_id)
        handle = self._incoming.get(transfer_id)
        if transfer is None or handle is None:
            return
        problem = ""
        if transfer.size > 0 and transfer.done + len(payload) > transfer.size:
            problem = "more data arrived than the file's size"
        elif handle.error:                                  # type: ignore[union-attr]
            problem = f"writing failed: {handle.error}"     # type: ignore[union-attr]
        else:
            handle.write(payload)                           # type: ignore[union-attr]
        if problem:
            transfer.link.send({"t": "file_cancel", "id": transfer_id, "message": problem})
            transfer.state = FAILED
            transfer.error = problem
            self._discard_incoming(transfer_id)
            self.changed.emit(transfer)
            self.failed.emit(f"{transfer.name}: {problem}")
            return
        transfer.done += len(payload)
        self._progress(transfer)

    def _completed(self, message: dict) -> None:
        """The last chunk has arrived: make the file real."""
        transfer_id = str(message.get("id") or "")
        transfer = self._transfers.get(transfer_id)
        handle = self._incoming.pop(transfer_id, None)
        if transfer is None or handle is None:
            return
        try:
            handle.close()                                  # type: ignore[union-attr]
        except OSError:
            pass

        problem = ""
        if transfer.size > 0 and transfer.done != transfer.size:
            problem = f"only {transfer.done} of {transfer.size} bytes arrived"
        elif handle.error:                                  # type: ignore[union-attr]
            problem = f"writing failed: {handle.error}"     # type: ignore[union-attr]
        if problem:
            transfer.link.send({"t": "file_cancel", "id": transfer_id, "message": problem})
            transfer.state = FAILED
            transfer.error = problem
            transfer.finished = time.monotonic()
            # Put the handle back so the discard finds and removes the stub.
            self._incoming[transfer_id] = handle
            self._discard_incoming(transfer_id)
            self.changed.emit(transfer)
            self.failed.emit(f"{transfer.name}: {problem}")
            return

        partial = partial_path(transfer.path)
        try:
            # Drop the .part suffix only once the file is complete.
            partial.replace(transfer.path)                   # type: ignore[arg-type]
        except OSError as exc:
            transfer.state = FAILED
            transfer.error = str(exc)
            self.changed.emit(transfer)
            self.failed.emit(f"{transfer.name}: {exc}")
            return

        transfer.state = DONE
        transfer.finished = time.monotonic()
        if transfer.size <= 0:
            transfer.size = transfer.done
        self.changed.emit(transfer)
        self.received.emit(transfer)
        transfer.link.send({
            "t": "file_saved", "id": transfer_id, "path": str(transfer.path),
        })

    def _accepted(self, message: dict) -> None:
        transfer = self._transfers.get(str(message.get("id") or ""))
        if transfer is None or transfer.direction != SENDING:
            return
        transfer.state = RUNNING
        transfer.started = time.monotonic()
        self.changed.emit(transfer)
        self._pump()

    def _rejected(self, message: dict) -> None:
        transfer = self._transfers.get(str(message.get("id") or ""))
        if transfer is None:
            return
        self._abort_send(
            transfer, str(message.get("message") or "The phone refused the file.")
        )

    def _saved(self, message: dict) -> None:
        """The phone says where it put a file we sent: now it is done."""
        transfer = self._transfers.get(str(message.get("id") or ""))
        if transfer is None:
            return
        where = str(message.get("path") or "")
        if where:
            log.info("the phone saved %s as %s", transfer.name, where)
        if transfer.state in (RUNNING, FINISHING):
            transfer.state = DONE
            transfer.done = transfer.size
            transfer.finished = time.monotonic()
            self.changed.emit(transfer)
        self._close_send(transfer.id)
        self._start_next()

    def _confirm_timeout(self, transfer_id: str) -> None:
        """The phone took every byte and never said what became of them."""
        transfer = self._transfers.get(transfer_id)
        if transfer is None or transfer.state != FINISHING:
            return
        self._abort_send(
            transfer,
            "The phone took the whole file and never confirmed it. It may not "
            "have been saved.",
        )

    def _cancelled(self, message: dict) -> None:
        transfer = self._transfers.get(str(message.get("id") or ""))
        if transfer is None or not transfer.active:
            # Nothing to stop.
            if transfer is not None and transfer.state == DONE:
                transfer.state = FAILED
                transfer.error = str(message.get("message") or "The phone gave up.")
                self.changed.emit(transfer)
                self.failed.emit(f"{transfer.name}: {transfer.error}")
            return
        transfer.state = CANCELLED
        transfer.error = str(message.get("message") or "")
        transfer.finished = time.monotonic()
        if transfer.direction == SENDING:
            self._close_send(transfer.id)
            self._start_next()
        else:
            self._discard_incoming(transfer.id)
        self.changed.emit(transfer)

    def _discard_incoming(self, transfer_id: str) -> None:
        """Throw away a half-written file rather than leaving a stub behind."""
        handle = self._incoming.pop(transfer_id, None)
        if handle is not None:
            try:
                handle.close()                              # type: ignore[union-attr]
            except OSError:
                pass
        transfer = self._transfers.get(transfer_id)
        if transfer is not None and transfer.path is not None:
            partial = partial_path(transfer.path)
            try:
                if partial.exists():
                    partial.unlink()
            except OSError:
                pass

    # -- the link ------------------------------------------------------------

    def _on_link(self, connected: bool, link=None) -> None:
        """A dropped connection ends the transfers that were using it."""
        if connected:
            return
        for transfer in list(self._transfers.values()):
            if not transfer.active:
                continue
            if link is not None and transfer.link is not link:
                continue
            transfer.state = FAILED
            transfer.error = "The phone disconnected."
            transfer.finished = time.monotonic()
            if transfer.direction == SENDING:
                self._close_send(transfer.id)
            else:
                self._discard_incoming(transfer.id)
            self.changed.emit(transfer)
        if self._sending and self._sending not in {
            t.id for t in self._transfers.values() if t.active
        }:
            self._sending = ""
        if self._link() is None:
            self._queue.clear()
        else:
            self._start_next()

    def forget_finished(self) -> None:
        for transfer_id, transfer in list(self._transfers.items()):
            if not transfer.active:
                del self._transfers[transfer_id]


def reveal(path: Path) -> None:
    """Show a file in the desktop's file manager, or open its folder."""
    from ..core import platform
    from ..core.proc import have, run

    if platform.IS_WINDOWS:
        # Explorer's own "select this file", one argument, no quotes of ours:
        # Explorer parses its command line itself.
        if platform.REAL == "windows":                      # pragma: no cover
            import subprocess
            subprocess.Popen(["explorer", f"/select,{path}"],
                             creationflags=platform.no_window_flags())
        return
    if have("dbus-send"):
        from urllib.parse import quote

        # Encoded, and commas too: dbus-send splits array items on them.
        uri = "file://" + quote(str(path)).replace(",", "%2C")
        result = run([
            "dbus-send", "--session", "--print-reply",
            "--dest=org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1.ShowItems",
            f"array:string:{uri}", "string:",
        ], timeout=5.0)
        if result.ok:
            return
    open_path(path.parent)


def open_path(path: Path) -> None:
    """Hand a file to whatever the desktop opens it with."""
    from ..core.proc import have, run

    if os.name == "nt":                                     # pragma: no cover
        os.startfile(str(path))                             # type: ignore[attr-defined]
        return
    if have("xdg-open"):
        run(["xdg-open", str(path)], timeout=5.0)
