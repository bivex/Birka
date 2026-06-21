from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from birka.application.media_ports import FileScanner, MetadataReader
from birka.application.user_metadata import UserMetadata, UserMetadataStore
from birka.domain.media import (
    AudioItem,
    AudioMetadata,
    MediaItem,
    MidiItem,
    MidiMetadata,
)


class LoadLibrary:
    """Scan a root, read metadata per file, merge user metadata.

    Optionally backed by a persistent ScanCache: when a cache is supplied,
    a file whose (mtime, size) match a cached entry is rebuilt from the
    cache without opening or parsing the file. Files that miss the cache
    are parsed in parallel via a ThreadPoolExecutor (I/O-bound work), and
    the freshly parsed metadata is written back to the cache so subsequent
    refreshes (incl. the 10s auto-refresh) are near-instant for unchanged
    files.
    """

    def __init__(
        self,
        scanner: FileScanner,
        reader: MetadataReader,
        metadata_store: UserMetadataStore,
        cache: Optional[Any] = None,
        max_workers: Optional[int] = None,
    ) -> None:
        self._scanner = scanner
        self._reader = reader
        self._metadata_store = metadata_store
        self._cache = cache
        # Default workers: cap at 8 — file parsing is fast and I/O is the
        # bottleneck, so more threads just add contention with SQLite.
        self._max_workers = max_workers or min(8, os.cpu_count() or 4)

    def execute(self, root: Path) -> List[MediaItem]:
        paths = list(self._scanner.scan(root))

        if self._cache is None:
            # Legacy fast path: no cache, serial read (keeps existing tests
            # and behavior identical when no cache is injected).
            items = [self._reader.read(path) for path in paths]
        else:
            items = self._scan_with_cache(paths)

        # Drop stale cache entries (files removed from disk since last scan)
        # so the cache doesn't grow unbounded across refreshes.
        if self._cache is not None:
            try:
                self._cache.prune({str(p) for p in paths})
            except Exception:
                # Prune is best-effort; never let cache maintenance break a scan.
                pass

        user_meta = self._metadata_store.load_all()
        return _apply_user_metadata(items, user_meta)

    def _scan_with_cache(self, paths: List[Path]) -> List[MediaItem]:
        # Each path is resolved independently and is I/O-bound, so dispatch
        # across a thread pool. Cache hits do only an os.stat + SQLite
        # primary-key lookup; cache misses do a full parse + cache write.
        # SQLite is opened with check_same_thread=False + WAL, so concurrent
        # reads are safe; writes are serialized by ScanCache's internal lock.
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            results = list(pool.map(self._read_one_cached, paths))
        return results

    def _read_one_cached(self, path: Path) -> MediaItem:
        path_str = str(path)
        try:
            st = os.stat(path)
        except OSError:
            # File vanished between scan and read; fall back to a bare item.
            return MediaItem(path=path, name=path.name)
        try:
            cached = self._cache.get(path_str, st.st_mtime, st.st_size)  # type: ignore[union-attr]
        except Exception:
            cached = None
        if cached is not None:
            return _item_from_cache(path, cached)
        # Cache miss: full parse, then store for next time.
        item = self._reader.read(path)
        try:
            meta, kind = _meta_from_item(item)
            if kind is not None:
                self._cache.put(  # type: ignore[union-attr]
                    path_str, st.st_mtime, st.st_size, kind, meta
                )
        except Exception:
            # Caching is best-effort; never let a cache write break a scan.
            pass
        return item


def _item_from_cache(path: Path, cached: Dict[str, Any]) -> MediaItem:
    """Rebuild a MediaItem from a ScanCache row dict."""
    kind = cached.get("kind")
    if kind == "wav":
        meta = AudioMetadata(
            duration_seconds=cached.get("duration_seconds") or 0.0,
            sample_rate_hz=cached.get("sample_rate_hz") or 0,
            channels=cached.get("channels") or 0,
            bpm=cached.get("bpm"),
            key=cached.get("key"),
        )
        return AudioItem(path=path, name=path.name, metadata=meta)
    if kind == "midi":
        tpb = cached.get("ticks_per_beat")
        tc = cached.get("track_count")
        if tpb is not None and tc is not None:
            meta = MidiMetadata(
                ticks_per_beat=tpb,
                track_count=tc,
                duration_seconds=cached.get("duration_seconds"),
                bpm=cached.get("bpm"),
                key=cached.get("key"),
            )
            return MidiItem(path=path, name=path.name, metadata=meta)
    # Unknown or partial cache entry: fall back to a bare item. The next
    # scan's prune will eventually drop it.
    return MediaItem(path=path, name=path.name)


def _meta_from_item(item: MediaItem) -> Tuple[Dict[str, Any], Optional[str]]:
    """Extract a cacheable meta dict + kind tag from a parsed MediaItem."""
    if isinstance(item, AudioItem) and item.metadata is not None:
        m = item.metadata
        return (
            {
                "duration_seconds": m.duration_seconds,
                "sample_rate_hz": m.sample_rate_hz,
                "channels": m.channels,
                "bpm": m.bpm,
                "key": m.key,
            },
            "wav",
        )
    if isinstance(item, MidiItem) and item.metadata is not None:
        m = item.metadata
        return (
            {
                "duration_seconds": m.duration_seconds,
                "ticks_per_beat": m.ticks_per_beat,
                "track_count": m.track_count,
                "bpm": m.bpm,
                "key": m.key,
            },
            "midi",
        )
    return {}, None


def _apply_user_metadata(items: Iterable[MediaItem], user_meta: Dict[Path, UserMetadata]) -> List[MediaItem]:
    updated: List[MediaItem] = []
    for item in items:
        meta = user_meta.get(item.path)
        if meta is None:
            updated.append(item)
        else:
            updated.append(replace(item, rating=meta.rating, tags=tuple(meta.tags)))
    return updated
