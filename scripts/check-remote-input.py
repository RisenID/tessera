#!/usr/bin/env python3
"""The Linux remote-input backend: keystrokes are paced, ordered and balanced.

No real portal. _notify is captured, and the paced queue is drained under a
GLib main loop, so the check needs python3-gobject but no desktop session.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tessera.backends import input_linux as il  # noqa: E402

if not il.HAVE_GIO:
    print("python3-gobject is not installed; skipping (Linux desktop only)")
    sys.exit(0)

from gi.repository import GLib  # noqa: E402

FAILED = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILED
    if not ok:
        FAILED += 1
        print(f"FAIL {name}: {detail}")


def drive(actions) -> list[tuple[int, int]]:
    """Run *actions* against a fake-session PortalInput and return (keysym, state) sent."""
    sent: list[tuple[int, int]] = []
    portal = il.PortalInput()
    portal._session_path = "/fake/session"
    portal._connection = object()

    def fake_notify(method, signature, *arguments):
        if method == "NotifyKeyboardKeysym":
            sent.append((int(arguments[0]), int(arguments[1])))

    portal._notify = fake_notify

    loop = GLib.MainLoop()
    for act in actions:
        act(portal)

    def finish():
        loop.quit()
        return False

    # Long enough for the queue to drain at KEY_GAP_MS per event.
    GLib.timeout_add(400, finish)
    loop.run()
    portal.stop()
    return sent


def balanced(sent: list[tuple[int, int]]) -> bool:
    """Every press is followed by its own release before that key is pressed again."""
    down: set[int] = set()
    for code, state in sent:
        if state == 1:
            if code in down:
                return False        # pressed twice without releasing: a stuck key
            down.add(code)
        else:
            if code not in down:
                return False        # released without a press
            down.discard(code)
    return not down                 # nothing left held


def main() -> int:
    space = il.KEYSYMS["space"]

    # A phrase with the characters that stuck before: spaces and repeats.
    text = "the cat sat  on a mat"
    sent = drive([lambda p: p.text(text)])
    presses = [c for c, s in sent if s == 1]
    check("every character sent", len(presses) == len(text),
          f"{len(presses)} of {len(text)}")
    check("order preserved", presses == [il.keysym(c) for c in text],
          "keysyms out of order")
    check("balanced press/release", balanced(sent), "a key was left held or doubled")
    check("spaces intact", presses.count(space) == text.count(" "),
          f"{presses.count(space)} spaces of {text.count(' ')}")

    # Named keys go through the same queue and stay balanced with text around them.
    sent = drive([
        lambda p: p.text("hi"),
        lambda p: p.key("space"),
        lambda p: p.key("enter"),
        lambda p: p.text("bye"),
    ])
    check("mixed keys and text balanced", balanced(sent), "unbalanced with named keys")
    check("mixed keys all sent", len([1 for _, s in sent if s == 1]) == len("hibye") + 2,
          "a keystroke was dropped")

    # A double space, the classic stuck-repeat case.
    sent = drive([lambda p: p.text("a  b")])
    check("double space balanced", balanced(sent), "double space left a key held")
    check("double space both sent",
          [c for c, s in sent if s == 1].count(space) == 2, "a space was lost")

    if FAILED:
        print(f"{FAILED} remote-input checks failed")
        return 1
    print("all remote-input checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
