"""Telling the user how to install something, on whichever distribution."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import platform

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

    #: Windows installers need no root, and there is no pkexec to ask.
    elevated: bool = True
    #: Arguments that go immediately before the package names, like winget's
    #: --id, which only means anything in that position.
    before_names: tuple[str, ...] = ()
    #: winget installs one package per invocation; the others take a list.
    one_at_a_time: bool = False

    def _words(self, packages: list[str], interactive: bool) -> list[list[str]]:
        """One command per invocation this manager needs."""
        args = [*self.install]
        if not interactive and self.assume_yes:
            args.append(self.assume_yes)
        args += self.before_names
        groups = [[name] for name in packages] if self.one_at_a_time else [packages]
        return [[self.program, *args, *group] for group in groups]

    def command(self, packages: list[str]) -> str:
        """The line to show the user, to run themselves."""
        prefix = ["sudo "] if self.elevated else [""]
        return "; ".join(
            prefix[0] + " ".join(words)
            for words in self._words(packages, interactive=True)
        )

    def argv(self, packages: list[str]) -> list[str]:
        """The same install, for running from the app."""
        commands = self._words(packages, interactive=False)
        if self.elevated:
            return ["pkexec", *commands[0][0:1], *commands[0][1:]]
        if len(commands) == 1:
            return commands[0]
        # More than one invocation and no shell to chain them: PowerShell is
        # the one interpreter every Windows install has.
        joined = "; ".join(" ".join(words) for words in commands)
        return ["powershell", "-NoProfile", "-Command", joined]


MANAGERS: dict[str, Manager] = {
    "dnf": Manager("dnf", "dnf", ("install",)),
    "apt": Manager("apt", "apt", ("install",)),
    "pacman": Manager("pacman", "pacman", ("-S",), "--noconfirm"),
    "zypper": Manager("zypper", "zypper", ("install",)),
    "apk": Manager("apk", "apk", ("add",), ""),
    "xbps": Manager("xbps", "xbps-install", ("-S",), "-y"),
    "emerge": Manager("emerge", "emerge", ("--ask=n",), ""),
    "eopkg": Manager("eopkg", "eopkg", ("install",), "-y"),
    # Windows. winget ships with Windows 11 and recent 10; the other two are
    # what people who install command-line tools already have.
    "winget": Manager(
        "winget", "winget", ("install",), "--accept-package-agreements",
        elevated=False, before_names=("--exact", "--id"), one_at_a_time=True,
    ),
    "choco": Manager("choco", "choco", ("install",), "-y", elevated=False),
    "scoop": Manager("scoop", "scoop", ("install",), "", elevated=False),
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
        # Google ships adb inside the platform-tools bundle, which is also
        # what scrcpy for Windows carries; either satisfies this.
        "winget": "Google.PlatformTools", "choco": "adb", "scoop": "adb",
    },
    "scrcpy": {
        "dnf": "scrcpy", "apt": "scrcpy", "pacman": "scrcpy",
        "zypper": "scrcpy", "apk": "scrcpy", "xbps": "scrcpy",
        "winget": "Genymobile.scrcpy", "choco": "scrcpy", "scoop": "scrcpy",
    },
    "ffmpeg": {
        "dnf": "ffmpeg", "apt": "ffmpeg", "pacman": "ffmpeg",
        "zypper": "ffmpeg", "apk": "ffmpeg", "xbps": "ffmpeg",
        "winget": "Gyan.FFmpeg", "choco": "ffmpeg", "scoop": "ffmpeg",
    },
    # Out-of-tree kernel module, so the package is the DKMS build
    # almost everywhere.
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
    # The Qt bindings, from the distribution on Linux.
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


#: Repositories to enable first, where the distribution itself lacks the package.
REPOSITORIES: dict[str, dict[str, str]] = {
    "scrcpy": {"dnf": "dnf copr enable -y zeno/scrcpy"},
    "v4l2loopback": {
        "dnf": "dnf install -y https://mirrors.rpmfusion.org/free/fedora/"
               "rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm",
    },
}

#: Capabilities whose key is also what you would type at a package manager.
PROGRAMS = frozenset({"adb", "scrcpy", "ffmpeg", "gcc", "curl", "pactl", "bluez"})

#: How to describe a capability when no package name is known for it.
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
    """The package manager this system uses, or None if it cannot be told."""
    if platform.IS_WINDOWS:
        # In preference order: winget is on the machine already, the others are
        # only there if the user put them there -- but if they did, that is
        # where their tools live.
        for key in ("scoop", "choco", "winget"):
            if platform.have_tool(MANAGERS[key].program):
                return MANAGERS[key]
        return MANAGERS["winget"]
    if platform.IS_MACOS:
        return None
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
    if not platform.IS_LINUX:
        return platform.describe()
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


def _repositories(capabilities, manager: Manager | None) -> list[str]:
    if manager is None:
        return []
    found = (REPOSITORIES.get(c, {}).get(manager.key) for c in dict.fromkeys(capabilities))
    return [command for command in found if command]


def _with_repositories(capabilities, manager: Manager, named: list[str]) -> str:
    """The install line, with any repository setup before it."""
    steps = [f"sudo {command}" for command in _repositories(capabilities, manager)]
    return " && ".join([*steps, manager.command(named)])


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
    return _with_repositories(capabilities, manager, named)


def install_argv(*capabilities: str) -> list[str]:
    """The install as an argv for running through polkit. Empty when unknown."""
    manager = detect()
    named, _ = _split(capabilities, manager)
    if manager is None or not named:
        return []
    repositories = _repositories(capabilities, manager)
    if not repositories:
        return manager.argv(named)
    install = " ".join(manager._words(named, interactive=False)[0])
    return ["pkexec", "sh", "-c", " && ".join([*repositories, install])]


def advice(*capabilities: str) -> str:
    """One sentence: what is missing, and how to get it on this system."""
    manager = detect()
    named, unnamed = _split(capabilities, manager)

    parts = []
    if named and manager is not None:
        parts.append(f"Install it with: {_with_repositories(capabilities, manager, named)}")
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
