"""Setting up LDAC reception, from inside the app."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from ..core import packages
from ..core.proc import have, run
from . import btcodecs

log = logging.getLogger(__name__)

SCRIPT_NAME = "build-ldac-decoder.sh"

#: The launcher the package installs on PATH.
COMMAND = "tessera-ldac-decoder"


def script_path() -> Path | None:
    """The setup script, wherever this copy of Tessera came from."""
    override = os.environ.get("TESSERA_LDAC_SCRIPT")
    if override:
        return Path(override) if Path(override).is_file() else None

    here = Path(__file__).resolve()
    candidates = [
        # A clone: tessera/backends/ldacdec.py -> <repo>/scripts/
        here.parents[2] / "scripts" / SCRIPT_NAME,
        Path(sys.prefix) / "share" / "tessera" / "ldac-decoder" / SCRIPT_NAME,
        Path("/usr/share/tessera/ldac-decoder") / SCRIPT_NAME,
        Path("/usr/local/share/tessera/ldac-decoder") / SCRIPT_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    found = shutil.which(COMMAND)
    return Path(found) if found else None


def available() -> bool:
    """Whether setup can be offered at all."""
    return script_path() is not None


def installed() -> bool:
    """Whether LDAC can already be received."""
    return btcodecs.ldac_receivable()


#: What the script needs, and the Fedora package that supplies it.
BUILD_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("gcc", "gcc"),
    ("curl", "curl"),
    ("ldacBT.h", "ldac-headers"),
    ("bluetooth/bluetooth.h", "bluez-headers"),
)


def _has_header(header: str) -> bool:
    if not have("gcc"):
        return False
    return run(["gcc", "-E", "-x", "c", "-"], timeout=20.0,
               stdin=f"#include <{header}>\n").ok


def missing_packages() -> list[str]:
    """Packages that must be installed before a build can succeed."""
    missing = []
    for requirement, package in BUILD_REQUIREMENTS:
        present = have(requirement) if "." not in requirement else _has_header(requirement)
        if not present and package not in missing:
            missing.append(package)
    return missing


def install_command(capabilities: list[str]) -> str:
    """What the user would type to install the build dependencies."""
    return packages.install_command(*capabilities) or packages.advice(*capabilities)


def install_argv(capabilities: list[str]) -> list[str]:
    """The same thing through polkit, for running it from the app."""
    return packages.install_argv(*capabilities)


def setup_argv(action: str = "") -> list[str]:
    """Argument vector that builds and installs, or removes, the decoder."""
    script = script_path()
    if script is None:
        raise RuntimeError("The LDAC setup script is not installed with this copy of Tessera.")
    return ["bash", str(script), *( [action] if action else [] )]
