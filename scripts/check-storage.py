#!/usr/bin/env python3
"""Checks mounting the phone's storage, without a phone or a mount."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from xml.dom import minidom

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Before any tessera import: a check must never write the real configuration,
# nor -- here -- the real file manager bookmarks.
from sandbox import escaped, isolate, only_on                        # noqa: E402

ROOT = Path(isolate())

from PySide6.QtWidgets import QApplication                           # noqa: E402

from tessera.backends import storage                                 # noqa: E402
from tessera.backends.storage import Mount, ServerInfo               # noqa: E402
from tessera.core import hub as hub_module                           # noqa: E402
from tessera.core.config import Config                               # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


REPLY = {
    "t": "reply", "port": 8766, "user": "tessera",
    "password": "5f0c2b7e9d1a4c3b8e6f7a2d1c0b9e8f",
    "path": "/storage/emulated/0",
    "hostKey": "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAA=",
}


def server_info() -> None:
    print("-- what the phone says about its server")
    info = ServerInfo.from_reply("192.168.100.48", REPLY)
    check("a complete answer is understood", info is not None)
    check("the key type is read from the key", info.key_type == "ecdsa-sha2-nistp256",
          info.key_type)
    check("an answer without a password is refused",
          ServerInfo.from_reply("192.168.100.48", {**REPLY, "password": ""}) is None)
    check("so is one without a port",
          ServerInfo.from_reply("192.168.100.48", {**REPLY, "port": "x"}) is None)
    check("an IPv6 address is bracketed",
          ServerInfo.from_reply("fe80::1", REPLY).bracketed_host == "[fe80::1]")


def trust() -> None:
    print("\n-- the mount trusts the phone's key and nothing else")
    info = ServerInfo.from_reply("192.168.100.48", REPLY)
    line = storage.known_hosts_line(info)
    check("known_hosts names the exact address and port",
          line.startswith("[192.168.100.48]:8766 ecdsa-sha2-nistp256 "), line)

    try:
        storage.mount(ServerInfo.from_reply("192.168.100.48", {**REPLY, "hostKey": ""}), "Phone")
        refused = False
    except RuntimeError as exc:
        refused = "key" in str(exc)
    check("a phone that sends no key is not mounted at all", refused)

    check("a phone's name cannot climb out of the mount folder",
          "/" not in storage.folder_name("../../etc/Ruchit's S25"),
          storage.folder_name("../../etc/Ruchit's S25"))
    check("host key trouble is said plainly",
          "key" in storage._explain("Host key verification failed."))


# -- Linux: sshfs and the file manager's sidebar ------------------------------


@only_on("linux")
def sshfs_on_linux() -> None:
    print("\n-- Linux: sshfs")
    info = ServerInfo.from_reply("192.168.100.48", REPLY)
    command = storage.sshfs_command(info, Path("/tmp/mnt"), Path("/tmp/kh"))
    joined = " ".join(command)
    check("an unknown or different key is refused, not accepted",
          "StrictHostKeyChecking=yes" in joined and "StrictHostKeyChecking=no" not in joined)
    check("only the key file written from the paired link is consulted",
          "UserKnownHostsFile=/tmp/kh" in joined and "GlobalKnownHostsFile=/dev/null" in joined)
    check("and only for the algorithm that key uses",
          "HostKeyAlgorithms=ecdsa-sha2-nistp256" in joined)
    check("the password is never on the command line",
          REPLY["password"] not in joined)
    check("it is read from stdin instead", "password_stdin" in joined)
    check("the user's own SSH keys are not offered to the phone",
          "PubkeyAuthentication=no" in joined)
    check("it mounts the phone's shared storage",
          "tessera@192.168.100.48:/storage/emulated/0" in command, command[1])


KDE_PLACES = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xbel>
<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks" xmlns:kdepriv="http://www.kde.org/kdepriv" xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info">
 <bookmark href="file:///home/someone">
  <title>Home</title>
  <info>
   <metadata owner="http://www.kde.org">
    <ID>1700000000/0</ID>
    <isSystemItem>true</isSystemItem>
   </metadata>
  </info>
 </bookmark>
</xbel>
"""


