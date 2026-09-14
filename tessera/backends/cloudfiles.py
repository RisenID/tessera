"""The Cloud Files API (cldapi.dll), as much of it as the phone's storage needs.

Adapted from Sefirah's CloudFilter interop (https://github.com/shrimqy/Sefirah,
GPL-3.0) by shrimqy.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import functools
import os

LARGE = ctypes.c_longlong
WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

# CF_CALLBACK_TYPE
FETCH_DATA, CANCEL_FETCH_DATA, FETCH_PLACEHOLDERS = 0, 2, 3
DELETE_COMPLETION, RENAME_COMPLETION = 10, 12
CALLBACK_END = 0xFFFFFFFF

# CF_OPERATION_TYPE
TRANSFER_DATA, TRANSFER_PLACEHOLDERS = 0, 4

#: Process info, full paths, and no implicit hydration by this process.
CONNECT_FLAGS = 0x2 | 0x4 | 0x8
CREATE_IN_SYNC, CREATE_NO_ON_DEMAND = 0x2, 0x1
#: Stop on error, and the folder counts as populated afterwards.
TRANSFER_PLACEHOLDERS_FLAGS = 0x1 | 0x2
UPDATE_MARK_IN_SYNC = 0x2
CONVERT_MARK_IN_SYNC = 0x1
OPLOCK_EXCLUSIVE, OPLOCK_WRITE = 0x1, 0x2

STATE_PLACEHOLDER, STATE_IN_SYNC, STATE_PARTIALLY_ON_DISK = 0x1, 0x8, 0x20
STATE_INVALID = 0xFFFFFFFF

STATUS_SUCCESS = 0
STATUS_UNSUCCESSFUL = -0x3FFFFFFF           # 0xC0000001
ERROR_CLOUD_REQUEST_CANCELED = -0x7FF8FE72  # HRESULT_FROM_WIN32(398)

ATTR_DIRECTORY = 0x10
ATTR_OFFLINE = 0x1000
ATTR_NOT_INDEXED = 0x2000
ATTR_RECALL_ON_OPEN = 0x40000
ATTR_PINNED = 0x80000
ATTR_UNPINNED = 0x100000

FILE_READ_ATTRIBUTES, FILE_WRITE_DATA, FILE_LIST_DIRECTORY = 0x80, 0x2, 0x1
SHARE_ALL = 0x7
OPEN_EXISTING = 3
BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE = w.HANDLE(-1).value


class FILE_BASIC_INFO(ctypes.Structure):
    _fields_ = [("CreationTime", LARGE), ("LastAccessTime", LARGE),
                ("LastWriteTime", LARGE), ("ChangeTime", LARGE), ("FileAttributes", w.DWORD)]


class FS_METADATA(ctypes.Structure):
    _fields_ = [("BasicInfo", FILE_BASIC_INFO), ("FileSize", LARGE)]


class PLACEHOLDER_CREATE_INFO(ctypes.Structure):
    _fields_ = [("RelativeFileName", w.LPCWSTR), ("FsMetadata", FS_METADATA),
                ("FileIdentity", ctypes.c_void_p), ("FileIdentityLength", w.DWORD),
                ("Flags", w.DWORD), ("Result", ctypes.c_long), ("CreateUsn", LARGE)]


class CALLBACK_INFO(ctypes.Structure):
    _fields_ = [("StructSize", w.DWORD), ("ConnectionKey", LARGE), ("CallbackContext", ctypes.c_void_p),
                ("VolumeGuidName", w.LPCWSTR), ("VolumeDosName", w.LPCWSTR),
                ("VolumeSerialNumber", w.DWORD), ("SyncRootFileId", LARGE),
                ("SyncRootIdentity", ctypes.c_void_p), ("SyncRootIdentityLength", w.DWORD),
                ("FileId", LARGE), ("FileSize", LARGE), ("FileIdentity", ctypes.c_void_p),
                ("FileIdentityLength", w.DWORD), ("NormalizedPath", w.LPCWSTR),
                ("TransferKey", LARGE), ("PriorityHint", ctypes.c_ubyte),
                ("CorrelationVector", ctypes.c_void_p), ("ProcessInfo", ctypes.c_void_p),
                ("RequestKey", LARGE)]


class _FetchData(ctypes.Structure):
    _fields_ = [("Flags", w.DWORD), ("RequiredFileOffset", LARGE), ("RequiredLength", LARGE),
                ("OptionalFileOffset", LARGE), ("OptionalLength", LARGE),
                ("LastDehydrationTime", LARGE), ("LastDehydrationReason", w.DWORD)]


class _FetchPlaceholders(ctypes.Structure):
    _fields_ = [("Flags", w.DWORD), ("Pattern", w.LPCWSTR)]


class _RenameCompletion(ctypes.Structure):
    _fields_ = [("Flags", w.DWORD), ("SourcePath", w.LPCWSTR)]


class _CallbackUnion(ctypes.Union):
    _fields_ = [("FetchData", _FetchData), ("FetchPlaceholders", _FetchPlaceholders),
                ("RenameCompletion", _RenameCompletion)]


class CALLBACK_PARAMETERS(ctypes.Structure):
    _fields_ = [("ParamSize", ctypes.c_ulong), ("u", _CallbackUnion)]


class OPERATION_INFO(ctypes.Structure):
    _fields_ = [("StructSize", w.DWORD), ("Type", w.DWORD), ("ConnectionKey", LARGE),
                ("TransferKey", LARGE), ("CorrelationVector", ctypes.c_void_p),
                ("SyncStatus", ctypes.c_void_p), ("RequestKey", LARGE)]


class _TransferData(ctypes.Structure):
    _fields_ = [("Flags", w.DWORD), ("CompletionStatus", ctypes.c_long), ("Buffer", ctypes.c_void_p),
                ("Offset", LARGE), ("Length", LARGE)]


class _TransferPlaceholders(ctypes.Structure):
    _fields_ = [("Flags", w.DWORD), ("CompletionStatus", ctypes.c_long), ("PlaceholderTotalCount", LARGE),
                ("PlaceholderArray", ctypes.POINTER(PLACEHOLDER_CREATE_INFO)),
                ("PlaceholderCount", w.DWORD), ("EntriesProcessed", w.DWORD)]


class _RetrieveData(ctypes.Structure):
    """Unused; the union's largest member, so the size matches cfapi.h."""
    _fields_ = [("Flags", w.DWORD), ("Buffer", ctypes.c_void_p), ("Offset", LARGE),
                ("Length", LARGE), ("ReturnedLength", LARGE)]


