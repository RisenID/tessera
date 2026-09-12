"""Entry point for the frozen Windows build.

PyInstaller needs a script rather than a module, and a frozen build must not
rely on the working directory, so this is deliberately the whole of it.
"""

import multiprocessing
import sys

from tessera.app import main

if __name__ == "__main__":
    # Harmless on a single-process app, and the one line that stops a frozen
    # build spawning copies of itself if a dependency ever uses multiprocessing.
    multiprocessing.freeze_support()
    sys.exit(main())