@only_on("linux")
def sidebar() -> None:
    print("\n-- the file manager's sidebar comes back exactly as it was")
    places = ROOT / "data" / "user-places.xbel"
    places.parent.mkdir(parents=True, exist_ok=True)
    places.write_text(KDE_PLACES, encoding="utf-8")
    gtk = ROOT / "config" / "gtk-3.0" / "bookmarks"
    gtk.parent.mkdir(parents=True, exist_ok=True)
    gtk.write_text("file:///home/someone/Music Music\n", encoding="utf-8")

    mounted = Mount(storage.SSHFS, str(ROOT / "state" / "mounts" / "Ruchit's S25"), "Ruchit's S25")
    storage.add_place(mounted)
    storage.add_place(mounted)
    text = places.read_text(encoding="utf-8")
    check("the phone appears in KDE's places", storage.PLACE_ID in text)
    check("once, however many times it is mounted", text.count(storage.PLACE_ID) == 1,
          str(text.count(storage.PLACE_ID)))
    check("the user's own places are untouched", "<title>Home</title>" in text)
    try:
        minidom.parseString(text)
        valid = True
    except Exception:                                   # noqa: BLE001
        valid = False
    check("and the file is still valid XML", valid)
    check("a name with an apostrophe is written safely",
          "Ruchit&#x27;s" in text or "Ruchit's S25" in text)

    lines = gtk.read_text(encoding="utf-8").splitlines()
    check("the phone appears in GTK's bookmarks", any("Ruchit's S25" in l for l in lines))
    check("once", sum("Ruchit's S25" in l for l in lines) == 1, str(lines))
    check("beside the user's own", lines[0] == "file:///home/someone/Music Music")

    storage.remove_place(mounted)
    check("removing it restores KDE's places exactly",
          places.read_text(encoding="utf-8") == KDE_PLACES)
    check("and GTK's bookmarks exactly",
          gtk.read_text(encoding="utf-8") == "file:///home/someone/Music Music\n")

    missing = ROOT / "data2"
    os.environ["XDG_DATA_HOME"] = str(missing)
    storage.add_place(mounted)
    check("a desktop with no KDE places file does not get one made",
          not (missing / "user-places.xbel").exists())
    storage.remove_place(mounted)
    os.environ["XDG_DATA_HOME"] = str(ROOT / "data")


@only_on("linux")
def unmounting() -> None:
    print("\n-- unmounting can never delete the phone's files")
    folder = ROOT / "state" / "mounts" / "Looks mounted"
    folder.mkdir(parents=True, exist_ok=True)
    precious = folder / "DCIM.jpg"
    precious.write_bytes(b"a photo")
    storage.unmount(Mount(storage.SSHFS, str(folder), "Looks mounted"))
    check("a folder that still has files in it is left alone", precious.exists())

    empty = ROOT / "state" / "mounts" / "Empty"
    empty.mkdir(parents=True, exist_ok=True)
    storage.unmount(Mount(storage.SSHFS, str(empty), "Empty"))
    check("an empty mount point is tidied away", not empty.exists())
    check("nothing here counts as mounted", not storage.is_mounted(ROOT))


# -- Windows: a drive, through WinFsp and SSHFS-Win ---------------------------


class Stdin:
    def __init__(self):
        self.written = ""
        self.closed = False

    def write(self, text: str) -> None:
        self.written += text

    def close(self) -> None:
        self.closed = True


class Sshfs:
    """sshfs.exe, as far as the mount code can tell."""

    def __init__(self, command, env, log_path, exit_code=None):
        self.command, self.env, self.log_path = command, env, log_path
        self.exit_code = exit_code
        self.stdin = Stdin()
        self.stopped = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.stopped = True

    def kill(self):
        self.stopped = True

    def wait(self, timeout=None):
        return self.exit_code or 0