class _OperationUnion(ctypes.Union):
    _fields_ = [("TransferData", _TransferData), ("RetrieveData", _RetrieveData),
                ("TransferPlaceholders", _TransferPlaceholders)]


class OPERATION_PARAMETERS(ctypes.Structure):
    _fields_ = [("ParamSize", ctypes.c_ulong), ("u", _OperationUnion)]


CALLBACK = WINFUNCTYPE(None, ctypes.POINTER(CALLBACK_INFO), ctypes.POINTER(CALLBACK_PARAMETERS))


class CALLBACK_REGISTRATION(ctypes.Structure):
    _fields_ = [("Type", w.DWORD), ("Callback", CALLBACK)]


class _AttributeTag(ctypes.Structure):
    _fields_ = [("FileAttributes", w.DWORD), ("ReparseTag", w.DWORD)]


@functools.cache
def _cld():
    dll = ctypes.WinDLL("cldapi")
    signatures = {
        "CfConnectSyncRoot": (w.LPCWSTR, ctypes.POINTER(CALLBACK_REGISTRATION), ctypes.c_void_p,
                              w.DWORD, ctypes.POINTER(LARGE)),
        "CfDisconnectSyncRoot": (LARGE,),
        "CfExecute": (ctypes.POINTER(OPERATION_INFO), ctypes.POINTER(OPERATION_PARAMETERS)),
        "CfCreatePlaceholders": (w.LPCWSTR, ctypes.POINTER(PLACEHOLDER_CREATE_INFO), w.DWORD,
                                 w.DWORD, ctypes.POINTER(w.DWORD)),
        "CfReportProviderProgress": (LARGE, LARGE, LARGE, LARGE),
        "CfConvertToPlaceholder": (w.HANDLE, ctypes.c_void_p, w.DWORD, w.DWORD,
                                   ctypes.POINTER(LARGE), ctypes.c_void_p),
        "CfUpdatePlaceholder": (w.HANDLE, ctypes.POINTER(FS_METADATA), ctypes.c_void_p, w.DWORD,
                                ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(LARGE), ctypes.c_void_p),
        "CfSetInSyncState": (w.HANDLE, w.DWORD, w.DWORD, ctypes.POINTER(LARGE)),
        "CfHydratePlaceholder": (w.HANDLE, LARGE, LARGE, w.DWORD, ctypes.c_void_p),
        "CfDehydratePlaceholder": (w.HANDLE, LARGE, LARGE, w.DWORD, ctypes.c_void_p),
        "CfOpenFileWithOplock": (w.LPCWSTR, w.DWORD, ctypes.POINTER(w.HANDLE)),
    }
    for name, args in signatures.items():
        function = getattr(dll, name)
        function.argtypes, function.restype = args, ctypes.c_long
    dll.CfGetWin32HandleFromProtectedHandle.argtypes = (w.HANDLE,)
    dll.CfGetWin32HandleFromProtectedHandle.restype = w.HANDLE
    dll.CfCloseHandle.argtypes, dll.CfCloseHandle.restype = (w.HANDLE,), None
    dll.CfGetPlaceholderStateFromAttributeTag.argtypes = (w.DWORD, w.DWORD)
    dll.CfGetPlaceholderStateFromAttributeTag.restype = w.DWORD
    return dll


