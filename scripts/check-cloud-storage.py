#!/usr/bin/env python3
"""Checks the phone's storage through the Cloud Files API, against a local SFTP server.

Windows only. Registers a sync root under a test id and removes it again.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox import HOST, isolate                                    # noqa: E402

isolate()

FAILURES: list[str] = []


def check(label: str, produced: object, wanted: object) -> None:
    ok = produced == wanted
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {produced!r}")
    if not ok:
        FAILURES.append(label)


def wait_for(condition, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if condition():
                return True
        except OSError:
            pass
        time.sleep(0.25)
    return False


# -- another process, as Explorer would be: a provider never hydrates for itself --

def ls(path: Path) -> list[str]:
    code = "import os,sys; print('\\n'.join(sorted(os.listdir(sys.argv[1]))))"
    out = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=60)
    return out.stdout.split()


def cat(path: Path) -> str:
    code = "import sys; sys.stdout.write(open(sys.argv[1], encoding='utf-8').read())"
    out = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=60)
    return out.stdout


def rm(path: Path) -> None:
    subprocess.run([sys.executable, "-c", "import os,sys; os.remove(sys.argv[1])", str(path)], timeout=60)


# -- a phone, served from a folder ---------------------------------------------

def serve(root: Path):
    import paramiko

    class Handle(paramiko.SFTPHandle):
        def stat(self):
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

    class Folder(paramiko.SFTPServerInterface):
        def _real(self, path: str) -> str:
            return os.path.join(root, *[p for p in path.split("/") if p])

        def list_folder(self, path):
            real = self._real(path)
            if not os.path.isdir(real):
                return paramiko.SFTP_NO_SUCH_FILE
            out = []
            for name in os.listdir(real):
                attrs = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(real, name)))
                attrs.filename = name
                out.append(attrs)
            return out

        def stat(self, path):
            real = self._real(path)
            if not os.path.exists(real):
                return paramiko.SFTP_NO_SUCH_FILE
            return paramiko.SFTPAttributes.from_stat(os.stat(real))

        lstat = stat

        def open(self, path, flags, attr):
            real = self._real(path)
            try:
                fd = os.open(real, flags | getattr(os, "O_BINARY", 0), 0o666)
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)
            if flags & os.O_WRONLY:
                mode = "ab" if flags & os.O_APPEND else "wb"
            elif flags & os.O_RDWR:
                mode = "a+b" if flags & os.O_APPEND else "r+b"
            else:
                mode = "rb"
            handle = Handle(flags)
            handle.filename = real
            handle.readfile = handle.writefile = os.fdopen(fd, mode)
            return handle

        def remove(self, path):
            os.remove(self._real(path))
            return paramiko.SFTP_OK

        def rename(self, old, new):
            os.replace(self._real(old), self._real(new))
            return paramiko.SFTP_OK

        posix_rename = rename

        def mkdir(self, path, attr):
            os.mkdir(self._real(path))
            return paramiko.SFTP_OK

        def rmdir(self, path):
            os.rmdir(self._real(path))
            return paramiko.SFTP_OK

        def chattr(self, path, attr):
            if attr.st_mtime is not None:
                os.utime(self._real(path), (attr.st_atime or attr.st_mtime, attr.st_mtime))
            return paramiko.SFTP_OK

    class Server(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            ok = (username, password) == ("tessera", "secret")
            return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return "password"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    key = paramiko.ECDSAKey.generate()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)

    def accept() -> None:
        while True:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            transport = paramiko.Transport(connection)
            transport.add_server_key(key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Folder)
            transport.start_server(server=Server())

    threading.Thread(target=accept, daemon=True).start()
    return listener, listener.getsockname()[1], f"{key.get_name()} {key.get_base64()}"


def main() -> int:
    if HOST != "windows":
        print("(Windows only)")
        return 0

    import paramiko

    from tessera.backends import storage_cloud
    from tessera.backends.sftp_remote import Remote
    from tessera.backends.storage import ServerInfo

    phone = Path(tempfile.mkdtemp(prefix="tessera-phone-"))
    here = Path(tempfile.mkdtemp(prefix="tessera-cloud-")) / "Test phone"
    (phone / "a.txt").write_text("hello phone", encoding="utf-8")
    (phone / "sub").mkdir()
    (phone / "sub" / "b.txt").write_text("in a folder", encoding="utf-8")

    listener, port, host_key = serve(phone)
    info = ServerInfo("127.0.0.1", port, "tessera", "secret", "/", host_key)
    identity = storage_cloud.root_id("tessera-check")

    print("-- trust")
    other = paramiko.ECDSAKey.generate()
    for label, wrong in (("a server with another key is refused",
                          ServerInfo("127.0.0.1", port, "tessera", "secret", "/",
                                     f"{other.get_name()} {other.get_base64()}")),
                         ("a wrong password is refused",
                          ServerInfo("127.0.0.1", port, "tessera", "wrong", "/", host_key))):
        said = ""
        try:
            Remote(wrong).connect()
        except RuntimeError as exc:
            said = str(exc)
        check(label, bool(said), True)

    print("\n-- the sync root")
    check("the Cloud Files API is available", storage_cloud.supported(), True)
    try:
        storage_cloud.mount(Remote(info), here, "Test phone", identity)
        check("the phone's top folder is listed when opened", ls(here), ["a.txt", "sub"])
        check("a file's contents come from the phone when opened", cat(here / "a.txt"), "hello phone")
        check("a subfolder is listed when opened", ls(here / "sub"), ["b.txt"])

        (here / "made-here.txt").write_text("from the computer", encoding="utf-8")
        check("a file made here reaches the phone",
              wait_for(lambda: (phone / "made-here.txt").read_text(encoding="utf-8") == "from the computer"), True)

        (phone / "added.txt").write_text("new on the phone", encoding="utf-8")
        check("a file added on the phone appears here", wait_for(lambda: "added.txt" in ls(here)), True)
        check("and opens", cat(here / "added.txt"), "new on the phone")

        (phone / "a.txt").write_text("changed on the phone", encoding="utf-8")
        future = time.time() + 5
        os.utime(phone / "a.txt", (future, future))
        check("a file changed on the phone reads its new contents",
              wait_for(lambda: cat(here / "a.txt") == "changed on the phone"), True)

        rm(here / "made-here.txt")
        check("deleting here deletes on the phone",
              wait_for(lambda: not (phone / "made-here.txt").exists()), True)

        (phone / "sub" / "b.txt").unlink()
        check("a file deleted on the phone goes from here",
              wait_for(lambda: "b.txt" not in ls(here / "sub")), True)
    finally:
        storage_cloud.unmount(here)
        storage_cloud.unregister(identity)
        listener.close()
        shutil.rmtree(here.parent, ignore_errors=True)
        shutil.rmtree(phone, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("all cloud storage checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
