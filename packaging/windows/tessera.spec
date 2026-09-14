# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows build.

    pyinstaller packaging/windows/tessera.spec --noconfirm

One folder rather than one file: Qt starts faster when its plugins are on disk,
and an installer wants a directory anyway. The result is dist/Tessera/.

The app uses six Qt modules -- Core, Gui, Widgets, Network (the TLS link to
the phone), Svg (the icons it draws itself) and Multimedia (the phone's audio).
Everything else Qt ships is excluded, which is most of its size.
"""

import sys
from pathlib import Path

spec_dir = Path(SPECPATH).resolve()
root = spec_dir.parents[1]
sys.path.insert(0, str(root))

icon = spec_dir / "tessera.ico"

# Qt modules we do not use. Excluding them takes the build from roughly
# 300 MB to under 100: WebEngine alone is half of it.
EXCLUDED_QT = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDBus", "PySide6.QtDesigner", "PySide6.QtHelp",
    # Not QtMultimedia: the phone's audio over the link plays through
    # QAudioSink, on Windows as everywhere else.
    "PySide6.QtLocation", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPositioning",
    "PySide6.QtPrintSupport", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSerialBus", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
    "PySide6.QtSql", "PySide6.QtStateMachine", "PySide6.QtTest",
    "PySide6.QtTextToSpeech", "PySide6.QtUiTools", "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets", "PySide6.QtXml",
]

analysis = Analysis(
    [str(spec_dir / "launch.py")],
    pathex=[str(root)],
    # The virtual camera, built by native/win-camera/build.ps1.
    binaries=[
        (str(root / "build" / "native" / "TesseraCamera.dll"), "."),
        (str(root / "build" / "native" / "tessera-camera.exe"), "."),
    ],
    # The app icon, the same SVG Linux installs.
    datas=[(str(root / "packaging" / "icons" / "dev.tessera.Tessera.svg"), "icons")],
    # Imported inside functions, where the analysis cannot see them.
    hiddenimports=[
        "PySide6.QtSvg",
        "winrt.runtime",
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
        "winrt.windows.devices.enumeration",
        "winrt.windows.media.audio",
        "winrt.windows.applicationmodel.calls",
        # The phone's storage in File Explorer.
        "winrt.windows.storage",
        "winrt.windows.storage.provider",
        "winrt.windows.storage.streams",
        "winrt.windows.security.cryptography",
        "paramiko",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[*EXCLUDED_QT, "tkinter", "test", "unittest", "pydoc_data"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Tessera",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windowed: the app has a tray icon and a window, and a console would
    # flash up behind both.
    console=False,
    icon=str(icon) if icon.is_file() else None,
    version=None,
)

COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Tessera",
)
