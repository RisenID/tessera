"""The phone's storage in File Explorer's navigation pane, through the Cloud Files API.

Adapted from Sefirah's Windows remote storage (https://github.com/shrimqy/Sefirah,
GPL-3.0) by shrimqy: a sync root per phone, placeholders from SFTP listings,
contents fetched when opened, and changes synced both ways.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import logging
import os
import queue
import shutil
import struct
import threading
import time
from pathlib import Path

from . import cloudfiles as cf
from .sftp_remote import SKIPPED_DIRS, Entry, Remote

log = logging.getLogger(__name__)

PROVIDER = "Tessera"
PHONE_ICON = r"%SystemRoot%\System32\imageres.dll,42"
#: How often the phone is looked at for changes, as Sefirah does, backing off
#: to the maximum while nothing changes and nobody is browsing. Every look is
#: an SFTP listing the phone has to wake up for, so idle means minutes.
WATCH_SECONDS = 2.0
WATCH_MAX_SECONDS = 180.0
#: How long a folder listing or file open counts as someone browsing.
ACTIVE_SECONDS = 60.0
RECONNECT_SECONDS = 5.0
#: TRANSFER_DATA lengths must be 4 KiB aligned, except at the end of a file.
CHUNK = 4096 * 4
#: Not sent to the phone.
LOCAL_JUNK = ("desktop.ini", "thumbs.db")

NOTIFY_FILTER = 0x1 | 0x2 | 0x4 | 0x10      # file name, dir name, attributes, last write
ACTION_ADDED, ACTION_MODIFIED, ACTION_RENAMED_NEW = 1, 3, 5


def supported() -> bool:
    try:
        from winrt.windows.storage.provider import StorageProviderSyncRootManager

        return bool(StorageProviderSyncRootManager.is_supported())
    except Exception:                                   # noqa: BLE001
        return False


def base_folder() -> Path:
    return Path.home() / "Tessera"


def user_sid() -> str:
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    advapi.OpenProcessToken.argtypes = (w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
    advapi.GetTokenInformation.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD,
                                           ctypes.POINTER(w.DWORD))
    advapi.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(w.LPWSTR))
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    token = w.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = w.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        text = w.LPWSTR()
        advapi.ConvertSidToStringSidW(ctypes.c_void_p.from_buffer(buffer).value, ctypes.byref(text))
        try:
            return text.value
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.CloseHandle(token)


def root_id(account: str) -> str:
    return f"{PROVIDER}!{user_sid()}!{account.replace('!', '')}"


def register(folder: Path, name: str, identity: str) -> None:
    """Put the folder in Explorer's navigation pane under the phone's name."""
    from winrt.windows.security.cryptography import BinaryStringEncoding, CryptographicBuffer
    from winrt.windows.storage import StorageFolder
    from winrt.windows.storage.provider import (
        StorageProviderHydrationPolicy,
        StorageProviderInSyncPolicy,
        StorageProviderPopulationPolicy,
        StorageProviderSyncRootInfo,
        StorageProviderSyncRootManager,
    )

    folder.mkdir(parents=True, exist_ok=True)
    cf.set_indexed(str(folder))
    info = StorageProviderSyncRootInfo()
    info.id = identity
    info.path = StorageFolder.get_folder_from_path_async(str(folder)).get()
    info.display_name_resource = name
    info.icon_resource = PHONE_ICON
    info.hydration_policy = StorageProviderHydrationPolicy.FULL
    info.population_policy = StorageProviderPopulationPolicy.FULL
    info.in_sync_policy = StorageProviderInSyncPolicy.DEFAULT
    info.show_siblings_as_group = False
    info.version = "1"
    info.context = CryptographicBuffer.convert_string_to_binary(identity, BinaryStringEncoding.UTF8)
    StorageProviderSyncRootManager.register(info)


def unregister(identity: str) -> None:
    from winrt.windows.storage.provider import StorageProviderSyncRootManager

    try:
        StorageProviderSyncRootManager.unregister(identity)
    except OSError as exc:
        log.debug("unregister %s: %s", identity, exc)


def unregister_all() -> None:
    """Every Tessera sync root of this user, for uninstalling."""
    from winrt.windows.storage.provider import StorageProviderSyncRootManager

    prefix = f"{PROVIDER}!{user_sid()}!"
    for info in StorageProviderSyncRootManager.get_current_sync_roots():
        if info.id.startswith(prefix):
            unregister(info.id)


class Provider:
    """Keeps one sync root and the phone's storage in step."""

    def __init__(self, remote: Remote, folder: Path):
        self.remote = remote
        self.folder = str(folder)
        self._key: int | None = None
        self._tasks: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._cancelled: dict[int, threading.Event] = {}
        self._threads: list[threading.Thread] = []
        self._watch_handle = None
        self._stopped = False
        self._active_until = 0.0
        self._known: dict[str, tuple[float, bool]] = {}
        self._callbacks = {
            cf.FETCH_PLACEHOLDERS: cf.CALLBACK(self._guard(self._on_fetch_placeholders)),
            cf.FETCH_DATA: cf.CALLBACK(self._guard(self._on_fetch_data)),
            cf.CANCEL_FETCH_DATA: cf.CALLBACK(self._guard(self._on_cancel_fetch_data)),
            cf.DELETE_COMPLETION: cf.CALLBACK(self._guard(self._on_delete_completion)),
            cf.RENAME_COMPLETION: cf.CALLBACK(self._guard(self._on_rename_completion)),
        }
        self._registrations = cf.registrations(self._callbacks)

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        if not self.remote.connected:
            self.remote.connect()
        self._key = cf.connect(self.folder, self._registrations)
        self._known = self._local_snapshot()
        for target in (self._run_tasks, self._watch_local, self._watch_remote):
            thread = threading.Thread(target=target, name=f"tessera-cloud-{target.__name__}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop.set()
        self._tasks.put(None)
        handle = self._watch_handle
        if handle is not None:
            cf.kernel32().CancelIoEx(handle, None)
        for event in list(self._cancelled.values()):
            event.set()
        for thread in self._threads:
            thread.join(timeout=5)
        if self._key is not None:
            try:
                cf.disconnect(self._key)
            except OSError as exc:
                log.debug("%s", exc)
            self._key = None
        self.remote.close()

    # -- paths -----------------------------------------------------------------

    def _relative(self, full: str) -> str:
        relative = os.path.relpath(full, self.folder).replace("\\", "/")
        return "" if relative == "." else relative

    def _local(self, relative: str) -> str:
        return os.path.join(self.folder, *relative.split("/")) if relative else self.folder

    def _full_path(self, info: cf.CALLBACK_INFO, normalized: str | None = None) -> str:
        return (info.VolumeDosName or "") + (normalized if normalized is not None else info.NormalizedPath)

    def _inside(self, full: str) -> bool:
        # commonpath, not a prefix: "Pixel" is not inside "Pixel 8".
        root = os.path.normcase(os.path.abspath(self.folder))
        try:
            return os.path.commonpath([root, os.path.normcase(os.path.abspath(full))]) == root
        except ValueError:                              # another drive
            return False

    def _busy(self) -> None:
        self._active_until = time.monotonic() + ACTIVE_SECONDS

    # -- callbacks, on cldapi's threads ----------------------------------------

    def _guard(self, handler):
        def call(info_pointer, params_pointer):
            try:
                handler(info_pointer.contents, params_pointer.contents)
            except Exception:                           # noqa: BLE001
                log.exception("cloud files callback failed")
        return call

    def _on_fetch_placeholders(self, info, _params) -> None:
        self._busy()
        relative = self._relative(self._full_path(info))
        try:
            entries = self.remote.list(relative)
        except Exception:                               # noqa: BLE001
            log.exception("listing %s", relative)
            cf.transfer_placeholders(info, [], ok=False)
            return
        cf.transfer_placeholders(info, [(e.name, e.size, e.mtime, e.is_dir) for e in entries])

    def _on_fetch_data(self, info, params) -> None:
        self._busy()
        fetch = params.u.FetchData
        offset, end = fetch.RequiredFileOffset, fetch.RequiredFileOffset + fetch.RequiredLength
        relative = self._relative(self._full_path(info))
        cancelled = self._cancelled.setdefault(info.TransferKey, threading.Event())
        try:
            reader = self.remote.reader()
            try:
                with reader.open(self.remote.path(relative), "rb") as remote_file:
                    remote_file.seek(offset)
                    while offset < end and not cancelled.is_set():
                        data = remote_file.read(min(CHUNK, end - offset))
                        if not data:
                            break
                        cf.transfer_data(info, data, offset, len(data))
                        offset += len(data)
                        cf.report_progress(info, info.FileSize, offset)
            finally:
                reader.close()
        except Exception:                               # noqa: BLE001
            log.exception("fetching %s", relative)
            cf.transfer_data(info, None, fetch.RequiredFileOffset, fetch.RequiredLength)
        finally:
            self._cancelled.pop(info.TransferKey, None)

    def _on_cancel_fetch_data(self, info, _params) -> None:
        event = self._cancelled.get(info.TransferKey)
        if event is not None:
            event.set()

    def _on_delete_completion(self, info, _params) -> None:
        full = self._full_path(info)
        self._tasks.put(lambda: self._deleted_here(full))

    def _on_rename_completion(self, info, params) -> None:
        old = self._full_path(info, params.u.RenameCompletion.SourcePath)
        new = self._full_path(info)
        self._tasks.put(lambda: self._renamed_here(old, new))

    # -- the work queue: one change at a time ----------------------------------

    def _run_tasks(self) -> None:
        while not self._stop.is_set():
            task = self._tasks.get()
            if task is None:
                return
            try:
                task()
            except Exception:                           # noqa: BLE001
                log.exception("sync task failed")

    def _deleted_here(self, full: str) -> None:
        # A file made here is sometimes not quite gone yet.
        for _ in range(20):
            if not os.path.exists(full):
                break
            time.sleep(0.25)
        else:
            return
        relative = self._relative(full)
        if self.remote.exists(relative):
            self.remote.remove(relative)
        self._known.pop(relative, None)

    def _renamed_here(self, old: str, new: str) -> None:
        old_relative = self._relative(old)
        if not self.remote.exists(old_relative):
            return
        if not self._inside(new):
            self.remote.remove(old_relative)
        else:
            self.remote.rename(old_relative, self._relative(new))
        self._known.pop(old_relative, None)

    def _changed_here(self, full: str) -> None:
        self._busy()
        name = os.path.basename(full)
        if not os.path.exists(full) or name.lower() in LOCAL_JUNK or name.startswith("~$"):
            return
        attributes = cf.attributes(full)
        is_dir = bool(attributes & cf.ATTR_DIRECTORY)
        state = cf.placeholder_state(full)
        if state != cf.STATE_INVALID and state & cf.STATE_PLACEHOLDER and state & cf.STATE_IN_SYNC:
            # Explorer's "Always keep on this device" and "Free up space".
            if not is_dir and attributes & cf.ATTR_PINNED and attributes & cf.ATTR_OFFLINE:
                cf.hydrate(full)
            elif (not is_dir and attributes & cf.ATTR_UNPINNED and not attributes & cf.ATTR_OFFLINE
                  and not state & cf.STATE_PARTIALLY_ON_DISK):
                cf.dehydrate(full)
            return
        self._upload(full, is_dir)

    def _upload(self, full: str, is_dir: bool) -> None:
        relative = self._relative(full)
        if is_dir:
            self.remote.mkdir(relative)
            cf.mark_in_sync(full)
            for child in os.listdir(full):
                path = os.path.join(full, child)
                self._upload(path, os.path.isdir(path))
            return
        remote = self.remote.stat(relative)
        mtime = os.path.getmtime(full)
        if remote is not None and mtime < remote.mtime:
            return
        for _ in range(20):                 # still being written by its app
            try:
                with open(full, "rb"):
                    break
            except PermissionError:
                time.sleep(0.25)
        self.remote.upload(full, relative, mtime)
        cf.mark_in_sync(full)
        self._known[relative] = (int(mtime), False)

    # -- this computer's side --------------------------------------------------

    def _watch_local(self) -> None:
        kernel = cf.kernel32()
        try:
            handle = cf.Handle(self.folder, cf.FILE_LIST_DIRECTORY)
        except OSError:
            log.exception("watching %s", self.folder)
            return
        self._watch_handle = handle.value
        buffer = ctypes.create_string_buffer(64 * 1024)
        returned = w.DWORD()
        with handle:
            while not self._stop.is_set():
                if not kernel.ReadDirectoryChangesW(handle.value, buffer, len(buffer), True, NOTIFY_FILTER,
                                                    ctypes.byref(returned), None, None):
                    self._watch_handle = None
                    return
                data = ctypes.string_at(buffer, returned.value)
                offset = 0
                while offset + 12 <= len(data):
                    following, action, length = struct.unpack_from("<III", data, offset)
                    name = data[offset + 12: offset + 12 + length].decode("utf-16-le")
                    if action in (ACTION_ADDED, ACTION_MODIFIED, ACTION_RENAMED_NEW):
                        full = os.path.join(self.folder, name)
                        self._tasks.put(lambda full=full: self._changed_here(full))
                    if not following:
                        break
                    offset += following

    # -- the phone's side ------------------------------------------------------

    def _hydrated(self, relative: str) -> bool:
        full = self._local(relative)
        return os.path.isdir(full) and not cf.attributes(full) & (cf.ATTR_OFFLINE | cf.ATTR_RECALL_ON_OPEN)

    def _local_snapshot(self) -> dict[str, tuple[float, bool]]:
        found: dict[str, tuple[float, bool]] = {}

        def walk(relative: str) -> None:
            for name in os.listdir(self._local(relative)):
                child = f"{relative}/{name}" if relative else name
                full = self._local(child)
                if not cf.in_sync(full):
                    continue
                is_dir = os.path.isdir(full)
                found[child] = (0 if is_dir else int(os.path.getmtime(full)), is_dir)
                if is_dir and self._hydrated(child):
                    walk(child)

        try:
            walk("")
        except OSError:
            pass
        return found

    def _remote_snapshot(self) -> dict[str, tuple[float, bool]]:
        found: dict[str, tuple[float, bool]] = {}
        pending = [""]
        while pending:
            relative = pending.pop()
            for entry in self.remote.list(relative):
                found[entry.relative] = (0 if entry.is_dir else int(entry.mtime), entry.is_dir)
                if entry.is_dir and entry.relative not in SKIPPED_DIRS and self._hydrated(entry.relative):
                    pending.append(entry.relative)
        return found

    def _watch_remote(self) -> None:
        interval = WATCH_SECONDS
        while not self._stop.wait(interval):
            try:
                if not self.remote.connected:
                    self.remote.connect()
                found = self._remote_snapshot()
            except Exception as exc:                    # noqa: BLE001
                log.info("phone storage unreachable: %s", exc)
                self._stop.wait(RECONNECT_SECONDS)
                continue
            known, self._known = self._known, found
            gone = sorted(set(known) - set(found), reverse=True)
            changed = [r for r in sorted(found) if r not in known or found[r][0] > known[r][0]]
            for relative in gone:
                self._tasks.put(lambda r=relative: self._deleted_there(r))
            for relative in changed:
                self._tasks.put(lambda r=relative: self._changed_there(r))
            if gone or changed or time.monotonic() < self._active_until:
                interval = WATCH_SECONDS
            else:
                interval = min(interval * 2, WATCH_MAX_SECONDS)

    def _deleted_there(self, relative: str) -> None:
        full = self._local(relative)
        if not os.path.exists(full) or not cf.in_sync(full) or self.remote.exists(relative):
            return
        try:
            if os.path.isdir(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
        except OSError as exc:
            # Open in an app, most likely: remembered, so the next look tries again.
            log.info("could not remove %s yet: %s", relative, exc)
            self._known[relative] = (0, os.path.isdir(full))

    def _changed_there(self, relative: str) -> None:
        entry: Entry | None = self.remote.stat(relative)
        full = self._local(relative)
        parent = os.path.dirname(full)
        if entry is None or not os.path.isdir(parent):
            return
        try:
            if not os.path.exists(full):
                cf.create_placeholder(parent, entry.name, entry.size, entry.mtime, entry.is_dir)
                return
            if entry.is_dir or not cf.in_sync(full):
                return                      # a local edit wins until it is uploaded
            if os.path.getsize(full) == entry.size and int(os.path.getmtime(full)) == int(entry.mtime):
                return
            cf.update_placeholder(full, entry.size, entry.mtime)
        except OSError as exc:
            # Open or pinned, most likely: forgotten, so the next look sees it as new again.
            log.info("could not update %s yet: %s", relative, exc)
            self._known.pop(relative, None)


#: Running providers, by folder.
_providers: dict[str, Provider] = {}
#: Mounts and unmounts arrive on pool threads, in either order.
_lock = threading.Lock()


def mount(remote: Remote, folder: Path, name: str, identity: str) -> Provider:
    with _lock:
        # A provider left from before a reconnect holds the sync root.
        old = _providers.pop(str(folder), None)
        if old is not None:
            old.stop()
        register(folder, name, identity)
        provider = Provider(remote, folder)
        provider.start()
        _providers[str(folder)] = provider
        return provider


def unmount(folder: Path, provider: Provider | None = None) -> None:
    """Stop *provider*, or whichever runs for *folder*; never a newer one."""
    with _lock:
        current = _providers.get(str(folder))
        if provider is None or current is provider:
            _providers.pop(str(folder), None)
            provider = current
        if provider is not None:
            provider.stop()
