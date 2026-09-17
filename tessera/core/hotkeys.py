"""System-wide shortcuts. Windows registers them itself; elsewhere nothing yet."""

from __future__ import annotations

import ctypes
import logging

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

from . import platform
from .config import HotkeysConfig

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 1, 2, 4, 8
#: Repeats are noise for a toggle.
MOD_NOREPEAT = 0x4000

_MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
    "shift": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN, "super": MOD_WIN,
}
_KEYS = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B,
    "escape": 0x1B, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "delete": 0x2E, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}


def parse(text: str) -> tuple[int, int] | None:
    """"Ctrl+Alt+P" -> (modifiers, virtual key), or None when it makes no sense."""
    parts = [part.strip().lower() for part in text.split("+") if part.strip()]
    if not parts:
        return None
    modifiers, key = 0, parts[-1]
    for part in parts[:-1]:
        if part not in _MODIFIERS:
            return None
        modifiers |= _MODIFIERS[part]
    if len(key) == 1:
        code = ord(key.upper())
    elif key in _KEYS:
        code = _KEYS[key]
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        code = 0x70 + int(key[1:]) - 1
    else:
        return None
    # A bare letter would fire on every keystroke; insist on a modifier.
    if not modifiers and len(key) == 1:
        return None
    return modifiers, code


class Hotkeys(QObject):
    """Registers the configured shortcuts and says which one fired."""

    #: "phone_audio" or "ring_phone".
    triggered = Signal(str)

    def __init__(self, config: HotkeysConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._ids: dict[int, str] = {}
        self._filter: _Filter | None = None
        if platform.REAL == "windows":
            self._filter = _Filter(self)
            app = QCoreApplication.instance()
            if app is not None:
                app.installNativeEventFilter(self._filter)

    @property
    def supported(self) -> bool:
        return self._filter is not None

    def apply(self) -> None:
        """Register what the settings say now, dropping what they no longer say."""
        if not self.supported:
            return
        self.clear()
        user32 = ctypes.windll.user32                       # type: ignore[attr-defined]
        for index, (action, text) in enumerate(
            (("phone_audio", self._config.phone_audio), ("ring_phone", self._config.ring_phone)),
            start=1,
        ):
            parsed = parse(text)
            if parsed is None:
                if text.strip():
                    log.warning("shortcut %r for %s is not understood", text, action)
                continue
            modifiers, key = parsed
            if user32.RegisterHotKey(None, index, modifiers | MOD_NOREPEAT, key):
                self._ids[index] = action
            else:
                log.warning("shortcut %s is held by another program", text)

    def clear(self) -> None:
        if not self.supported:
            return
        user32 = ctypes.windll.user32                       # type: ignore[attr-defined]
        for index in list(self._ids):
            user32.UnregisterHotKey(None, index)
        self._ids.clear()

    def _fired(self, index: int) -> None:
        action = self._ids.get(index)
        if action:
            self.triggered.emit(action)


class _Filter(QAbstractNativeEventFilter):
    """Watches the message loop for WM_HOTKEY."""

    def __init__(self, owner: Hotkeys) -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt naming
        if event_type == b"windows_generic_MSG":
            msg = _MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._owner._fired(int(msg.wParam))
        return False, 0


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint), ("pt", _POINT),
    ]