@functools.cache
def kernel32():
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE)
    dll.CreateFileW.restype = w.HANDLE
    dll.CloseHandle.argtypes = (w.HANDLE,)
    dll.GetFileInformationByHandleEx.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD)
    dll.GetFileAttributesW.argtypes, dll.GetFileAttributesW.restype = (w.LPCWSTR,), w.DWORD
    dll.SetFileAttributesW.argtypes = (w.LPCWSTR, w.DWORD)
    dll.ReadDirectoryChangesW.argtypes = (w.HANDLE, ctypes.c_void_p, w.DWORD, w.BOOL, w.DWORD,
                                          ctypes.POINTER(w.DWORD), ctypes.c_void_p, ctypes.c_void_p)
    dll.CancelIoEx.argtypes = (w.HANDLE, ctypes.c_void_p)
    return dll


def check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} failed (0x{hr & 0xFFFFFFFF:08X})")


def filetime(unix: float) -> int:
    return int((unix + 11644473600) * 10_000_000) if unix else 0


def _long(path: str) -> str:
    return path if path.startswith("\\\\?\\") else "\\\\?\\" + os.path.abspath(path)


class Handle:
    """A file or folder handle that reads no data."""

    def __init__(self, path: str, access: int = 0):
        self.value = kernel32().CreateFileW(_long(path), access | FILE_READ_ATTRIBUTES, SHARE_ALL,
                                            None, OPEN_EXISTING, BACKUP_SEMANTICS, None)
        if self.value in (None, INVALID_HANDLE):
            raise ctypes.WinError(ctypes.get_last_error())

    def __enter__(self) -> w.HANDLE:
        return self.value

    def __exit__(self, *_exc) -> None:
        kernel32().CloseHandle(self.value)


def attributes(path: str) -> int:
    value = kernel32().GetFileAttributesW(_long(path))
    return 0 if value == 0xFFFFFFFF else value


def set_indexed(path: str) -> None:
    """A sync root must be indexed for Explorer to show file states."""
    value = attributes(path)
    if value & ATTR_NOT_INDEXED:
        kernel32().SetFileAttributesW(_long(path), value & ~ATTR_NOT_INDEXED)


