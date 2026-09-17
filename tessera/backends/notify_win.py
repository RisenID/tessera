"""Windows toasts with a reply box, the same face as backends.notify.Notifier."""

from __future__ import annotations

import logging
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from PySide6.QtCore import QObject, Signal

from ..core import platform
from ..core.proc import submit

log = logging.getLogger(__name__)

REPLY = "inline-reply"
DISMISS = "dismiss"
OPEN = "default"

#: Toasts this app still holds, so a reply or a dismissal can be matched.
MAX_LIVE = 64


def _winrt():
    from winrt.windows.data.xml.dom import XmlDocument
    from winrt.windows.ui.notifications import (
        ToastActivatedEventArgs,
        ToastNotification,
        ToastNotificationManager,
    )

    return XmlDocument, ToastNotification, ToastNotificationManager, ToastActivatedEventArgs


class WindowsNotifier(QObject):
    """Action Center toasts, under the app id app.py registers."""

    replied = Signal(int, str)
    activated = Signal(int, str)
    closed = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._notifier = None
        self._next = 1
        #: id -> ToastNotification, kept so it can be hidden or replaced.
        self._live: dict[int, object] = {}
        try:
            _doc, _toast, manager, _args = _winrt()
            self._notifier = manager.create_toast_notifier_with_id(platform.APP_ID)
        except Exception as exc:                            # noqa: BLE001
            log.info("Windows toasts are not available: %s", exc)

    @property
    def available(self) -> bool:
        return self._notifier is not None

    @property
    def can_reply(self) -> bool:
        return self.available

    @property
    def can_act(self) -> bool:
        return self.available

    @property
    def capabilities(self) -> list[str]:
        return ["actions", "inline-reply"] if self.available else []

    # -- sending ---------------------------------------------------------------

    def send(
        self,
        summary: str,
        body: str,
        *,
        icon: str = "",
        replace: int = 0,
        repliable: bool = False,
        clearable: bool = True,
        reply_placeholder: str = "Reply",
        urgent: bool = False,
    ) -> int:
        if self._notifier is None:
            return 0
        document_class, toast_class, _manager, args_class = _winrt()

        given = replace if replace in self._live else self._next
        self._next = max(self._next, given + 1)
        old = self._live.pop(given, None)
        if old is not None:
            try:
                self._notifier.hide(old)
            except OSError:
                pass

        document = document_class()
        document.load_xml(self._xml(summary, body, icon, repliable, clearable, reply_placeholder, urgent))
        toast = toast_class(document)
        toast.tag = str(given)

        def on_activated(_sender, args) -> None:
            try:
                details = args_class._from(args)
                argument = str(details.arguments or "")
                if argument == REPLY:
                    from winrt.system import unbox_string

                    text = ""
                    inputs = details.user_input
                    if inputs is not None and inputs.has_key("reply"):
                        text = str(unbox_string(inputs.lookup("reply")))
                    self.replied.emit(given, text)
                else:
                    self.activated.emit(given, argument or OPEN)
            except Exception as exc:                        # noqa: BLE001
                log.debug("toast activation: %s", exc)

        def on_dismissed(_sender, _args) -> None:
            self._live.pop(given, None)
            self.closed.emit(given)

        toast.add_activated(on_activated)
        toast.add_dismissed(on_dismissed)
        try:
            self._notifier.show(toast)
        except OSError as exc:
            log.debug("toast refused: %s", exc)
            return 0
        self._live[given] = toast
        while len(self._live) > MAX_LIVE:
            self._live.pop(next(iter(self._live)))
        return given

    def send_async(self, summary: str, body: str, on_done=None, **options) -> None:
        if not self.available:
            if on_done is not None:
                on_done(0)
            return
        submit(lambda: self.send(summary, body, **options), on_done=on_done,
               on_error=lambda message: log.debug("toast: %s", message))

    def close(self, notification_id: int) -> None:
        toast = self._live.pop(notification_id, None)
        if toast is not None and self._notifier is not None:
            try:
                self._notifier.hide(toast)
            except OSError:
                pass

    @staticmethod
    def _xml(summary: str, body: str, icon: str, repliable: bool, clearable: bool,
             placeholder: str, urgent: bool) -> str:
        image = ""
        if icon and Path(icon).is_file():
            image = f'<image placement="appLogoOverride" hint-crop="circle" src={quoteattr(Path(icon).as_uri())}/>'
        actions = []
        if repliable:
            actions.append(
                f'<input id="reply" type="text" placeHolderContent={quoteattr(placeholder)}/>'
                f'<action content="Reply" arguments={quoteattr(REPLY)} hint-inputId="reply"/>'
            )
        if clearable:
            actions.append(f'<action content="Dismiss on phone" arguments={quoteattr(DISMISS)}/>')
        return (
            f'<toast scenario={quoteattr("urgent" if urgent else "default")} launch={quoteattr(OPEN)}>'
            f"<visual><binding template=\"ToastGeneric\">{image}"
            f"<text>{escape(summary)}</text><text>{escape(body)}</text>"
            "</binding></visual>"
            + (f"<actions>{''.join(actions)}</actions>" if actions else "")
            + "</toast>"
        )
