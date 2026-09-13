"""Entry point for the frozen Windows build."""

import multiprocessing
import sys

from tessera.app import main

if __name__ == "__main__":
    # Harmless on a single-process app, and the one line that stops a frozen
    # build spawning copies of itself if a dependency ever uses multiprocessing.
    multiprocessing.freeze_support()
    sys.exit(main())
