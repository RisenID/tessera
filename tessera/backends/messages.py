"""SMS/MMS conversations."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from ..core.proc import run
from . import adb

log = logging.getLogger(__name__)

SMS_URI = "content://sms"
CONTACTS_URI = "content://com.android.contacts/data/phones"

SMS_PROJECTION = ["_id", "thread_id", "address", "date", "body", "type", "read"]

TYPE_INBOX = "1"
TYPE_SENT = "2"


def normalise_number(raw: str) -> str:
    """Reduce a phone number to a comparable key."""
    digits = re.sub(r"\D", "", raw or "")
    return digits[-9:] if len(digits) > 9 else digits


@dataclass(frozen=True)
class Message:
    id: str
    thread_id: str
    address: str
    body: str
    when: datetime | None
    outgoing: bool
    read: bool = True

    @property
    def time_text(self) -> str:
        if self.when is None:
            return ""
        now = datetime.now()
        if self.when.date() == now.date():
            return self.when.strftime("%H:%M")
        if self.when.year == now.year:
            return self.when.strftime("%d %b, %H:%M")
        return self.when.strftime("%d %b %Y")


@dataclass
class Conversation:
    thread_id: str
    address: str
    name: str = ""
    messages: list[Message] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.name or self.address or "Unknown"

    @property
    def latest(self) -> Message | None:
        return self.messages[-1] if self.messages else None

    @property
    def preview(self) -> str:
        latest = self.latest
        if latest is None:
            return ""
        body = " ".join(latest.body.split())
        prefix = "You: " if latest.outgoing else ""
        return f"{prefix}{body}"

    @property
    def unread(self) -> int:
        return sum(1 for m in self.messages if not m.read and not m.outgoing)

    @property
    def sort_key(self) -> datetime:
        latest = self.latest
        return latest.when if latest and latest.when else datetime.min


def _to_datetime(raw: str) -> datetime | None:
    """Telephony stores timestamps as epoch milliseconds."""
    if not raw or not raw.lstrip("-").isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000)
    except (OverflowError, OSError, ValueError):
        return None


def load_contacts(serial: str) -> dict[str, str]:
    """Map normalised phone numbers to contact names."""
    try:
        rows = adb.content_query(
            serial, CONTACTS_URI, ["display_name", "data1"], timeout=40.0
        )
    except adb.AdbError as exc:
        log.info("could not read contacts: %s", exc)
        return {}

    book: dict[str, str] = {}
    for row in rows:
        name = row.get("display_name", "").strip()
        key = normalise_number(row.get("data1", ""))
        if name and key:
            book.setdefault(key, name)
    return book


def list_messages(serial: str, limit: int = 500) -> list[Message]:
    """Most recent messages first. Blocking; run in a worker."""
    rows = adb.content_query(
        serial, SMS_URI, SMS_PROJECTION, sort="date DESC", limit=limit, timeout=60.0
    )
    messages: list[Message] = []
    for row in rows:
        body = row.get("body", "")
        if body == "null":
            body = ""
        messages.append(
            Message(
                id=row.get("_id", ""),
                thread_id=row.get("thread_id", ""),
                address=row.get("address", ""),
                body=body,
                when=_to_datetime(row.get("date", "")),
                outgoing=row.get("type", "") == TYPE_SENT,
                read=row.get("read", "1") != "0",
            )
        )
    return messages


def conversations(serial: str, limit: int = 500) -> list[Conversation]:
    """Group recent messages into threads, newest thread first."""
    messages = list_messages(serial, limit=limit)
    contacts = load_contacts(serial)

    threads: dict[str, Conversation] = {}
    for message in messages:
        key = message.thread_id or normalise_number(message.address) or message.address
        conversation = threads.get(key)
        if conversation is None:
            conversation = Conversation(
                thread_id=key,
                address=message.address,
                name=contacts.get(normalise_number(message.address), ""),
            )
            threads[key] = conversation
        conversation.messages.append(message)

    for conversation in threads.values():
        # list_messages returns newest first; a transcript reads oldest first.
        conversation.messages.reverse()

    return sorted(threads.values(), key=lambda c: c.sort_key, reverse=True)


def compose_on_phone(serial: str, address: str, body: str) -> None:
    """Open the phone's messaging app with the message pre-filled."""
    escaped = body.replace("\\", "\\\\").replace('"', '\\"')
    ok, out = adb.try_shell(
        serial,
        f'am start -a android.intent.action.SENDTO -d "sms:{address}" --es sms_body "{escaped}"',
        timeout=20.0,
    )
    if not ok or "error" in out.lower():
        raise adb.AdbError(f"could not open the messaging app: {out}")
