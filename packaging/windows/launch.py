"""Entry point for the frozen Windows build."""

import multiprocessing
import sys

#: Imported only when used, so the build's analysis may miss them; the self
#: test names them so a build without them fails there instead of on a
#: user's machine.
LATE_IMPORTS = (
    "PySide6.QtSvg",
    "PySide6.QtMultimedia",
    "winrt.windows.foundation",
    "winrt.windows.devices.enumeration",
    "winrt.windows.media.audio",
    "winrt.windows.applicationmodel.calls",
    "tessera.backends.bluetooth_win",
    "tessera.backends.calls_win",
    "winrt.windows.storage",
    "winrt.windows.storage.provider",
    "winrt.windows.security.cryptography",
    "paramiko",
    "tessera.backends.cloudfiles",
    "tessera.backends.sftp_remote",
    "tessera.backends.storage_cloud",
    "tessera.backends.webcam_win",
)


def self_test(report: str) -> int:
    """Import everything the app can reach. 0 only if all of it imports.

    For the build script: a windowed build that fails on import shows a dialog
    and keeps running, so starting the real app proves nothing -- and starting
    it for real would connect to whatever phone is paired.
    """
    import importlib
    import pkgutil
    import traceback

    failed = []
    names = ["tessera.app", *LATE_IMPORTS]
    try:
        import tessera

        names += [
            module.name
            for module in pkgutil.walk_packages(tessera.__path__, "tessera.")
            if module.name != "tessera.__main__"
        ]
    except Exception:                                   # noqa: BLE001
        failed.append(traceback.format_exc())
    for name in names:
        try:
            importlib.import_module(name)
        except Exception:                               # noqa: BLE001
            failed.append(f"{name}:\n{traceback.format_exc()}")
    try:
        from tessera.ui import appicon

        if appicon.source() is None:
            failed.append(f"the app icon {appicon.FILE} is not bundled")
    except Exception:                                   # noqa: BLE001
        failed.append(traceback.format_exc())
    if report:
        with open(report, "w", encoding="utf-8") as out:
            out.write("\n".join(failed) or f"imported {len(set(names))} modules\n")
    return 1 if failed else 0


if __name__ == "__main__":
    # Harmless on a single-process app, and the one line that stops a frozen
    # build spawning copies of itself if a dependency ever uses multiprocessing.
    multiprocessing.freeze_support()
    if "--self-test" in sys.argv:
        at = sys.argv.index("--self-test")
        sys.exit(self_test(sys.argv[at + 1] if len(sys.argv) > at + 1 else ""))
    if "--cleanup" in sys.argv:
        # Run by the uninstaller: the phones leave File Explorer's navigation pane.
        try:
            from tessera.backends import storage_cloud

            storage_cloud.unregister_all()
        except Exception:                               # noqa: BLE001
            pass
        sys.exit(0)

    from tessera.app import main

    sys.exit(main())
