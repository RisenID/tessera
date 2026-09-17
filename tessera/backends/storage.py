"""The phone's storage, as a folder in this computer's file manager."""

from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from ..core import platform
from ..core.proc import have

log = logging.getLogger(__name__)

#: The phone's shared storage, which is what people mean by "the phone's files".
DEFAULT_PATH = "/storage/emulated/0"

#: Marks the file manager sidebar entry as ours, so it can be found and removed
#: without touching anything the user put there.
PLACE_ID = "tessera-phone-storage"

SSHFS, GIO = "sshfs", "gio"
#: File Explorer's navigation pane, on Windows. See backends.storage_cloud.
CLOUD = "cloud-files"


@dataclass
class ServerInfo:
    """What the phone said about its server."""

    host: str
    port: int
    user: str
    password: str
    path: str = DEFAULT_PATH
    host_key: str = ""          # "ecdsa-sha2-nistp256 AAAA..."

    @classmethod
    def from_reply(cls, host: str, reply: dict) -> "ServerInfo | None":
        try:
            port = int(reply.get("port") or 0)
        except (TypeError, ValueError):
            return None
        user = str(reply.get("user") or "")
        password = str(reply.get("password") or "")
        if not (host and port and user and password):
            return None
        return cls(
            host=host,
            port=port,
            user=user,
            password=password,
            path=str(reply.get("path") or DEFAULT_PATH),
            host_key=str(reply.get("hostKey") or "").strip(),
        )

    @property
    def key_type(self) -> str:
        return self.host_key.split(" ", 1)[0] if self.host_key else ""

    @property
    def bracketed_host(self) -> str:
        return f"[{self.host}]" if ":" in self.host else self.host


@dataclass
class Mount:
    backend: str                # SSHFS | GIO | CLOUD
    location: str               # a directory for sshfs, an sftp:// URI for gio,
                                # the sync root folder for Cloud Files
    name: str
    #: The Cloud Files provider, so an unmount stops this mount and no later one.
    handle: object = field(default=None, compare=False, repr=False)

    @property
    def local_path(self) -> Path | None:
        return Path(self.location) if self.backend in (SSHFS, CLOUD) else None

    @property
    def uri(self) -> str:
        if self.backend == CLOUD:
            return "file:///" + quote(self.location.replace("\\", "/"), safe="/:")
        if self.backend == SSHFS:
            return "file://" + quote(self.location)
        return self.location


def backend() -> str:
    """Which way this computer can mount it: sshfs, gio, Cloud Files, or none."""
    if not platform.supported("storage"):
        return ""
    if platform.IS_WINDOWS:
        from . import storage_cloud

        return CLOUD if storage_cloud.supported() else ""
    if have("sshfs"):
        return SSHFS
    if have("gio"):
        return GIO
    return ""


def missing_advice() -> str:
    """What to install so that backend() finds something."""
    if platform.IS_WINDOWS:
        return "The phone's storage needs the Cloud Files API, from Windows 10 version 1709."
    return (
        "Mounting needs sshfs (the fuse-sshfs package) or GVfs, and this "
        "computer has neither."
    )


def mount_root() -> Path:
    return platform.state_dir() / "mounts"


def folder_name(name: str) -> str:
    """A phone's name as a folder name: readable, and nothing else."""
    cleaned = re.sub(r"[/\\\x00-\x1f]", "", name).strip().strip(".")
    return cleaned[:80] or "Phone"


def known_hosts_line(info: ServerInfo) -> str:
    """The one line an SSH client needs to trust this server and no other."""
    return f"[{info.host}]:{info.port} {info.host_key}"


def sshfs_command(info: ServerInfo, mountpoint: Path, known_hosts: Path) -> list[str]:
    remote = f"{info.user}@{info.bracketed_host}:{info.path}"
    options = [
        # The key the phone sent over the pinned link, and nothing else. A
        # server presenting any other key is refused rather than trusted.
        f"UserKnownHostsFile={known_hosts}",
        "StrictHostKeyChecking=yes",
        "GlobalKnownHostsFile=/dev/null",
        "password_stdin",
        # Straight to the password: trying the user's own keys first would
        # spend authentication attempts, or wake a key agent, for nothing.
        "PreferredAuthentications=password",
        "PubkeyAuthentication=no",
        # Wi-Fi drops for a moment far more often than it goes away.
        "reconnect",
        "ServerAliveInterval=15",
        "ServerAliveCountMax=3",
        # Files owned by the person looking at them, not by a uid that means
        # something only on the phone.
        "idmap=user",
        "fsname=tessera",
    ]
    if info.key_type:
        options.append(f"HostKeyAlgorithms={info.key_type}")
    command = ["sshfs", remote, str(mountpoint), "-p", str(info.port)]
    for option in options:
        command += ["-o", option]
    return command


