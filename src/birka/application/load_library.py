from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Dict, Iterable, List

from birka.application.media_ports import FileScanner, MetadataReader
from birka.application.user_metadata import UserMetadata, UserMetadataStore
from birka.domain.media import MediaItem


class LoadLibrary:
    def __init__(self, scanner: FileScanner, reader: MetadataReader, metadata_store: UserMetadataStore) -> None:
        self._scanner = scanner
        self._reader = reader
        self._metadata_store = metadata_store

    def execute(self, root: Path) -> List[MediaItem]:
        items = [self._reader.read(path) for path in self._scanner.scan(root)]
        user_meta = self._metadata_store.load_all()
        return _apply_user_metadata(items, user_meta)


def _apply_user_metadata(items: Iterable[MediaItem], user_meta: Dict[Path, UserMetadata]) -> List[MediaItem]:
    updated: List[MediaItem] = []
    for item in items:
        meta = user_meta.get(item.path)
        if meta is None:
            updated.append(item)
        else:
            updated.append(replace(item, rating=meta.rating, tags=tuple(meta.tags)))
    return updated
