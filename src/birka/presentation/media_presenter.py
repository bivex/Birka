from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List

from birka.domain.media import AudioItem, MediaItem, MidiItem


@dataclass(frozen=True)
class MediaRow:
    path: str
    name: str
    media_type: str
    bpm: str
    key: str
    duration: str
    rating: str
    tags: str
    created: str
    modified: str


class MediaPresenter:
    def to_rows(self, items: Iterable[MediaItem]) -> List[MediaRow]:
        return [self._to_row(item) for item in items]

    def _to_row(self, item: MediaItem) -> MediaRow:
        stat = item.path.stat()
        created = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M")
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        if isinstance(item, AudioItem):
            metadata = item.metadata
            duration = _format_duration(metadata.duration_seconds) if metadata else ""
            return MediaRow(
                path=str(item.path),
                name=item.name,
                media_type="Audio",
                bpm=_format_optional(metadata.bpm) if metadata else "",
                key=_format_optional(metadata.key) if metadata else "",
                duration=duration,
                rating=_format_rating(item),
                tags=_format_tags(item),
                created=created,
                modified=modified,
            )
        if isinstance(item, MidiItem):
            metadata = item.metadata
            duration = _format_duration(metadata.duration_seconds) if metadata and metadata.duration_seconds else ""
            return MediaRow(
                path=str(item.path),
                name=item.name,
                media_type="MIDI",
                bpm=_format_optional(metadata.bpm) if metadata else "",
                key=_format_optional(metadata.key) if metadata else "",
                duration=duration,
                rating=_format_rating(item),
                tags=_format_tags(item),
                created=created,
                modified=modified,
            )
        return MediaRow(
            path=str(item.path),
            name=item.name,
            media_type="Unknown",
            bpm="",
            key="",
            duration="",
            rating=_format_rating(item),
            tags=_format_tags(item),
            created=created,
            modified=modified,
        )


def _format_optional(value) -> str:  # noqa: ANN001
    if value is None:
        return ""
    return str(value)


def _format_duration(seconds: float) -> str:
    if seconds < 0:
        return ""
    minutes = int(seconds // 60)
    remainder = int(seconds % 60)
    return f"{minutes:02d}:{remainder:02d}"


def _format_rating(item: MediaItem) -> str:
    if item.rating is None:
        return ""
    return str(item.rating.value)


def _format_tags(item: MediaItem) -> str:
    if not item.tags:
        return ""
    return ", ".join(item.tags)
