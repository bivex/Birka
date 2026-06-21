"""Persistent scan-result cache for the library scanner.

Stores parsed file metadata keyed by absolute path, invalidated by
(mtime, size). Backed by SQLite (stdlib) so it survives restarts and
supports concurrent readers during a ThreadPoolExecutor-driven scan.

Schema is intentionally flat (one row per file) so a cache hit is a single
primary-key lookup and an upsert is a single INSERT OR REPLACE. The cache
file lives at `cache_path` (default `data/.scan_cache.sqlite`) and should be
gitignored.

Thread safety: the connection is opened with check_same_thread=False and
WAL journaling, and every public method wraps its SQL in a short-lived
transaction. Concurrent readers + a single writer (the scan completing) is
safe under WAL; the scanner writes only after all reads for a refresh are
done, so contention is minimal.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set


_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_meta (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    kind TEXT NOT NULL,
    duration_seconds REAL,
    sample_rate_hz INTEGER,
    channels INTEGER,
    bpm REAL,
    key TEXT,
    ticks_per_beat INTEGER,
    track_count INTEGER,
    cached_at REAL NOT NULL
);
"""


class ScanCache:
    """Path-keyed metadata cache invalidated by (mtime, size).

    Callers stat() the file themselves (the scanner already does) and pass
    the (mtime, size) pair to get(); a hit is returned only when both match
    the cached values, so an edited file (new mtime) or a truncated/grown
    file (new size) is always re-parsed.
    """

    def __init__(self, cache_path: os.PathLike | str) -> None:
        self._path = str(cache_path)
        # check_same_thread=False: read by worker threads, written by the
        # consolidating thread after the scan. A module-level lock serializes
        # writes; reads are concurrent-safe under WAL.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL: readers don't block the writer and vice-versa, important when
        # the ThreadPoolExecutor workers each call get() while a previous
        # batch's puts may still be flushing.
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            # Filesystem may not support WAL (rare); fall back to default.
            pass
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def get(self, path: str, mtime: float, size: int) -> Optional[Dict[str, Any]]:
        """Return cached metadata dict if (mtime, size) match, else None.

        Takes the write lock too: under the ThreadPoolExecutor-driven scan a
        worker may put() a path the same instant another thread get()s it, and
        a read mid-commit (even under WAL) can transiently miss the just-written
        row. The lock is held only for the duration of one SELECT, so this does
        not meaningfully serialize reads in practice (each lookup is a single
        primary-key fetch).
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM file_meta WHERE path = ?", (path,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        # Strict invalidation: any change to the file on disk invalidates.
        if abs(float(row["mtime"]) - float(mtime)) > 1e-6 or int(row["size"]) != int(size):
            return None
        return {
            "kind": row["kind"],
            "duration_seconds": row["duration_seconds"],
            "sample_rate_hz": row["sample_rate_hz"],
            "channels": row["channels"],
            "bpm": row["bpm"],
            "key": row["key"],
            "ticks_per_beat": row["ticks_per_beat"],
            "track_count": row["track_count"],
        }

    def put(
        self,
        path: str,
        mtime: float,
        size: int,
        kind: str,
        meta: Dict[str, Any],
    ) -> None:
        """Upsert a file's metadata. Thread-safe via the write lock."""
        now = time.time()
        values = (
            path,
            float(mtime),
            int(size),
            kind,
            meta.get("duration_seconds"),
            meta.get("sample_rate_hz"),
            meta.get("channels"),
            meta.get("bpm"),
            meta.get("key"),
            meta.get("ticks_per_beat"),
            meta.get("track_count"),
            now,
        )
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO file_meta
                   (path, mtime, size, kind, duration_seconds, sample_rate_hz,
                    channels, bpm, key, ticks_per_beat, track_count, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            self._conn.commit()

    def put_many(self, entries: Iterable[tuple]) -> None:
        """Batch upsert. Each entry is the values tuple matching put().

        One transaction for the whole batch — far cheaper than per-row commits
        when a scan populates hundreds of cache misses at once.
        """
        now = time.time()
        rows = [(*e, now) for e in entries]
        with self._lock:
            self._conn.executemany(
                """INSERT OR REPLACE INTO file_meta
                   (path, mtime, size, kind, duration_seconds, sample_rate_hz,
                    channels, bpm, key, ticks_per_beat, track_count, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self._conn.commit()

    def prune(self, existing_paths: Set[str]) -> int:
        """Delete cache rows whose path is not in `existing_paths`.

        Called after a scan so files removed from disk don't accumulate in
        the cache forever. Returns the number of rows deleted.
        """
        if not existing_paths:
            # Avoid building a giant NOT IN () on an empty set (which would
            # delete everything); an empty library is handled by the caller
            # deleting rows case by case if ever needed.
            return 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM file_meta WHERE path NOT IN (%s)"
                % ",".join("?" * len(existing_paths)),
                tuple(existing_paths),
            )
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
