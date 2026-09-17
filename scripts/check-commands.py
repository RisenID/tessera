#!/usr/bin/env python3
"""Checks the command socket and the tessera command."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sandbox import isolate                                          # noqa: E402

root = isolate()
# The socket must not be the real app's.
os.environ["XDG_RUNTIME_DIR"] = root

from PySide6.QtCore import QEventLoop, QTimer                      # noqa: E402
from PySide6.QtWidgets import QApplication                         # noqa: E402

from tessera import cli                                            # noqa: E402
from tessera.core import ipc                                       # noqa: E402
from tessera.core.commands import Commands, looks_like_url         # noqa: E402
from tessera.core.config import Config                             # noqa: E402
from tessera.core.hub import Hub                                   # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


def same(label: str, produced: object, wanted: object) -> None:
    check(label, produced == wanted, f"{produced!r}")


def parsing() -> None:
    same("a plain number is seconds", cli.parse_duration("90"), 90)
    same("minutes and hours add up", cli.parse_duration("1h30m"), 5400)
    same("nonsense is zero", cli.parse_duration("soon"), 0)
    args = cli.parser().parse_args(["notify", "build", "done", "-t", "CI"])
    same("notify joins its words", cli.build_request(args),
         {"cmd": "notify", "title": "CI", "text": "build done"})
    args = cli.parser().parse_args(["timer", "5m", "tea"])
    same("a timer carries seconds and a label", cli.build_request(args),
         {"cmd": "timer", "seconds": 300, "label": "tea"})
    check("a web link is a link", looks_like_url("https://example.org/a?b=1"))
    check("a sentence is not", not looks_like_url("see https://example.org later"))
    check("tessera with no command starts the app", not cli.is_command(["tessera"]))
    check("tessera status is a command", cli.is_command(["tessera", "status"]))


def socket_round_trip() -> None:
    """A request through the socket, in the same process, on the event loop."""
    hub = Hub(Config())
    server = ipc.IpcServer()
    Commands(hub).register(server)
    check("the server listens", server.listen(), ipc.socket_name())

    # A real client: another process, as the tessera command is.
    import json
    import subprocess

    requests = [
        {"cmd": "ping"}, {"cmd": "status"}, {"cmd": "notify", "text": "hello"},
        {"cmd": "nonsense"}, {"cmd": "copy", "text": "from the socket"},
    ]
    code = (
        "import json, sys; sys.path.insert(0, sys.argv[1]);"
        "from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]);"
        "from tessera.core import ipc;"
        "[print(json.dumps(ipc.call(r, 5000))) for r in json.loads(sys.argv[2])];"
        "print(json.dumps({'running': ipc.running()}))"
    )
    client = subprocess.Popen(
        [sys.executable, "-c", code, str(Path(__file__).resolve().parents[1]), json.dumps(requests)],
        stdout=subprocess.PIPE, text=True, env=os.environ,
    )
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if client.poll() is not None else None)
    poll.start(30)
    QTimer.singleShot(15_000, loop.quit)
    loop.exec()
    if client.poll() is None:
        client.kill()
    answers = [json.loads(line) for line in (client.stdout.read() if client.stdout else "").splitlines()]
    check("every request was answered", len(answers) == len(requests) + 1, str(len(answers)))
    if len(answers) < len(requests) + 1:
        return
    check("ping answers", answers[0].get("ok") is True, str(answers[0]))
    check("status answers without a phone", answers[1].get("ok") is True
          and answers[1].get("connected") is False, str(answers[1]))
    check("a phone command without a phone says so", answers[2].get("ok") is False
          and "not connected" in answers[2].get("message", ""), str(answers[2]))
    check("an unknown command is refused", "unknown" in answers[3].get("message", ""))
    from PySide6.QtGui import QGuiApplication

    check("copy lands on the clipboard", QGuiApplication.clipboard().text() == "from the socket")
    check("the client sees the app as running", answers[5].get("running") is True)
    print("     " + cli.format_status(answers[1]).replace("\n", "\n     "))
    server.close()
    check("closing frees the socket", not os.path.exists(ipc.socket_name()))


def main() -> int:
    app = QApplication(sys.argv)
    print("-- parsing")
    parsing()
    print("\n-- the socket")
    socket_round_trip()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("all command checks passed")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
