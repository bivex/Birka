from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from birka.application.user_metadata import UserMetadata, UserMetadataStore
from birka.domain.media import Rating


class JsonUserMetadataStore(UserMetadataStore):
    def __init__(self, path: Path) -> None:
        self._path = path

    def load_all(self) -> Dict[Path, UserMetadata]:
        if not self._path.exists():
            return {}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        result: Dict[Path, UserMetadata] = {}
        for raw_path, data in payload.items():
            rating_value = data.get("rating")
            rating = Rating(rating_value) if rating_value is not None else None
            tags = list(data.get("tags", []))
            result[Path(raw_path)] = UserMetadata(rating=rating, tags=tags)
        return result

    def save(self, path: Path, metadata: UserMetadata) -> None:
        payload = {}
        if self._path.exists():
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        payload[str(path)] = {
            "rating": metadata.rating.value if metadata.rating else None,
            "tags": list(metadata.tags),
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete(self, path: Path) -> None:
        if not self._path.exists():
            return
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        payload.pop(str(path), None)
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