@only_on("windows")
def drive_on_windows() -> None:
    print("\n-- Windows: a drive letter, through WinFsp and SSHFS-Win")
    from tessera.backends import storage_win as win
    from tessera.core import platform

    check("phone storage is offered", platform.supported("storage"))
    info = ServerInfo.from_reply("192.168.100.48", REPLY)

    known = Path(r"C:\Users\me\AppData\Local\Tessera\known_hosts")
    check("paths reach Cygwin's sshfs in its own spelling",
          win.cygwin_path(known) == "/cygdrive/c/Users/me/AppData/Local/Tessera/known_hosts",
          win.cygwin_path(known))

    sshfs = r"C:\Program Files\SSHFS-Win\bin\sshfs.exe"
    command = win.sshfs_command(info, "Y", known, "Ruchit's S25, work", sshfs)
    joined = " ".join(command)
    check("only the key file written from the paired link is consulted",
          "UserKnownHostsFile=/cygdrive/c/Users/me/AppData/Local/Tessera/known_hosts" in command
          and "GlobalKnownHostsFile=/dev/null" in command)
    check("an unknown or different key is refused, not accepted",
          "StrictHostKeyChecking=yes" in command and "StrictHostKeyChecking=no" not in joined)
    check("and only for the algorithm that key uses",
          "HostKeyAlgorithms=ecdsa-sha2-nistp256" in command)
    check("the password is never on the command line", REPLY["password"] not in joined)
    check("it is read from stdin instead", "password_stdin" in command)
    check("the user's own SSH keys are not offered", "PubkeyAuthentication=no" in command)
    check("it mounts the phone's shared storage",
          "tessera@192.168.100.48:/storage/emulated/0" in command)
    check("on a drive letter", command[3] == "Y:", command[3])
    check("in the foreground, so stopping sshfs unmounts it", "-f" in command)
    check("the files belong to whoever is signed in",
          "uid=-1" in command and "gid=-1" in command)
    check("the drive is labelled with the phone, less what would break the options",
          "volname=Ruchit's S25 work" in command,
          str([option for option in command if option.startswith("volname")]))

    check("letters come from the end of the alphabet", win.free_letter({"C", "D"}) == "Z")
    check("passing over ones in use", win.free_letter({"C", "Z", "Y"}) == "X")
    check("and there may be none", win.free_letter(set(win.LETTERS)) == "")

    real = (win.sshfs_path, win.winfsp_installed, win._spawn, win._exists, win.used_letters,
            win.dress_drive, win.undress_drive)
    dressed: list[tuple[str, ...]] = []
    try:
        win.sshfs_path = lambda: ""
        win.winfsp_installed = lambda: False
        check("without WinFsp and SSHFS-Win there is nothing to mount with",
              storage.backend() == "")
        advice = storage.missing_advice()
        check("and the advice names both", "WinFsp" in advice and "SSHFS-Win" in advice, advice)
        said = ""
        try:
            win.mount(info, "Ruchit's S25")
        except RuntimeError as exc:
            said = str(exc)
        check("mounting anyway says what is missing", "WinFsp" in said, said)

        win.sshfs_path = lambda: sshfs
        win.winfsp_installed = lambda: True
        win.used_letters = lambda: {"C", "Z"}
        check("with both, SSHFS-Win mounts it", storage.backend() == storage.SSHFS_WIN,
              storage.backend())

        spawned: list[Sshfs] = []

        def spawn(command, env, log_path):
            spawned.append(Sshfs(command, env, log_path))
            return spawned[-1]

        win._spawn = spawn
        win._exists = lambda root: root == "Y:\\"
        # Recorded, not written: a check must not touch the real registry.
        win.dress_drive = lambda letter, name: dressed.append(("dress", letter, name))
        win.undress_drive = lambda letter: dressed.append(("undress", letter))
        bookmarks = ROOT / "config" / "gtk-3.0" / "bookmarks"
        before = bookmarks.read_text(encoding="utf-8") if bookmarks.exists() else None

        mounted = storage.mount(info, "Ruchit's S25")
        process = spawned[-1]
        check("the phone lands on the first free letter", mounted.location == "Y:\\",
              mounted.location)
        check("as a drive that can be opened",
              mounted.local_path == Path("Y:\\") and mounted.uri == "file:///Y:/", mounted.uri)
        check("the password goes in on stdin",
              process.stdin.written == REPLY["password"] + "\n", repr(process.stdin.written))
        check("which is closed after it", process.stdin.closed)
        pinned = platform.state_dir() / "known_hosts"
        check("the pinned key is written for sshfs to read",
              pinned.read_text(encoding="utf-8").strip() == storage.known_hosts_line(info))
        check("ssh.exe is found beside sshfs.exe",
              process.env["PATH"].startswith(str(Path(sshfs).parent)))
        after = bookmarks.read_text(encoding="utf-8") if bookmarks.exists() else None
        check("no Linux file manager bookmark is written", after == before)
        check("the drive shows as the phone, with its icon and name",
              dressed == [("dress", "Y", "Ruchit's S25")], str(dressed))
        check("with a phone from Windows' own icons", "imageres.dll" in win.PHONE_ICON)

        storage.unmount(mounted)
        check("unmounting stops sshfs, which takes the drive away", process.stopped)
        check("and takes the icon and name back", dressed[-1] == ("undress", "Y"), str(dressed))
        storage.unmount(mounted)
        check("and doing it twice is harmless", len(spawned) == 1)

        def refuses(command, env, log_path):
            Path(log_path).write_text(
                "read: Connection reset by peer\nHost key verification failed.\n",
                encoding="utf-8",
            )
            spawned.append(Sshfs(command, env, log_path, exit_code=1))
            return spawned[-1]

        win._spawn = refuses
        win._exists = lambda root: False
        said = ""
        try:
            storage.mount(info, "Ruchit's S25")
        except RuntimeError as exc:
            said = str(exc)
        check("a server with the wrong key is not trusted", "key" in said, said)
        check("and nothing is left running", spawned[-1].stopped)
        check("nor a phone icon on a drive that never came up",
              dressed[-1][0] == "undress", str(dressed[-1:]))
    finally:
        (win.sshfs_path, win.winfsp_installed, win._spawn, win._exists, win.used_letters,
         win.dress_drive, win.undress_drive) = real


