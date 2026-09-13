"""Keep a check run out of the real configuration."""

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
