"""`tessera <command>`: drive the running app from a terminal or a script."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

COMMANDS = (
    "status", "notify", "sms", "send", "open", "copy", "ring", "type", "photo",
    "mic", "timer", "alarm", "wifi", "show", "lock",
)


def is_command(argv: list[str]) -> bool:
    return len(argv) > 1 and argv[1] in COMMANDS


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="tessera",
        description="Talk to the running Tessera app. Without a command, starts it.",
    )
    sub = root.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="the phone: connection, battery, signal, ringer")
    status.add_argument("--json", action="store_true", help="machine-readable")

    notify = sub.add_parser("notify", help="show a notification on the phone")
    notify.add_argument("text", nargs="+")
    notify.add_argument("-t", "--title", default="", help="defaults to this computer's name")

    sms = sub.add_parser("sms", help="send a text message from the phone")
    sms.add_argument("number")
    sms.add_argument("text", nargs="+")

    send = sub.add_parser("send", help="send files to the phone")
    send.add_argument("files", nargs="+")

    open_ = sub.add_parser("open", help="open a link on the phone")
    open_.add_argument("url", nargs="?", default="", help="defaults to the link on the clipboard")

    copy = sub.add_parser("copy", help="put text on this computer's clipboard")
    copy.add_argument("text", nargs="+")

    sub.add_parser("ring", help="make the phone ring")

    type_ = sub.add_parser("type", help="type into whatever has focus on the phone")
    type_.add_argument("text", nargs="+")
    type_.add_argument("--enter", action="store_true", help="press Enter afterwards")

    photo = sub.add_parser("photo", help="take a photo with the phone's camera")
    photo.add_argument("path", nargs="?", default="", help="where to save it")
    photo.add_argument("--front", action="store_true", help="the front camera")
    photo.add_argument("--clipboard", action="store_true", help="also copy it to the clipboard")

    mic = sub.add_parser("mic", help="use the phone as this computer's microphone")
    mic.add_argument("state", choices=("on", "off"))

    timer = sub.add_parser("timer", help="start a timer on the phone")
    timer.add_argument("duration", help="e.g. 90s, 5m, 1h30m")
    timer.add_argument("label", nargs="*")

    alarm = sub.add_parser("alarm", help="set an alarm on the phone")
    alarm.add_argument("time", help="HH:MM")
    alarm.add_argument("label", nargs="*")

    wifi = sub.add_parser("wifi", help="send a saved Wi-Fi network to the phone")
    wifi.add_argument("ssid", nargs="?", default="", help="defaults to the one in use")

    sub.add_parser("show", help="bring the window up")
    sub.add_parser("lock", help="lock this computer now, the way presence lock would")
    return root


def parse_duration(text: str) -> int:
    """"90", "90s", "5m", "1h30m" -> seconds; 0 when it makes no sense."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([hms])", text):
        total += int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total if re.fullmatch(r"(\d+\s*[hms]\s*)+", text) else 0


def build_request(args: argparse.Namespace) -> dict:
    """The IPC request for parsed arguments."""
    command = args.command
    if command == "status":
        return {"cmd": "status"}
    if command == "notify":
        return {"cmd": "notify", "title": args.title, "text": " ".join(args.text)}
    if command == "sms":
        return {"cmd": "sms", "number": args.number, "text": " ".join(args.text)}
    if command == "send":
        return {"cmd": "send", "files": [os.path.abspath(path) for path in args.files]}
    if command == "open":
        return {"cmd": "open", "url": args.url}
    if command == "copy":
        return {"cmd": "copy", "text": " ".join(args.text)}
    if command == "type":
        return {"cmd": "type", "text": " ".join(args.text), "enter": bool(args.enter)}
    if command == "photo":
        return {
            "cmd": "photo", "path": os.path.abspath(args.path) if args.path else "",
            "facing": "front" if args.front else "back", "clipboard": bool(args.clipboard),
        }
    if command == "mic":
        return {"cmd": "mic", "on": args.state == "on"}
    if command == "timer":
        return {"cmd": "timer", "seconds": parse_duration(args.duration),
                "label": " ".join(args.label)}
    if command == "alarm":
        return {"cmd": "alarm", "time": args.time, "label": " ".join(args.label)}
    if command == "wifi":
        return {"cmd": "wifi", "ssid": args.ssid}
    return {"cmd": command}


def format_status(answer: dict) -> str:
    """One readable block for a terminal."""
    lines = [f"{answer.get('phone') or 'No phone'}: "
             + ("connected" if answer.get("connected") else "offline")]
    battery = answer.get("battery") or {}
    if isinstance(battery.get("level"), int) and battery["level"] >= 0:
        lines.append(f"battery  {battery['level']}%"
                     + (" charging" if battery.get("charging") else ""))
    for key, label in (("wifi", "wi-fi"), ("cell", "cellular")):
        reading = answer.get(key) or {}
        if isinstance(reading.get("level"), int):
            extra = reading.get("type") or reading.get("operator") or ""
            lines.append(f"{label:<8} {reading['level']}/{reading.get('max', 4)} {extra}".rstrip())
    if answer.get("ringer"):
        lines.append(f"ringer   {answer['ringer']}")
    media = answer.get("media") or {}
    if media.get("title"):
        state = "playing" if media.get("playing") else "paused"
        lines.append(f"media    {media['title']} — {media.get('artist', '')} ({state})".rstrip())
    if answer.get("code"):
        lines.append(f"code     {answer['code']} from {answer.get('code_from', '')}".rstrip())
    if answer.get("presence"):
        lines.append(f"presence {answer['presence']}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    if args.command == "timer" and not build_request(args)["seconds"]:
        print("tessera timer: give a duration like 90s, 5m or 1h30m", file=sys.stderr)
        return 2

    # QLocalSocket wants an application object, even for blocking calls. A
    # windowed build may need a dialog later, which wants the GUI one.
    if sys.stderr is None:
        from PySide6.QtWidgets import QApplication as _App
    else:
        from PySide6.QtCore import QCoreApplication as _App

    from .core import ipc

    _app = _App.instance() or _App([])
    answer = ipc.call(build_request(args), timeout_ms=60_000)
    if args.command == "status" and answer.get("ok"):
        print(json.dumps(answer, indent=2) if args.json else format_status(answer))
        return 0
    if args.command == "status" and args.json:
        print(json.dumps(answer))
        return 1
    message = answer.get("message", "")
    if message and sys.stderr is None:
        # A windowed build (Windows' Send to): a dialog is the only output.
        if answer.get("ok"):
            return 0
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(None, "Tessera", message)
        return 1
    if message:
        print(message, file=sys.stdout if answer.get("ok") else sys.stderr)
    return 0 if answer.get("ok") else 1
