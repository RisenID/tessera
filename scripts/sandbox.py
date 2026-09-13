"""Keep a check run out of the real configuration.

A check builds a `Config`, and anything it exercises that saves -- a volume
slider, a settings switch, the mute box -- writes that object to disk. With a
default `Config()` that is a blank configuration on top of the user's: pairing
token, phone address and every preference gone, and the app asking to be paired
again the next time it starts. It happened once; this makes it impossible.

Import and call `isolate()` before importing anything from `tessera`, because
the paths are read at import time.
"""

from __future__ import annotations

import os
import tempfile


def isolate() -> str:
    """Point the XDG directories at a throwaway. Returns the directory."""
    root = tempfile.mkdtemp(prefix="tessera-check-")
    for variable, leaf in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_CACHE_HOME", "cache"),
    ):
        os.environ[variable] = os.path.join(root, leaf)
    return root
