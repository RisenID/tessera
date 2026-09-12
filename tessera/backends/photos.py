"""Browse the phone's photo library over adb.

Files are listed through MediaStore, which gives a date-sorted index without
walking the filesystem. Images are fetched lazily -- a modern phone camera
produces multi-megabyte files, so pulling a whole album up front would be slow
and pointless. Each fetched file is cached, and a downscaled thumbnail is
cached alongside it.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from ..core.config import state_dir
from ..core.proc import run
from . import adb

log = logging.getLogger(__name__)

IMAGES_URI = "content://media/external/images/media"
VIDEOS_URI = "content://media/external/video/media"

PROJECTION = [
    "_id", "_data", "date_added", "datetaken", "_size", "mime_type",
    "bucket_display_name",
]

THUMB_SIZE = 320


def cache_root() -> Path:
    path = state_dir() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class MediaItem:
    id: str
    remote_path: str
    taken: datetime | None
    size: int
    mime: str
    album: str
    is_video: bool = False

    @property
    def filename(self) -> str:
        return self.remote_path.rsplit("/", 1)[-1]

    @property
    def date_text(self) -> str:
        return self.taken.strftime("%d %b %Y, %H:%M") if self.taken else "Unknown date"

    @property
    def size_text(self) -> str:
        megabytes = self.size / (1024 * 1024)
        if megabytes >= 1:
            return f"{megabytes:.1f} MB"
        return f"{max(self.size // 1024, 1)} KB"

    @property
    def cache_key(self) -> str:
        """Filesystem-safe name that is stable across listings."""
        stem = re.sub(r"[^A-Za-z0-9_.-]", "_", self.filename)
        return f"{self.id}_{stem}"

    @property
    def local_path(self) -> Path:
        return cache_root() / "full" / self.cache_key

    @property
    def thumb_path(self) -> Path:
        return cache_root() / "thumbs" / f"{self.cache_key}.jpg"

    @property
    def cached(self) -> bool:
        return self.local_path.exists() and self.local_path.stat().st_size > 0


def _to_datetime(row: dict[str, str]) -> datetime | None:
    """MediaStore reports datetaken in ms and date_added in seconds."""
    raw = row.get("datetaken", "")
    if raw and raw.isdigit() and int(raw) > 0:
        try:
            return datetime.fromtimestamp(int(raw) / 1000)
        except (OverflowError, OSError, ValueError):
            pass
    raw = row.get("date_added", "")
    if raw and raw.isdigit() and int(raw) > 0:
        try:
            return datetime.fromtimestamp(int(raw))
        except (OverflowError, OSError, ValueError):
            pass
    return None


def list_media(serial: str, limit: int = 300, include_videos: bool = True) -> list[MediaItem]:
    """Most recent items first. Blocking; run in a worker."""
    items: list[MediaItem] = []
    sources = [(IMAGES_URI, False)]
    if include_videos:
        sources.append((VIDEOS_URI, True))

    for uri, is_video in sources:
        try:
            rows = adb.content_query(
                serial, uri, PROJECTION, sort="date_added DESC", limit=limit, timeout=45.0
            )
        except adb.AdbError as exc:
            log.warning("could not list %s: %s", uri, exc)
            continue
        for row in rows:
            path = row.get("_data", "")
            if not path:
                continue
            try:
                size = int(row.get("_size", "0") or 0)
            except ValueError:
                size = 0
            items.append(
                MediaItem(
                    id=row.get("_id", path),
                    remote_path=path,
                    taken=_to_datetime(row),
                    size=size,
                    mime=row.get("mime_type", ""),
                    album=row.get("bucket_display_name", ""),
                    is_video=is_video,
                )
            )

    # MediaStore's own sort is per-query; re-sort once the sources are merged.
    items.sort(key=lambda i: i.taken or datetime.min, reverse=True)
    return items[:limit]


def albums(items: list[MediaItem]) -> list[str]:
    names = {item.album for item in items if item.album}
    return sorted(names, key=str.lower)


def fetch(serial: str, item: MediaItem, timeout: float = 120.0) -> Path:
    """Pull *item* to the local cache, returning its path."""
    target = item.local_path
    if item.cached:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    result = run(
        ["adb", "-s", serial, "pull", item.remote_path, str(partial)], timeout=timeout
    )
    if not result.ok or not partial.exists():
        partial.unlink(missing_ok=True)
        raise adb.AdbError(f"could not copy {item.filename}: {result.text}")
    partial.replace(target)
    return target


def thumbnail(serial: str, item: MediaItem, size: int = THUMB_SIZE) -> Path:
    """Return a cached thumbnail, generating it (and pulling the file) if needed.

    Videos have no still to scale without decoding, so they get no thumbnail and
    the UI shows a placeholder instead.
    """
    thumb = item.thumb_path
    if thumb.exists() and thumb.stat().st_size > 0:
        return thumb
    if item.is_video:
        raise adb.AdbError("video thumbnails are not generated")

    source = fetch(serial, item)
    image = QImage(str(source))
    if image.isNull():
        raise adb.AdbError(f"{item.filename} is not a readable image")

    thumb.parent.mkdir(parents=True, exist_ok=True)
    # Fill the square tile and let the view crop, rather than letterboxing.
    scaled = image.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    if not scaled.save(str(thumb), "JPEG", 85):
        raise adb.AdbError(f"could not write a thumbnail for {item.filename}")
    return thumb


def save_to(item: MediaItem, destination: Path) -> Path:
    """Copy an already-fetched item out of the cache to *destination*."""
    if not item.cached:
        raise adb.AdbError(f"{item.filename} has not been downloaded yet")
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = destination
    counter = 1
    while target.exists():
        target = destination.with_name(f"{destination.stem} ({counter}){destination.suffix}")
        counter += 1
    shutil.copy2(item.local_path, target)
    return target


def cache_size() -> int:
    total = 0
    for path in cache_root().rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def clear_cache() -> None:
    shutil.rmtree(cache_root(), ignore_errors=True)
