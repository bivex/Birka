from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from birka.domain.media import MediaItem


class FileScanner(ABC):
    @abstractmethod
    def scan(self, root: Path) -> Iterable[Path]:
        raise NotImplementedError


class MetadataReader(ABC):
    @abstractmethod
    def read(self, path: Path) -> MediaItem:
        raise NotImplementedError
