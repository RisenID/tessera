#!/usr/bin/env python3
"""Checks file transfer by running both ends against each other."""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Before any tessera import: a check must never write the real configuration.
from sandbox import isolate                                          # noqa: E402

isolate()

from PySide6.QtCore import QObject, Signal                           # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

from tessera.backends import filetransfer                            # noqa: E402
from tessera.backends.filetransfer import (                          # noqa: E402
    CANCELLED, CHUNK, DONE, FAILED, FINISHING, HIGH_WATER, FileTransfers,
    safe_name, unique_path,
)
from tessera.core.config import Config                               # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


class Wire(QObject):
    """One end of a fake link, with a socket-shaped write buffer."""

    fileEvent = Signal(dict)
    fileChunk = Signal(dict, bytes)
    flushed = Signal()
    connectedChanged = Signal(bool)

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.peer: "Wire | None" = None
        self.connected = True
        self.pending_bytes = 0
        #: Frames held back while the buffer is "full", delivered on drain.
        self.held: list[tuple[dict, bytes]] = []
        self.auto_drain = True
        self.sent: list[dict] = []

    def send(self, message: dict) -> None:
        self.sent.append(message)
        if self.peer is not None:
            self.peer.fileEvent.emit(dict(message))

    def send_binary(self, header: dict, payload: bytes) -> None:
        self.pending_bytes += len(payload)
        if self.auto_drain:
            self.pending_bytes = 0
            if self.peer is not None:
                self.peer.fileChunk.emit(dict(header), payload)
            return
        self.held.append((dict(header), payload))

    def drain(self) -> None:
        """Deliver everything held and tell the sender there is room."""
        held, self.held = self.held, []
        self.pending_bytes = 0
        for header, payload in held:
            if self.peer is not None:
                self.peer.fileChunk.emit(header, payload)
        self.flushed.emit()

    def drop(self) -> None:
        self.connected = False
        self.connectedChanged.emit(False)


def pair(directory: Path):
    """Two engines, each able to send to the other."""
    left_wire, right_wire = Wire("desktop"), Wire("phone")
    left_wire.peer, right_wire.peer = right_wire, left_wire

    left_config, right_config = Config(), Config()
    right_config.files.save_to = str(directory)
    left_config.files.save_to = str(directory)
    return (
        FileTransfers(left_wire, left_config),
        FileTransfers(right_wire, right_config),
        left_wire,
        right_wire,
    )


def names() -> None:
    print("-- names from another machine are not trusted")
    check("a path is reduced to its last part",
          safe_name("../../etc/passwd") == "passwd", safe_name("../../etc/passwd"))
    check("a windows path too",
          safe_name(r"C:\Users\me\notes.txt") == "notes.txt",
          safe_name(r"C:\Users\me\notes.txt"))
    check("a leading dot is dropped, so nothing lands hidden",
          safe_name(".bashrc") == "bashrc", safe_name(".bashrc"))
    check("an empty name still produces one", safe_name("///") == "file")

    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        (directory / "report.pdf").write_bytes(b"x")
        second = unique_path(directory, "report.pdf")
        check("an existing file is never overwritten",
              second.name == "report (2).pdf", second.name)
        second.write_bytes(b"x")
        third = unique_path(directory, "report.pdf")
        check("and the one after that gets its own name",
              third.name == "report (3).pdf", third.name)


def round_trip(app: QApplication) -> None:
    print("\n-- a file actually travels")
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        inbox = directory / "inbox"
        inbox.mkdir()
        source = directory / "holiday.bin"
        # Larger than one chunk, and not a round number of them: the last
        # chunk being short is where an off-by-one would hide.
        payload = secrets.token_bytes(CHUNK * 3 + 1234)
        source.write_bytes(payload)

        left, right, _lw, _rw = pair(inbox)
        seen: list[int] = []
        left.changed.connect(lambda t: seen.append(t.done))

        left.send([source])
        app.processEvents()

        landed = inbox / "holiday.bin"
        check("it arrives", landed.is_file(), str(landed))
        check("byte for byte", landed.read_bytes() == payload if landed.is_file() else False)
        check("nothing is left half-written",
              not (inbox / "holiday.bin.part").exists())

        sent = left.transfers[0]
        got = right.transfers[0]
        check("the sender says it is done", sent.state == DONE, sent.state)
        check(
            "and only because the receiver confirmed it",
            any(m.get("t") == "file_saved" for m in _rw.sent),
            "; ".join(sorted({m.get("t", "") for m in _rw.sent})),
        )
        check("the receiver says so too", got.state == DONE, got.state)
        check("progress ends at the whole file",
              sent.done == len(payload), f"{sent.done} of {len(payload)}")
        check("progress only ever went forwards",
              all(b >= a for a, b in zip(seen, seen[1:])), str(seen[:4]))
        check("it went in chunks rather than one lump",
              len(seen) >= 4, f"{len(seen)} updates")

        # The same name again: the first file must survive.
        left.send([source])
        app.processEvents()
        check("a second copy lands beside the first, not on top of it",
              (inbox / "holiday (2).bin").is_file())
        check("and the first is untouched", landed.read_bytes() == payload)


