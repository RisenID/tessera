"""Telling the user how to install something, on whichever distribution.

Every feature Tessera cannot do by itself leans on a program someone has to
install: adb, scrcpy, ffmpeg, the PipeWire tools, a compiler. Saying so is
easy; saying it *usefully* means naming the package and the command, and both
differ by distribution -- "sudo dnf install android-tools" is wrong advice on
Debian, Arch and openSUSE alike, and advice that does not work is barely
better than none.

So the program to install is named here by what it is for, and the package
that supplies it is looked up per package manager.

Two deliberate limits:

* The Fedora names are the only ones verified against a real system, because
  that is the system this was built on. The rest are the packages those
  distributions are known to ship, and are the best available guess rather
  than a promise.
* A capability with no entry for the detected manager falls back to naming the
  program itself. "Install ffmpeg" with no package name is honest; a made-up
  package name is not.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Manager:
    """A package manager, and how to install with it."""

    key: str
    program: str
    #: Arguments that install, after the program name.
    install: tuple[str, ...]
    #: Extra argument that makes it non-interactive, for a pkexec run.
    assume_yes: str = "-y"

    def command(self, packages: list[str]) -> str:
        """The line to show the user, to run themselves."""
        return " ".join(["sudo", self.program, *self.install, *packages])

    def argv(self, packages: list[str]) -> list[str]:
        """The same install through polkit, for running from the app."""
        args = [*self.install]
        if self.assume_yes:
            args.append(self.assume_yes)
        return ["pkexec", self.program, *args, *packages]


MANAGERS: dict[str, Manager] = {
    "dnf": Manager("dnf", "dnf", ("install",)),
    "apt": Manager("apt", "apt", ("install",)),
    "pacman": Manager("pacman", "pacman", ("-S",), "--noconfirm"),
    "zypper": Manager("zypper", "zypper", ("install",)),
    "apk": Manager("apk", "apk", ("add",), ""),
    "xbps": Manager("xbps", "xbps-install", ("-S",), "-y"),
    "emerge": Manager("emerge", "emerge", ("--ask=n",), ""),
    "eopkg": Manager("eopkg", "eopkg", ("install",), "-y"),
}

#: Which manager each distribution uses, by os-release ID and ID_LIKE.
FAMILIES: dict[str, str] = {
    "fedora": "dnf", "rhel": "dnf", "centos": "dnf", "almalinux": "dnf",
    "rocky": "dnf", "nobara": "dnf", "bazzite": "dnf",
    "debian": "apt", "ubuntu": "apt", "linuxmint": "apt", "pop": "apt",
    "elementary": "apt", "zorin": "apt", "raspbian": "apt", "devuan": "apt",
    "arch": "pacman", "manjaro": "pacman", "endeavouros": "pacman",
    "garuda": "pacman", "cachyos": "pacman", "artix": "pacman",
    "opensuse": "zypper", "opensuse-leap": "zypper",
    "opensuse-tumbleweed": "zypper", "sles": "zypper", "suse": "zypper",
    "alpine": "apk", "postmarketos": "apk",
    "void": "xbps",
    "gentoo": "emerge",
    "solus": "eopkg",
}

#: What Tessera needs, and the package that supplies it per manager. The key
#: is the program or capability; "program" is what to look for on PATH.
PACKAGES: dict[str, dict[str, str]] = {
    "adb": {
        "dnf": "android-tools", "apt": "adb", "pacman": "android-tools",
        "zypper": "android-tools", "apk": "android-tools", "xbps": "android-tools",
    },
    "scrcpy": {
        "dnf": "scrcpy", "apt": "scrcpy", "pacman": "scrcpy",
        "zypper": "scrcpy", "apk": "scrcpy", "xbps": "scrcpy",
    },
    "ffmpeg": {
        "dnf": "ffmpeg", "apt": "ffmpeg", "pacman": "ffmpeg",
        "zypper": "ffmpeg", "apk": "ffmpeg", "xbps": "ffmpeg",
    },
    # Out-of-tree kernel module, so the package is the DKMS build almost
    # everywhere. It has to rebuild for each kernel, which is why the name
    # differs from the plain module name.
    "v4l2loopback": {
        "dnf": "v4l2loopback", "apt": "v4l2loopback-dkms",
        "pacman": "v4l2loopback-dkms", "zypper": "v4l2loopback-kmp-default",
        "xbps": "v4l2loopback-dkms",
    },
    # pw-dump and pw-link.
    "pipewire-tools": {
        "dnf": "pipewire-utils", "apt": "pipewire-bin", "pacman": "pipewire",
        "zypper": "pipewire-tools", "apk": "pipewire-tools", "xbps": "pipewire",
    },
    # pactl.
    "pactl": {
        "dnf": "pulseaudio-utils", "apt": "pulseaudio-utils", "pacman": "libpulse",
        "zypper": "pulseaudio-utils", "apk": "pulseaudio-utils", "xbps": "pulseaudio-utils",
    },
    "bluez": {
        "dnf": "bluez", "apt": "bluez", "pacman": "bluez-utils",
        "zypper": "bluez", "apk": "bluez", "xbps": "bluez",
    },
    "gcc": {
        "dnf": "gcc", "apt": "build-essential", "pacman": "base-devel",
        "zypper": "gcc", "apk": "build-base", "xbps": "gcc",
    },
    # The only Python dependency. Deliberately the distribution's package
    # rather than pip's: Fedora and Debian both mark the system interpreter as
    # externally managed, so pip refuses to install into it, and a second copy
    # of Qt in site-packages is not something to inflict on anyone.
    "pyside6": {
        "dnf": "python3-pyside6", "apt": "python3-pyside6.qtwidgets",
        "pacman": "pyside6", "zypper": "python3-pyside6",
        "apk": "py3-pyside6", "xbps": "python3-pyside6",
    },
    "curl": {
        "dnf": "curl", "apt": "curl", "pacman": "curl",
        "zypper": "curl", "apk": "curl", "xbps": "curl",
    },
    # Sony's LDAC encoder headers, for building the decoder shim against its
    # ABI. See native/ldac-decoder.
    "ldac-headers": {
        "dnf": "libldac-devel", "apt": "libldacbt-enc-dev", "pacman": "libldac",
        "zypper": "libldac-devel", "xbps": "libldac-devel",
    },
    # bluetooth/bluetooth.h.
    "bluez-headers": {
        "dnf": "bluez-libs-devel", "apt": "libbluetooth-dev",
        "pacman": "bluez-libs", "zypper": "bluez-devel", "apk": "bluez-dev",
        "xbps": "bluez-devel",
    },
}


#: Capabilities whose key is also what you would type at a package manager.
#:
#: For these the key is good advice on its own, which matters for a
#: distribution with no table entry above: "emerge ffmpeg" resolves, and so
#: does every other unambiguous program name. The distinction is only needed
#: because the rest -- header sets, tool bundles, kernel modules -- have keys
#: that are descriptions, not package names.
PROGRAMS = frozenset({"adb", "scrcpy", "ffmpeg", "gcc", "curl", "pactl", "bluez"})

#: How to describe a capability when no package name is known for it.
#:
#: The key itself is a fine fallback for a program -- "install ffmpeg" reads
#: correctly -- and a poor one for anything else: printing "apk add
#: ldac-headers" invents a package that does not exist, which is exactly the
#: kind of advice this module exists to avoid.
LABELS: dict[str, str] = {
    "pipewire-tools": "the PipeWire command-line tools (pw-dump, pw-link)",
    "pactl": "pactl",
    "bluez": "BlueZ",
    "v4l2loopback": "the v4l2loopback kernel module",
    "ldac-headers": "Sony's LDAC encoder headers",
    "bluez-headers": "the BlueZ development headers",
    "gcc": "a C compiler",
    "pyside6": "PySide6, the Qt bindings for Python",
}


def _os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        try:
            text = path.read_text("utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            key, _, value = line.partition("=")
            if key:
                values[key.strip()] = value.strip().strip('"').strip("'")
        break
    return values


def detect() -> Manager | None:
    """The package manager this system uses, or None if it cannot be told.

    os-release comes first because it is declarative and right even on a
    system with several managers installed; the presence of a binary is the
    fallback for a distribution not listed above.
    """
    release = _os_release()
    candidates = [release.get("ID", "")] + release.get("ID_LIKE", "").split()
    for candidate in candidates:
        key = FAMILIES.get(candidate.strip().lower())
        if key:
            return MANAGERS[key]

    for key, manager in MANAGERS.items():
        if shutil.which(manager.program):
            log.debug("no os-release match; found %s on PATH", manager.program)
            return manager
    return None


def distribution() -> str:
    """A readable name for this system, for a message that needs one."""
    release = _os_release()
    return release.get("PRETTY_NAME") or release.get("NAME") or "this system"


def _split(capabilities: "tuple[str, ...] | list[str]",
           manager: Manager | None) -> tuple[list[str], list[str]]:
    """Package names this manager knows, and descriptions of the rest."""
    named: list[str] = []
    unnamed: list[str] = []
    for capability in capabilities:
        name = PACKAGES.get(capability, {}).get(manager.key) if manager else None
        if not name and capability in PROGRAMS:
            name = capability
        if name:
            if name not in named:
                named.append(name)
        else:
            # A program name is its own good advice; anything else needs words.
            described = LABELS.get(capability, capability)
            if described not in unnamed:
                unnamed.append(described)
    return named, unnamed


def names_for(capabilities: "tuple[str, ...] | list[str]",
              manager: Manager | None = None) -> list[str]:
    """Package names for *capabilities* that this manager is known to have."""
    return _split(capabilities, manager or detect())[0]


def install_command(*capabilities: str) -> str:
    """A command the user can run, or "" when nothing can be named."""
    manager = detect()
    named, _ = _split(capabilities, manager)
    if not named or manager is None:
        return ""
    return manager.command(named)


def install_argv(*capabilities: str) -> list[str]:
    """The install as an argv for running through polkit. Empty when unknown.

    Covers only what can be named. Anything left over is caught by the caller
    checking its dependencies again afterwards, which is a better outcome than
    refusing to install the part that is known.
    """
    manager = detect()
    named, _ = _split(capabilities, manager)
    if manager is None or not named:
        return []
    return manager.argv(named)


def advice(*capabilities: str) -> str:
    """One sentence: what is missing, and how to get it on this system."""
    manager = detect()
    named, unnamed = _split(capabilities, manager)

    parts = []
    if named and manager is not None:
        parts.append(f"Install it with: {manager.command(named)}")
    elif named:
        parts.append("Install " + _join(named) + " with your package manager.")
    if unnamed:
        lead = "You will also need " if parts else "You need "
        verb = "have" if len(unnamed) > 1 else "has"
        parts.append(f"{lead}{_join(unnamed)}, which {verb} no known package on "
                     f"{distribution()}.")
    return " ".join(parts) or "It is not installed."


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
