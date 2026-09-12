"""Setting up LDAC reception, from inside the app.

The work itself is a shell script -- scripts/build-ldac-decoder.sh -- because
it compiles C against whichever PipeWire the machine is running and there is
nothing Python can usefully do about that. What this module adds is everything
around it: finding the script wherever Tessera was installed from, checking the
build dependencies before a run rather than after it fails, and naming the
package that supplies each missing one.

See btcodecs for why LDAC needs any of this.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from ..core.proc import have, run
from . import btcodecs

log = logging.getLogger(__name__)

SCRIPT_NAME = "build-ldac-decoder.sh"

#: The launcher the package installs on PATH.
COMMAND = "tessera-ldac-decoder"


def script_path() -> Path | None:
    """The setup script, wherever this copy of Tessera came from.

    Three layouts are real: a git clone, where it sits in scripts/; an
    installed package, where it sits in the data directory beside the decoder
    sources it builds; and either of those reached through the launcher on
    PATH. TESSERA_LDAC_SCRIPT overrides the search, which is how the packaged
    layout gets tested without installing it. Returning None means the feature
    is unavailable rather than broken, and the UI says so.
    """
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


#: What the script needs, and the Fedora package that supplies it. Headers are
#: tested by asking the compiler rather than looking in /usr/include, which
#: accounts for CPATH and for layouts other than Fedora's -- the same test the
#: script makes, so the two can never disagree about whether a run will work.
BUILD_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("gcc", "gcc"),
    ("curl", "curl"),
    ("ldacBT.h", "libldac-devel"),
    ("bluetooth/bluetooth.h", "bluez-libs-devel"),
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


def install_command(packages: list[str]) -> str:
    """The command that installs the build dependencies."""
    return "sudo dnf install " + " ".join(packages)


def install_argv(packages: list[str]) -> list[str]:
    """The same thing through polkit, for running it from the app.

    Installing packages is a bigger step than anything else Tessera does on its
    own, so it is a button of its own with the command written next to it,
    never folded silently into the setup run.
    """
    return ["pkexec", "dnf", "install", "-y", *packages]


def setup_argv(action: str = "") -> list[str]:
    """Argument vector that builds and installs, or removes, the decoder."""
    script = script_path()
    if script is None:
        raise RuntimeError("The LDAC setup script is not installed with this copy of Tessera.")
    return ["bash", str(script), *( [action] if action else [] )]