def placeholder_state(path: str) -> int:
    try:
        with Handle(path) as handle:
            tag = _AttributeTag()
            if not kernel32().GetFileInformationByHandleEx(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
                return STATE_INVALID
            return _cld().CfGetPlaceholderStateFromAttributeTag(tag.FileAttributes, tag.ReparseTag)
    except OSError:
        return STATE_INVALID


def in_sync(path: str) -> bool:
    state = placeholder_state(path)
    return state != STATE_INVALID and bool(state & STATE_PLACEHOLDER) and bool(state & STATE_IN_SYNC)


def connect(root: str, registrations) -> int:
    key = LARGE()
    check(_cld().CfConnectSyncRoot(root, registrations, None, CONNECT_FLAGS, ctypes.byref(key)),
          "Connecting the sync root")
    return key.value


def disconnect(key: int) -> None:
    check(_cld().CfDisconnectSyncRoot(key), "Disconnecting the sync root")


def registrations(callbacks: dict[int, CALLBACK]):
    """CF_CALLBACK_REGISTRATION[], ended; keep it alive while connected."""
    array = (CALLBACK_REGISTRATION * (len(callbacks) + 1))()
    for index, (kind, callback) in enumerate(callbacks.items()):
        array[index].Type, array[index].Callback = kind, callback
    array[len(callbacks)].Type = CALLBACK_END
    return array


def _create_info(name: str, size: int, mtime: float, is_dir: bool, on_demand: bool) -> PLACEHOLDER_CREATE_INFO:
    info = PLACEHOLDER_CREATE_INFO()
    info.RelativeFileName = name
    stamp = filetime(mtime)
    basic = info.FsMetadata.BasicInfo
    basic.CreationTime = basic.LastWriteTime = basic.LastAccessTime = basic.ChangeTime = stamp
    basic.FileAttributes = ATTR_DIRECTORY if is_dir else 0
    info.FsMetadata.FileSize = 0 if is_dir else size
    # The identity is the name; paths come from the callbacks.
    identity = ctypes.create_unicode_buffer(name)
    info._identity = identity
    info.FileIdentity = ctypes.cast(identity, ctypes.c_void_p)
    info.FileIdentityLength = ctypes.sizeof(identity)
    info.Flags = CREATE_IN_SYNC | (0 if on_demand or not is_dir else CREATE_NO_ON_DEMAND)
    return info


def create_placeholder(parent: str, name: str, size: int, mtime: float, is_dir: bool) -> None:
    info = _create_info(name, size, mtime, is_dir, on_demand=True)
    done = w.DWORD()
    check(_cld().CfCreatePlaceholders(parent, ctypes.byref(info), 1, 0, ctypes.byref(done)),
          f"Creating a placeholder for {name}")


def _operation(info: CALLBACK_INFO, kind: int) -> OPERATION_INFO:
    op = OPERATION_INFO()
    op.StructSize, op.Type = ctypes.sizeof(OPERATION_INFO), kind
    op.ConnectionKey, op.TransferKey, op.RequestKey = info.ConnectionKey, info.TransferKey, info.RequestKey
    op.CorrelationVector = info.CorrelationVector
    return op


def _param_size(member) -> int:
    return OPERATION_PARAMETERS.u.offset + ctypes.sizeof(member)


def transfer_placeholders(info: CALLBACK_INFO, entries, ok: bool = True) -> None:
    """Answer FETCH_PLACEHOLDERS: (name, size, mtime, is_dir) per entry, one at a time."""
    op = _operation(info, TRANSFER_PLACEHOLDERS)
    if not entries or not ok:
        params = OPERATION_PARAMETERS()
        params.ParamSize = _param_size(_TransferPlaceholders)
        block = params.u.TransferPlaceholders
        block.Flags = TRANSFER_PLACEHOLDERS_FLAGS
        block.CompletionStatus = STATUS_SUCCESS if ok else STATUS_UNSUCCESSFUL
        check(_cld().CfExecute(ctypes.byref(op), ctypes.byref(params)), "Listing a folder")
        return
    # Sefirah found a whole array at once can corrupt placeholders.
    for processed, (name, size, mtime, is_dir) in enumerate(entries):
        create = _create_info(name, size, mtime, is_dir, on_demand=True)
        params = OPERATION_PARAMETERS()
        params.ParamSize = _param_size(_TransferPlaceholders)
        block = params.u.TransferPlaceholders
        block.Flags = TRANSFER_PLACEHOLDERS_FLAGS
        block.CompletionStatus = STATUS_SUCCESS
        block.PlaceholderArray = ctypes.pointer(create)
        block.PlaceholderCount = 1
        block.PlaceholderTotalCount = len(entries)
        block.EntriesProcessed = processed
        check(_cld().CfExecute(ctypes.byref(op), ctypes.byref(params)), f"Listing {name}")


def transfer_data(info: CALLBACK_INFO, data: bytes | None, offset: int, length: int) -> None:
    op = _operation(info, TRANSFER_DATA)
    params = OPERATION_PARAMETERS()
    params.ParamSize = _param_size(_TransferData)
    block = params.u.TransferData
    buffer = ctypes.create_string_buffer(data, len(data)) if data else None
    block.CompletionStatus = STATUS_SUCCESS if data is not None else STATUS_UNSUCCESSFUL
    block.Buffer = ctypes.cast(buffer, ctypes.c_void_p) if buffer is not None else None
    block.Offset, block.Length = offset, length
    hr = _cld().CfExecute(ctypes.byref(op), ctypes.byref(params))
    if hr != ERROR_CLOUD_REQUEST_CANCELED:
        check(hr, "Sending file contents")


def report_progress(info: CALLBACK_INFO, total: int, done: int) -> None:
    _cld().CfReportProviderProgress(info.ConnectionKey, info.TransferKey, total, done)


def mark_in_sync(path: str) -> None:
    """Make a local file or folder a placeholder that matches the phone."""
    with Handle(path, FILE_WRITE_DATA) as handle:
        if placeholder_state(path) & STATE_PLACEHOLDER:
            check(_cld().CfSetInSyncState(handle, 1, 0, None), "Marking in sync")
        else:
            check(_cld().CfConvertToPlaceholder(handle, None, 0, CONVERT_MARK_IN_SYNC, None, None),
                  "Converting to a placeholder")


def update_placeholder(path: str, size: int, mtime: float) -> None:
    """New size and time from the phone; the contents are fetched again on next open."""
    protected = w.HANDLE()
    check(_cld().CfOpenFileWithOplock(_long(path), OPLOCK_EXCLUSIVE | OPLOCK_WRITE, ctypes.byref(protected)),
          "Opening a placeholder")
    try:
        handle = _cld().CfGetWin32HandleFromProtectedHandle(protected)
        meta = FS_METADATA()
        stamp = filetime(mtime)
        basic = meta.BasicInfo
        basic.CreationTime = basic.LastWriteTime = basic.LastAccessTime = basic.ChangeTime = stamp
        meta.FileSize = size
        check(_cld().CfUpdatePlaceholder(handle, ctypes.byref(meta), None, 0, None, 0,
                                         UPDATE_MARK_IN_SYNC | 0x4, None, None),
              "Updating a placeholder")
    finally:
        _cld().CfCloseHandle(protected)


def hydrate(path: str) -> None:
    with Handle(path) as handle:
        check(_cld().CfHydratePlaceholder(handle, 0, -1, 0, None), "Downloading")


def dehydrate(path: str) -> None:
    protected = w.HANDLE()
    check(_cld().CfOpenFileWithOplock(_long(path), OPLOCK_EXCLUSIVE | OPLOCK_WRITE, ctypes.byref(protected)),
          "Opening a placeholder")
    try:
        handle = _cld().CfGetWin32HandleFromProtectedHandle(protected)
        check(_cld().CfDehydratePlaceholder(handle, 0, -1, 0, None), "Freeing up space")
    finally:
        _cld().CfCloseHandle(protected)
