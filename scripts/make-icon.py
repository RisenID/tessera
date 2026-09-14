#!/usr/bin/env python3
"""Write the application icon, as .ico for Windows or as a PNG."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication                     # noqa: E402

from tessera.ui import appicon                                 # noqa: E402


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "packaging/windows/tessera.ico")
    QApplication([])
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".ico":
        if not appicon.tile(256).save(str(target), "ICO"):
            fallback = target.with_suffix(".png")
            appicon.tile(256).save(str(fallback), "PNG")
            print(f"no ICO support in this Qt build; wrote {fallback}")
            return 0
    else:
        appicon.tile(512).save(str(target))
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
