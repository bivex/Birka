from __future__ import annotations

from pathlib import Path
from typing import List

from birka.application.media_ports import FileScanner, MetadataReader
from birka.domain.media import MediaItem


class ScanLibrary:
    def __init__(self, scanner: FileScanner, reader: MetadataReader) -> None:
        self._scanner = scanner
        self._reader = reader

    def execute(self, root: Path) -> List[MediaItem]:
        items: List[MediaItem] = []
        for path in self._scanner.scan(root):
            items.append(self._reader.read(path))
        return items