def is_mounted(path: Path) -> bool:
    """Whether *path* is a mount point right now, from the kernel's own list."""
    target = str(path)
    try:
        with open("/proc/self/mounts", encoding="utf-8") as mounts:
            for line in mounts:
                fields = line.split()
                if len(fields) > 1 and _unescape(fields[1]) == target:
                    return True
    except OSError:
        pass
    return False


def _unescape(field: str) -> str:
    """/proc/mounts writes spaces and friends as octal escapes."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def _run(command: list[str], stdin: str = "", timeout: float = 25.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except FileNotFoundError:
        return 127, f"{command[0]} is not installed"
    except subprocess.TimeoutExpired:
        return 124, f"{command[0]} did not finish"
    return result.returncode, (result.stderr or result.stdout).strip()


# -- mounting ----------------------------------------------------------------


def mount(info: ServerInfo, name: str, sidebar: bool = True) -> Mount:
    """Mount the phone's storage. Raises RuntimeError with a readable reason."""
    if not info.host_key:
        raise RuntimeError(
            "The phone did not say which key its file server uses, so there is "
            "no way to know the server is the phone. Update the companion app."
        )
    chosen = backend()
    if chosen == CLOUD:
        from . import storage_cloud
        from .sftp_remote import Remote

        folder = storage_cloud.base_folder() / folder_name(name)
        provider = storage_cloud.mount(Remote(info), folder, name, storage_cloud.root_id(folder_name(name)))
        mounted = Mount(CLOUD, str(folder), name, provider)
    elif chosen == SSHFS:
        mounted = _mount_sshfs(info, name)
    elif chosen == GIO:
        mounted = _mount_gio(info, name)
    else:
        raise RuntimeError(missing_advice())
    # A sync root is in Explorer's navigation pane already; the sidebar files
    # are the Linux file managers'.
    if sidebar and mounted.backend != CLOUD:
        try:
            add_place(mounted)
        except OSError as exc:
            log.debug("could not add the sidebar entry: %s", exc)
    return mounted


def _mount_sshfs(info: ServerInfo, name: str) -> Mount:
    mountpoint = mount_root() / folder_name(name)
    if is_mounted(mountpoint):
        # Left behind by a crash or a dropped network: sshfs refuses to mount
        # over itself, and the old one answers nothing.
        _fusermount(mountpoint, lazy=True)
    mountpoint.mkdir(parents=True, exist_ok=True)

    known_hosts = platform.state_dir() / "known_hosts"
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    # Rewritten each time: the phone's key is whatever it says now, over the
    # pinned link, and an old line for a different port would be noise.
    known_hosts.write_text(known_hosts_line(info) + "\n", encoding="utf-8")
    os.chmod(known_hosts, 0o600)

    code, output = _run(
        sshfs_command(info, mountpoint, known_hosts), stdin=info.password + "\n"
    )
    if code != 0 or not is_mounted(mountpoint):
        _remove_empty(mountpoint)
        raise RuntimeError(_explain(output) or "sshfs could not mount the phone.")
    return Mount(SSHFS, str(mountpoint), name)


def _mount_gio(info: ServerInfo, name: str) -> Mount:
    """GVfs, for a desktop without sshfs."""
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    known_hosts = ssh_dir / "known_hosts"
    _run(["ssh-keygen", "-R", f"[{info.host}]:{info.port}", "-f", str(known_hosts)])
    with open(known_hosts, "a", encoding="utf-8") as handle:
        handle.write(known_hosts_line(info) + "\n")

    uri = f"sftp://{info.user}@{info.bracketed_host}:{info.port}{quote(info.path)}"
    code, output = _run(["gio", "mount", uri], stdin=info.password + "\n")
    if code != 0 and "already mounted" not in output.lower():
        raise RuntimeError(_explain(output) or "GVfs could not mount the phone.")
    return Mount(GIO, uri, name)


def unmount(mounted: Mount) -> None:
    """Unmount, and take the sidebar entry away with it. Never raises."""
    if mounted.backend == CLOUD:
        from . import storage_cloud

        # The folder stays in the navigation pane, its files unavailable until next time.
        storage_cloud.unmount(Path(mounted.location), mounted.handle)
        return

    try:
        remove_place(mounted)
    except OSError as exc:
        log.debug("could not remove the sidebar entry: %s", exc)

    if mounted.backend == GIO:
        _run(["gio", "mount", "-u", mounted.location])
        return
    path = Path(mounted.location)
    if is_mounted(path):
        # Lazy when the ordinary way fails: the phone is usually gone by the
        # time this runs, and a file manager still looking at the folder would
        # otherwise keep it mounted and hanging.
        if not _fusermount(path, lazy=False):
            _fusermount(path, lazy=True)
    _remove_empty(path)