# -- the hub and the page, on either system -----------------------------------


def make_hub():
    hub_module.Hub._apply_codec_preference = lambda self: None
    hub_module.Hub._watch_bluetooth = lambda self: None

    def now(fn, *args, on_done=None, on_error=None):
        try:
            result = fn(*args)
        except Exception as exc:                        # noqa: BLE001
            if on_error:
                on_error(str(exc))
            return
        if on_done:
            on_done(result)

    hub_module.submit = now
    hub = hub_module.Hub(Config())
    hub.companion.phone.name = "Ruchit's S25"
    sent: list[dict] = []
    asked: list[dict] = []
    replies: dict[str, dict] = {}
    hub.companion.send = sent.append                    # type: ignore[method-assign]

    def request(message, on_reply):
        asked.append(message)
        if message["t"] in replies:
            on_reply(replies[message["t"]])

    hub.companion.request = request                     # type: ignore[method-assign]
    return hub, sent, asked, replies


def connect(hub, caps: list[str]) -> None:
    hub.companion._authenticated = True
    hub.companion._socket = object()
    hub.companion._pending_host = ("192.168.100.48", 8765)
    hub.companion._capabilities = caps
    hub.companion.capabilitiesChanged.emit(caps)


def orchestration(app: QApplication) -> None:
    print("\n-- the hub mounts when it should, and only then")
    mounts: list[ServerInfo] = []
    unmounts: list[Mount] = []
    real_mount, real_unmount, real_backend = storage.mount, storage.unmount, storage.backend
    storage.backend = lambda: storage.SSHFS
    storage.mount = lambda info, name, sidebar=True: (
        mounts.append(info) or Mount(storage.SSHFS, "/tmp/phone", name))
    storage.unmount = lambda mounted: unmounts.append(mounted)
    try:
        hub, sent, asked, replies = make_hub()
        replies["storage_start"] = REPLY
        connect(hub, ["storage"])
        check("a phone that has not allowed access is not asked to mount",
              not any(m["t"] == "storage_start" for m in asked))

        hub, sent, asked, replies = make_hub()
        replies["storage_start"] = REPLY
        changes: list[str] = []
        hub.storageChanged.connect(lambda: changes.append(hub.storage_state))
        connect(hub, ["storage", "storage_allowed", "file_channel"])
        check("a phone that has allowed it is mounted on connect",
              hub.storage_state == "mounted" and hub.storage_mount is not None,
              hub.storage_state)
        check("with the address the phone answered on",
              mounts and mounts[-1].host == "192.168.100.48")
        check("and the interface was told at each step",
              changes[:1] == ["starting"] and changes[-1] == "mounted", str(changes))

        hub.companion._authenticated = False
        hub.companion.connectedChanged.emit(False)
        check("losing the phone unmounts", unmounts and hub.storage_mount is None)
        check("without telling a phone that is not there",
              not any(m.get("t") == "storage_stop" for m in sent))

        hub, sent, asked, replies = make_hub()
        hub.config.storage.auto_mount = False
        replies["storage_start"] = REPLY
        connect(hub, ["storage", "storage_allowed"])
        check("switched to manual, connecting mounts nothing",
              not any(m["t"] == "storage_start" for m in asked))
        hub.mount_storage()
        check("but the button still does", hub.storage_state == "mounted")
        hub.unmount_storage()
        check("and unmounting tells the phone to stop its server",
              any(m.get("t") == "storage_stop" for m in sent))

        hub, sent, asked, replies = make_hub()
        replies["storage_start"] = {"t": "error", "message": "All files access is off."}
        connect(hub, ["storage", "storage_allowed"])
        check("a refusal is shown, not swallowed",
              hub.storage_state == "error" and "All files" in hub.storage_message,
              hub.storage_message)

        hub, sent, asked, replies = make_hub()
        replies["storage_grant"] = {"t": "reply", "granted": True}
        replies["storage_start"] = REPLY
        connect(hub, ["storage", "storage_grant"])
        hub.grant_storage()
        check("allowing it from the desktop goes on to mount",
              hub.storage_state == "mounted", hub.storage_state)

        storage.backend = lambda: ""
        hub, sent, asked, replies = make_hub()
        connect(hub, ["storage", "storage_allowed"])
        hub.mount_storage()
        check("a computer with nothing to mount with says what to install",
              hub.storage_state == "error" and "Mounting needs" in hub.storage_message,
              hub.storage_message)
    finally:
        storage.mount, storage.unmount, storage.backend = real_mount, real_unmount, real_backend


