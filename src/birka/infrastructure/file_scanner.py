from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator

from birka.application.media_ports import FileScanner


class FileSystemScanner(FileScanner):
    def __init__(self, extensions: Iterable[str]) -> None:
        super().__init__()
        # Precompute ".wav"-style suffixes for a cheap str.endswith check
        # during the walk; building the set once avoids re-lowercasing per
        # entry.
        self._suffixes = tuple(ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions)

    def scan(self, root: Path) -> Iterator[Path]:
        # Recursive os.scandir walk instead of Path.rglob("*").
        #
        # rglob("*") yields every entry (files AND directories) and then
        # forces a separate stat() per entry just to test is_file(), so a
        # tree with N files costs ~2N syscalls (readdir + stat each).
        # os.scandir's DirEntry caches the stat returned by readdir, so
        # entry.is_file()/is_dir() need no extra syscall — roughly half the
        # I/O for the same tree. The walk is iterative (explicit stack) to
        # avoid Python recursion limits on deeply nested libraries.
        stack: list[str] = [str(root)]
        suffixes = self._suffixes
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(suffixes):
                                yield Path(entry.path)
                        except OSError:
                            # Individual entry unreadable (permissions, broken
                            # symlink); skip rather than abort the whole walk.
                            continue
            except (PermissionError, OSError):
                # Whole directory unreadable; move on to the next stack entry.
                continue
