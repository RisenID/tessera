"""Injecting pointer and keyboard input on Windows, through SendInput."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004

#: Special keys by the names the phone sends.
VIRTUAL_KEYS = {
    "enter": 0x0D, "backspace": 0x08, "tab": 0x09, "escape": 0x1B, "space": 0x20,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "delete": 0x2E,
    "f5": 0x74, "f11": 0x7A,
    "play": 0xB3, "next": 0xB0, "previous": 0xB1,
    "volumeup": 0xAF, "volumedown": 0xAE, "mute": 0xAD,
}
BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


def _send(*inputs: _INPUT) -> None:
    array = (_INPUT * len(inputs))(*inputs)
    ctypes.windll.user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT))   # type: ignore[attr-defined]


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> _INPUT:
    entry = _INPUT(type=INPUT_MOUSE)
    entry.u.mi = _MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, None)
    return entry


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    entry = _INPUT(type=INPUT_KEYBOARD)
    entry.u.ki = _KEYBDINPUT(vk, scan, flags, 0, None)
    return entry


class WindowsInput:
    """The same surface as the portal backend, for the dispatcher."""

    def available(self) -> bool:
        return True

    def start(self, on_ready=None) -> None:
        if on_ready is not None:
            on_ready(True, "")

    def stop(self) -> None:
        pass

    def move(self, dx: float, dy: float) -> None:
        _send(_mouse(MOUSEEVENTF_MOVE, int(round(dx)), int(round(dy))))

    def button(self, name: str, down: bool) -> None:
        flags = BUTTONS.get(name)
        if flags:
            _send(_mouse(flags[0] if down else flags[1]))

    def click(self, name: str = "left") -> None:
        flags = BUTTONS.get(name)
        if flags:
            _send(_mouse(flags[0]), _mouse(flags[1]))

    def scroll(self, dx: float, dy: float) -> None:
        # One wheel notch is 120; the phone sends pixels of finger travel.
        if dy:
            _send(_mouse(MOUSEEVENTF_WHEEL, data=int(-dy * 4)))
        if dx:
            _send(_mouse(MOUSEEVENTF_HWHEEL, data=int(dx * 4)))

    def key(self, name: str) -> None:
        vk = VIRTUAL_KEYS.get(name.lower())
        if vk is None:
            return
        _send(_key(vk=vk), _key(vk=vk, flags=KEYEVENTF_KEYUP))

    def text(self, text: str) -> None:
        events = []
        for char in text:
            if char == "\n":
                events += [_key(vk=0x0D), _key(vk=0x0D, flags=KEYEVENTF_KEYUP)]
                continue
            for code in _utf16(char):
                events += [_key(scan=code, flags=KEYEVENTF_UNICODE),
                           _key(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
        if events:
            _send(*events)


def _utf16(char: str) -> list[int]:
    raw = char.encode("utf-16-le")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
