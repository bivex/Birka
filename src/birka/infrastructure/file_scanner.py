from __future__ import annotations

from pathlib import Path
from typing import Iterable

from birka.application.media_ports import FileScanner


class FileSystemScanner(FileScanner):
    def __init__(self, extensions: Iterable[str]) -> None:
        self._extensions = {ext.lower() for ext in extensions}

    def scan(self, root: Path) -> Iterable[Path]:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in self._extensions:
                yield path
