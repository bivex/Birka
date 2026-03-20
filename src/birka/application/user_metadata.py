from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from birka.domain.media import Rating


@dataclass(frozen=True)
class UserMetadata:
    rating: Optional[Rating]
    tags: List[str]


class UserMetadataStore:
    def load_all(self) -> Dict[Path, UserMetadata]:
        raise NotImplementedError

    def save(self, path: Path, metadata: UserMetadata) -> None:
        raise NotImplementedError

    def delete(self, path: Path) -> None:
        raise NotImplementedError

    def save_many(self, entries: Iterable[tuple[Path, UserMetadata]]) -> None:
        for path, metadata in entries:
            self.save(path, metadata)

    def delete_many(self, paths: Iterable[Path]) -> None:
        for path in paths:
            self.delete(path)
