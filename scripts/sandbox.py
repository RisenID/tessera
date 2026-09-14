"""Keep a check run out of the real configuration."""

from __future__ import annotations

import os
import sys
import tempfile

#: The system the checks are really running on, whatever TESSERA_PLATFORM says.
HOST = "windows" if sys.platform.startswith("win") else "linux"

_ESCAPED: list[str] = []


def isolate() -> str:
    """Point the settings and state directories at a throwaway. Returns it."""
    root = tempfile.mkdtemp(prefix="tessera-check-")
    for variable, leaf in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_CACHE_HOME", "cache"),
        # Windows ignores XDG and keeps settings and state under these.
        ("APPDATA", "roaming"),
        ("LOCALAPPDATA", "local"),
    ):
        os.environ[variable] = os.path.join(root, leaf)
    _catch_escapes()
    return root


def _catch_escapes() -> None:
    """Remember exceptions raised inside Qt slots, which Qt prints and swallows."""
    previous = sys.excepthook

    def hook(kind, value, trace):
        _ESCAPED.append(f"{kind.__name__}: {value}")
        previous(kind, value, trace)

    sys.excepthook = hook


def escaped() -> list[str]:
    """Exceptions that were printed rather than raised, since isolate()."""
    return list(_ESCAPED)


def only_on(*hosts: str):
    """Run a group of checks on these systems only, and say when it is skipped."""
    def wrap(group):
        def run(*args, **kwargs):
            if HOST not in hosts:
                print(f"\n-- skipped {group.__name__}: {' and '.join(hosts)} only")
                return None
            return group(*args, **kwargs)
        return run
    return wrap