def links_and_wallpaper(app: QApplication) -> None:
    print("\n-- the file connection, and the wallpaper, on connect")
    hub, _sent, asked, _replies = make_hub()
    opened: list[tuple] = []
    hub.files_link.connect_to_phone = lambda *a: opened.append(a)   # type: ignore[method-assign]
    connect(hub, ["file_channel", "wallpaper"])
    check("a phone that offers it gets a second connection for files",
          opened == [("192.168.100.48", 8765)], str(opened))
    check("on the address the main link is using", bool(opened))

    closed: list[bool] = []
    hub.files_link.disconnect_from_phone = lambda: closed.append(True)  # type: ignore[method-assign]
    hub.companion._authenticated = False
    hub.companion.connectedChanged.emit(False)
    check("and it goes when the main link goes", closed == [True])

    connect(hub, ["file_channel", "wallpaper"])
    connect(hub, ["file_channel", "wallpaper"])
    wallpaper_asks = sum(m["t"] == "wallpaper_get" for m in asked)
    check("the wallpaper is asked for once, not on every reconnect",
          wallpaper_asks == 1, f"asked {wallpaper_asks} times over three connections")

    hub2, _s, asked2, _r = make_hub()
    connect(hub2, ["wallpaper"])
    hub2._wallpaper_asked -= hub2.WALLPAPER_RECHECK_SECONDS + 1
    connect(hub2, ["wallpaper"])
    check("but again after a long while, in case it changed",
          sum(m["t"] == "wallpaper_get" for m in asked2) == 2)


def page(app: QApplication) -> None:
    print("\n-- the Share page")
    from tessera.ui.pages.share import SharePage
    from tessera.ui.theme import detect_palette

    real_backend = storage.backend
    storage.backend = lambda: storage.SSHFS
    try:
        hub, _sent, _asked, _replies = make_hub()
        view = SharePage(hub, detect_palette(app))
        connect(hub, ["storage", "storage_grant"])
        view._refresh_storage()
        check("a phone that can be allowed from here offers the button",
              view.storage_allow.isVisibleTo(view))
        check("and says why", "All files access" in view.storage_note.text(),
              view.storage_note.text())

        hub.storage_mount = Mount(storage.SSHFS, "/tmp/phone", "Ruchit's S25")
        hub._storage("mounted", "/tmp/phone")
        check("once mounted it can be opened", view.storage_open.isVisibleTo(view))
        check("or unmounted", view.storage_toggle.text() == "Unmount")
        check("and the allow button is gone", not view.storage_allow.isVisibleTo(view))

        hub.config.features.storage = False
        view._refresh_storage()
        check("switched off in Settings, the card goes", not view.storage_card.isVisibleTo(view))
    finally:
        storage.backend = real_backend


def main() -> int:
    app = QApplication(sys.argv)
    server_info()
    trust()
    sshfs_on_linux()
    sidebar()
    unmounting()
    drive_on_windows()
    orchestration(app)
    links_and_wallpaper(app)
    page(app)
    check("no exception escaped into Qt", not escaped(), "; ".join(escaped()))
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed:")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("\nall storage checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
