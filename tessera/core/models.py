"""Source-neutral data the UI renders."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Notification:
    id: str
    app: str = ""
    package: str = ""
    title: str = ""
    text: str = ""
    when: float = field(default_factory=time.time)
    repliable: bool = False
    clearable: bool = True
    ongoing: bool = False
    #: A text message arriving, as judged by the phone. See is_text_message.
    sms: bool = False

    @classmethod
    def from_companion(cls, message: dict[str, Any]) -> "Notification":
        # The phone sends epoch milliseconds.
        raw_time = message.get("time", 0) or 0
        return cls(
            id=str(message.get("id", "")),
            app=str(message.get("app", "")),
            package=str(message.get("package", "")),
            title=str(message.get("title", "")),
            text=str(message.get("text", "")),
            when=(raw_time / 1000) if raw_time else time.time(),
            repliable=bool(message.get("repliable")),
            clearable=bool(message.get("clearable", True)),
            ongoing=bool(message.get("ongoing")),
            sms=bool(message.get("sms")),
        )

    @classmethod
    def from_kdeconnect(cls, note: Any) -> "Notification":
        return cls(
            id=note.id,
            app=note.app_name,
            title=note.title,
            text=note.text,
            when=note.received_at,
            repliable=bool(note.reply_id),
            clearable=bool(note.dismissable),
        )

    #: Packages that are somebody's default SMS app somewhere.
    SMS_PACKAGES = (
        "com.google.android.apps.messaging",
        "com.samsung.android.messaging",
        "com.android.messaging",
        "com.android.mms",
        "org.thoughtcrime.securesms",
    )
    SMS_APP_NAMES = ("messages", "messaging", "sms")

    @property
    def is_text_message(self) -> bool:
        """Whether this notification is a text message arriving."""
        if self.sms:
            return True
        if self.package:
            return self.package in self.SMS_PACKAGES
        return self.app.strip().lower() in self.SMS_APP_NAMES

    @property
    def body(self) -> str:
        """Everything a passcode might be hiding in."""
        return " ".join(part for part in (self.title, self.text) if part)

    @property
    def summary_line(self) -> str:
        """One line for a compact list: the title, then the body if it fits."""
        parts = [part for part in (self.title, " ".join(self.text.split())) if part]
        line = " — ".join(parts) if len(parts) > 1 else (parts[0] if parts else "")
        return line[:64]

    @property
    def time_text(self) -> str:
        moment = datetime.fromtimestamp(self.when)
        now = datetime.now()
        delta = (now - moment).total_seconds()
        if delta < 60:
            return "just now"
        if delta < 3600:
            return f"{int(delta // 60)} min ago"
        if moment.date() == now.date():
            return moment.strftime("%H:%M")
        return moment.strftime("%d %b, %H:%M")