def backpressure(app: QApplication) -> None:
    print("\n-- a big file does not become memory")
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        inbox = directory / "inbox"
        inbox.mkdir()
        source = directory / "big.bin"
        source.write_bytes(secrets.token_bytes(CHUNK * 20))

        left, right, left_wire, _rw = pair(inbox)
        left_wire.auto_drain = False           # a link that is not keeping up

        left.send([source])
        app.processEvents()

        held = sum(len(payload) for _h, payload in left_wire.held)
        check(
            "the sender stops at the watermark instead of reading on",
            held <= HIGH_WATER + CHUNK,
            f"{held} bytes queued, watermark {HIGH_WATER}",
        )
        check("so most of the file is still on disk, not in memory",
              held < CHUNK * 20)

        # Let it through, repeatedly, as a real socket would.
        for _ in range(30):
            left_wire.drain()
            app.processEvents()
            if not left.transfers[0].active:
                break

        check("and it finishes once the link keeps up",
              left.transfers[0].state == DONE, left.transfers[0].state)
        check("with every byte", (inbox / "big.bin").stat().st_size == CHUNK * 20)


def interruptions(app: QApplication) -> None:
    print("\n-- when it goes wrong")
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        inbox = directory / "inbox"
        inbox.mkdir()
        source = directory / "film.bin"
        source.write_bytes(secrets.token_bytes(CHUNK * 12))

        left, right, left_wire, _rw = pair(inbox)
        left_wire.auto_drain = False
        left.send([source])
        app.processEvents()
        left_wire.drain()
        app.processEvents()

        transfer = left.transfers[0]
        left.cancel(transfer.id)
        app.processEvents()
        check("cancelling stops it", transfer.state == CANCELLED, transfer.state)
        check("and the half-written file is removed",
              not (inbox / "film.bin.part").exists())
        check("leaving no file behind at all", not (inbox / "film.bin").exists())

    # A link that drops mid-transfer.
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        inbox = directory / "inbox"
        inbox.mkdir()
        source = directory / "clip.bin"
        source.write_bytes(secrets.token_bytes(CHUNK * 12))

        left, right, left_wire, right_wire = pair(inbox)
        left_wire.auto_drain = False
        left.send([source])
        app.processEvents()
        left_wire.drain()
        app.processEvents()

        left_wire.drop()
        right_wire.drop()
        app.processEvents()
        check("a dropped link fails the transfer rather than hanging",
              left.transfers[0].state == FAILED, left.transfers[0].state)
        # Kept, not cleaned up: the next offer of the same file resumes it.
        kept = inbox / "clip.bin.part"
        check("and keeps what arrived, for a second attempt", kept.exists())
        check("which the receiver remembers by name and size",
              ("clip.bin", source.stat().st_size) in right._resumable, str(right._resumable))
        got = kept.stat().st_size if kept.exists() else 0
        offered: list[dict] = []
        right.on_event({"t": "file_offer", "id": "again", "name": "clip.bin",
                        "size": source.stat().st_size, "mime": ""}, link=right_wire)
        offered = [m for m in right_wire.sent if m.get("t") == "file_accept" and m.get("id") == "again"]
        check("a second offer is accepted from where it stopped",
              bool(offered) and offered[-1].get("offset") == got,
              f"{offered[-1] if offered else 'no accept'} vs {got}")
        check("and continues into the same file", right.transfers and
              right.transfers[0].done == got, str(right.transfers[0].done if right.transfers else None))
        # Let go of the file, so the temporary folder can be removed.
        right.cancel("again")


def unconfirmed(app: QApplication) -> None:
    print("\n-- a file the phone never confirms is not a success")
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        source = directory / "quiet.bin"
        source.write_bytes(secrets.token_bytes(CHUNK))

        left = FileTransfers(_silent_peer(), Config())
        left.send([source])
        app.processEvents()
        transfer = left.transfers[0]
        check(
            "every byte written, but not yet done",
            transfer.state == FINISHING,
            transfer.state,
        )
        check("and it still counts as in flight", transfer.active)

        # What the timer would do, without waiting two minutes for it.
        left._confirm_timeout(transfer.id)
        check("a phone that never answers ends as a failure",
              transfer.state == FAILED, transfer.state)
        check("and says so", "never confirmed" in transfer.error, transfer.error)


class _silent_peer(Wire):
    """A phone that accepts a file, takes every byte, and says nothing more."""

    def __init__(self) -> None:
        super().__init__("silent")

    def send(self, message: dict) -> None:
        self.sent.append(message)
        if message.get("t") == "file_offer":
            self.fileEvent.emit({"t": "file_accept", "id": message["id"]})