def forget(name: str) -> None:
    """Take a forgotten phone out of the file manager for good."""
    if backend() == CLOUD:
        from . import storage_cloud

        storage_cloud.unregister(storage_cloud.root_id(folder_name(name)))


def _fusermount(path: Path, lazy: bool) -> bool:
    flags = "-uz" if lazy else "-u"
    for tool in ("fusermount3", "fusermount"):
        if have(tool):
            code, _output = _run([tool, flags, str(path)], timeout=10.0)
            return code == 0
    code, _output = _run(["umount", *(["-l"] if lazy else []), str(path)], timeout=10.0)
    return code == 0


def _remove_empty(path: Path) -> None:
    """Remove an unmounted mount point, but only if it really is empty."""
    try:
        if not is_mounted(path):
            path.rmdir()
    except OSError:
        pass


def _explain(output: str) -> str:
    lowered = output.lower()
    if "host key verification failed" in lowered or "remote host identification" in lowered:
        return (
            "The file server did not present the key the phone said it would, "
            "so it was not trusted."
        )
    if "permission denied" in lowered:
        return "The phone refused the password its own server was given."
    if "connection refused" in lowered or "no route" in lowered:
        return "The phone's file server could not be reached on this network."
    return output.splitlines()[-1] if output else ""


def open_location(mounted: Mount) -> None:
    if mounted.backend == CLOUD:
        if platform.REAL == "windows":
            os.startfile(mounted.location)                  # type: ignore[attr-defined]
        return
    subprocess.Popen(
        ["xdg-open", mounted.uri],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# -- the file manager's sidebar ----------------------------------------------


def _gtk_bookmarks() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "gtk-3.0" / "bookmarks"


def _kde_places() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "user-places.xbel"


def add_place(mounted: Mount) -> None:
    """Show the phone in the file manager's sidebar."""
    remove_place(mounted)

    bookmarks = _gtk_bookmarks()
    bookmarks.parent.mkdir(parents=True, exist_ok=True)
    existing = bookmarks.read_text(encoding="utf-8") if bookmarks.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    # One line per bookmark: the label is the phone's name with newlines and
    # control characters taken out, so it cannot add a second entry.
    bookmarks.write_text(
        existing + f"{mounted.uri} {folder_name(mounted.name)}\n", encoding="utf-8"
    )

    places = _kde_places()
    if places.exists():
        text = places.read_text(encoding="utf-8")
        if "</xbel>" in text:
            entry = (
                f' <bookmark href="{_xml(mounted.uri)}">\n'
                f"  <title>{_xml(folder_name(mounted.name))}</title>\n"
                "  <info>\n"
                '   <metadata owner="http://freedesktop.org">\n'
                '    <bookmark:icon name="smartphone"/>\n'
                "   </metadata>\n"
                '   <metadata owner="http://www.kde.org">\n'
                f"    <ID>{PLACE_ID}</ID>\n"
                "    <isSystemItem>false</isSystemItem>\n"
                "   </metadata>\n"
                "  </info>\n"
                " </bookmark>\n"
            )
            head, _sep, tail = text.rpartition("</xbel>")
            _write_atomically(places, head + entry + "</xbel>" + tail)


def remove_place(mounted: Mount) -> None:
    """Take the sidebar entry away again, leaving everything else as it was."""
    bookmarks = _gtk_bookmarks()
    if bookmarks.exists():
        lines = bookmarks.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [
            line for line in lines
            if unquote(line.split(" ", 1)[0].strip()) != unquote(mounted.uri)
        ]
        if len(kept) != len(lines):
            bookmarks.write_text("".join(kept), encoding="utf-8")

    places = _kde_places()
    if places.exists():
        text = places.read_text(encoding="utf-8")
        pattern = re.compile(
            r"[ \t]*<bookmark\b(?:(?!</bookmark>).)*?<ID>"
            + re.escape(PLACE_ID)
            + r"</ID>(?:(?!</bookmark>).)*?</bookmark>[ \t]*\n?",
            re.S,
        )
        cleaned = pattern.sub("", text)
        if cleaned != text:
            _write_atomically(places, cleaned)


def _xml(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _write_atomically(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
