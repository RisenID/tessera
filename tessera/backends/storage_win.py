"""The phone's storage as a drive on Windows, through WinFsp and SSHFS-Win.

SSHFS-Win's sshfs.exe is sshfs built for Cygwin, with WinFsp standing in for
FUSE. It is run directly rather than through ``net use \\\\sshfs\\...``,
because that route cannot be told which host key to trust, and pinning the key
the phone sent over the paired link is what makes the mount safe.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path

from ..core import platform
from . import storage

log = logging.getLogger(__name__)

#: Tried from the end of the alphabet, where removable drives and network
#: shares rarely are.
LETTERS = "ZYXWVUTSRQPONMLKJIHG"

#: How long sshfs.exe gets to put the drive up.
MOUNT_SECONDS = 25.0

#: sshfs.exe holds the drive for as long as it runs, so the process is the
#: mount. By drive letter.
_processes: dict[str, subprocess.Popen] = {}


def sshfs_path() -> str:
    return platform.find_tool("sshfs")


def winfsp_installed() -> bool:
    """WinFsp's driver DLL, where its installer puts it."""
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(variable)
        if base and (Path(base) / "WinFsp" / "bin" / "winfsp-x64.dll").is_file():
            return True
    return _winfsp_in_registry()


def _winfsp_in_registry() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WinFsp", 0,
                                winreg.KEY_READ | view) as key:
                directory, _kind = winreg.QueryValueEx(key, "InstallDir")
        except OSError:
            continue
        if (Path(directory) / "bin").is_dir():
            return True
    return False


def missing() -> list[str]:
    """What is still to install, by its name in core.packages."""
    gaps = []
    if not winfsp_installed():
        gaps.append("winfsp")
    if not sshfs_path():
        gaps.append("sshfs-win")
    return gaps


def ready() -> bool:
    return not missing()


def cygwin_path(path: "Path | str") -> str:
    """C:\\Users\\me\\x as /cygdrive/c/Users/me/x, which is what sshfs.exe reads."""
    text = str(path).replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/?(.*)$", text)
    if not match:
        return text
    return f"/cygdrive/{match.group(1).lower()}/{match.group(2)}"


def volume_name(name: str) -> str:
    """The drive's label: nothing that ends an -o option, nothing Explorer refuses."""
    cleaned = re.sub(r'[\\/:*?"<>|,=\x00-\x1f]', "", name).strip()
    return cleaned[:32] or "Phone"


def used_letters() -> set[str]:
    if platform.REAL != "windows":
        return set()
    import ctypes

    mask = ctypes.windll.kernel32.GetLogicalDrives()        # type: ignore[attr-defined]
    return {chr(ord("A") + index) for index in range(26) if mask >> index & 1}


def free_letter(taken: "set[str] | None" = None) -> str:
    in_use = used_letters() if taken is None else taken
    for letter in LETTERS:
        if letter not in in_use:
            return letter
    return ""


def sshfs_command(info: storage.ServerInfo, letter: str, known_hosts: Path,
                  name: str, sshfs: str = "sshfs.exe") -> list[str]:
    remote = f"{info.user}@{info.bracketed_host}:{info.path}"
    options = [
        # As on Linux: the key from the paired link, and no other.
        f"UserKnownHostsFile={cygwin_path(known_hosts)}",
        "StrictHostKeyChecking=yes",
        "GlobalKnownHostsFile=/dev/null",
        "password_stdin",
        "PreferredAuthentications=password",
        "PubkeyAuthentication=no",
        "reconnect",
        "ServerAliveInterval=15",
        "ServerAliveCountMax=3",
        # Owned by whoever is signed in to Windows, as SSHFS-Win's own
        # launcher sets it up.
        "idmap=user",
        "uid=-1",
        "gid=-1",
        "umask=000",
        f"volname={volume_name(name)}",
    ]
    if info.key_type:
        options.append(f"HostKeyAlgorithms={info.key_type}")
    # -f: in the foreground, so there is a process to stop.
    command = [sshfs, "-f", remote, f"{letter}:", "-p", str(info.port)]
    for option in options:
        command += ["-o", option]
    return command


#: Explorer's per-user drive icon and label, by letter.
DRIVE_KEY = r"Software\Classes\Applications\Explorer.exe\Drives\{letter}"
#: The phone in Windows' own icon set.
PHONE_ICON = r"%SystemRoot%\System32\imageres.dll,42"


def dress_drive(letter: str, name: str) -> None:
    """Show the drive as the phone: its icon and its name."""
    try:
        import winreg
    except ImportError:
        return
    base = DRIVE_KEY.format(letter=letter)
    try:
        for sub, value in (("DefaultIcon", PHONE_ICON), ("DefaultLabel", name)):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{sub}") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_EXPAND_SZ if sub == "DefaultIcon"
                                  else winreg.REG_SZ, value)
    except OSError as exc:
        log.debug("could not set the drive icon: %s", exc)


def undress_drive(letter: str) -> None:
    try:
        import winreg
    except ImportError:
        return
    base = DRIVE_KEY.format(letter=letter)
    for sub in (rf"{base}\DefaultIcon", rf"{base}\DefaultLabel", base):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            pass


def _spawn(command: list[str], env: dict, log_path: Path) -> subprocess.Popen:
    with open(log_path, "w", encoding="utf-8") as output:
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            creationflags=platform.no_window_flags(),
        )


def _exists(root: str) -> bool:
    return os.path.isdir(root)


def mount(info: storage.ServerInfo, name: str) -> storage.Mount:
    """Put the phone up as a drive. Raises RuntimeError with a readable reason."""
    sshfs = sshfs_path()
    if not sshfs or not winfsp_installed():
        raise RuntimeError(storage.missing_advice())
    letter = free_letter()
    if not letter:
        raise RuntimeError(
            "Every drive letter from G: to Z: is in use, so there is nowhere to "
            "put the phone."
        )

    state = platform.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    # Rewritten each time, as on Linux: the key is whatever the phone says now.
    known_hosts = state / "known_hosts"
    known_hosts.write_text(storage.known_hosts_line(info) + "\n", encoding="utf-8")
    log_path = state / "sshfs.log"

    # sshfs.exe starts ssh.exe by name, and both live in SSHFS-Win's bin folder.
    env = {
        **os.environ,
        "PATH": str(Path(sshfs).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    # Before the drive appears: Explorer reads these when it first shows it.
    dress_drive(letter, volume_name(name))
    process = _spawn(sshfs_command(info, letter, known_hosts, name, sshfs), env, log_path)
    try:
        process.stdin.write(info.password + "\n")
        process.stdin.close()
    except OSError:
        pass                        # it has already exited, and the log says why

    root = f"{letter}:\\"
    deadline = time.monotonic() + MOUNT_SECONDS
    while time.monotonic() < deadline and process.poll() is None:
        if _exists(root):
            _processes[letter] = process
            return storage.Mount(storage.SSHFS_WIN, root, name)
        time.sleep(0.2)

    _stop(process)
    undress_drive(letter)
    try:
        output = log_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        output = ""
    raise RuntimeError(storage._explain(output) or "sshfs could not mount the phone.")


def unmount(mounted: storage.Mount) -> None:
    """Stop sshfs.exe, which takes the drive away with it. Never raises."""
    letter = mounted.location[:1].upper()
    process = _processes.pop(letter, None)
    if process is not None:
        _stop(process)
    undress_drive(letter)


def _stop(process) -> None:
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    except OSError as exc:
        log.debug("stopping sshfs: %s", exc)