def two_connections(app: QApplication) -> None:
    print("\n-- files on their own connection")
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        inbox = directory / "inbox"
        inbox.mkdir()
        source = directory / "movie.bin"
        payload = secrets.token_bytes(CHUNK * 2 + 7)
        source.write_bytes(payload)

        # The desktop holds two connections to one phone: one for files, and
        # the main link. The phone end is one engine answering on both.
        files_desk, files_phone = Wire("desktop-files"), Wire("phone-files")
        main_desk, main_phone = Wire("desktop-main"), Wire("phone-main")
        files_desk.peer, files_phone.peer = files_phone, files_desk
        main_desk.peer, main_phone.peer = main_phone, main_desk
        phone_config = Config()
        phone_config.files.save_to = str(inbox)

        desktop = FileTransfers(files_desk, Config(), fallback=main_desk)
        phone = FileTransfers(files_phone, phone_config, fallback=main_phone)

        desktop.send([source])
        app.processEvents()
        check("a file goes down the file connection",
              any(m.get("t") == "file_offer" for m in files_desk.sent)
              and not any(m.get("t") == "file_offer" for m in main_desk.sent))
        check("and arrives whole", (inbox / "movie.bin").read_bytes() == payload)

        # Without the file connection -- an older phone, or one that dropped --
        # the main link still carries files.
        files_desk.drop()
        app.processEvents()
        desktop.send([source])
        app.processEvents()
        check("the main link takes over when the file connection is gone",
              any(m.get("t") == "file_offer" for m in main_desk.sent))
        check("and that copy arrives too", (inbox / "movie (2).bin").is_file())

        # Losing one connection only ends what was on it. Larger than the
        # watermark, so it is still going when the other connection drops.
        long = directory / "long.bin"
        long.write_bytes(secrets.token_bytes(CHUNK * 12))
        main_desk.auto_drain = False
        desktop.send([long])
        app.processEvents()
        on_main = [t for t in desktop.transfers if t.active]
        check("a transfer is under way on the main link", bool(on_main))
        files_desk.connectedChanged.emit(False)
        app.processEvents()
        check("the other connection dropping leaves it alone",
              all(t.active for t in on_main), str([t.state for t in on_main]))

        # Cancelled before the folder goes: a file still arriving is held
        # open, and Windows will not delete an open file.
        for transfer in on_main:
            desktop.cancel(transfer.id)
        app.processEvents()
        check("cancelling closes and removes the file that was arriving",
              not (inbox / "long.bin.part").exists(),
              str(sorted(path.name for path in inbox.iterdir())))


def refusals(app: QApplication) -> None:
    print("\n-- things that should be refused")
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        inbox = directory / "inbox"
        inbox.mkdir()
        left, right, _lw, _rw = pair(inbox)

        complaints: list[str] = []
        left.failed.connect(complaints.append)
        left.send([directory])
        check("a folder is refused, with a reason",
              any("folder" in message for message in complaints),
              "; ".join(complaints) or "nothing said")

        complaints.clear()
        left.send([directory / "nothing-here.txt"])
        check("so is a file that is not there", bool(complaints))

        # An offer larger than the ceiling never opens a file.
        right._offered({
            "t": "file_offer", "id": "big", "name": "huge.iso",
            "size": filetransfer.MAX_SIZE + 1,
        }, _rw)
        app.processEvents()
        check("an absurd size is refused before anything is written",
              not any(inbox.iterdir()), str(list(inbox.iterdir())))


def interface(app: QApplication) -> None:
    print("\n-- the page")
    from tessera.core import hub as hub_module
    from tessera.ui.main_window import PAGES
    from tessera.ui.pages.share import SharePage
    from tessera.ui.theme import detect_palette

    entry = [row for row in PAGES if row[0] == "Share"]
    check("there is a Share page", bool(entry))
    check("governed by its own feature switch",
          entry and entry[0][4] == "file_transfer", str(entry[0][4]) if entry else "")

    hub_module.Hub._apply_codec_preference = lambda self: None
    hub_module.Hub._watch_bluetooth = lambda self: None
    config = Config()
    hub = hub_module.Hub(config)
    page = SharePage(hub, detect_palette(app))
    check("it accepts dropped files", page.drop.acceptDrops())

    errors: list[str] = []
    hub.errorOccurred.connect(errors.append)
    hub.send_files(["/tmp/whatever"])
    check("sending with no phone says why, rather than failing quietly",
          any("not connected" in message for message in errors),
          "; ".join(errors) or "nothing said")

    config.features.file_transfer = False
    errors.clear()
    hub.send_files(["/tmp/whatever"])
    check("and the feature switch is honoured",
          any("Settings" in message for message in errors),
          "; ".join(errors) or "nothing said")


def main() -> int:
    app = QApplication(sys.argv)
    names()
    round_trip(app)
    backpressure(app)
    interruptions(app)
    unconfirmed(app)
    two_connections(app)
    refusals(app)
    interface(app)

    from sandbox import escaped
    check("no exception escaped into Qt", not escaped(), "; ".join(escaped()))

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed:")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("\nall file-transfer checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
