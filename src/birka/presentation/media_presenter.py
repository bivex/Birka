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
        # Batch the stat() calls once per refresh instead of one syscall per
        # row inside _to_row. A library with N files paid N separate stat()
        # round-trips on the UI thread after every scan (incl. the 10s
        # auto-refresh); this collapses them to a single pass with fail-soft
        # handling so a deleted/vanished file doesn't abort row building.
        materialized = list(items)
        stats: dict = {}
        for item in materialized:
            try:
                stats[str(item.path)] = item.path.stat()
            except OSError:
                stats[str(item.path)] = None
        return [self._to_row(item, stats.get(str(item.path))) for item in materialized]

    def _to_row(self, item: MediaItem, stat=None) -> MediaRow:
        if stat is None:
            try:
                stat = item.path.stat()
            except OSError:
                stat = None
        if stat is not None:
            created = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M")
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        else:
            created = modified = ""
        rating = _format_rating(item)
        tags = _format_tags(item)
        if isinstance(item, AudioItem):
            return _audio_row(item, created, modified, rating, tags)
        if isinstance(item, MidiItem):
            return _midi_row(item, created, modified, rating, tags)
        return MediaRow(
            path=str(item.path),
            name=item.name,
            media_type="Unknown",
            bpm="",
            key="",
            duration="",
            rating=rating,
            tags=tags,
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


def _audio_row(
    item: AudioItem, created: str, modified: str, rating: str, tags: str
) -> MediaRow:
    metadata = item.metadata
    duration = _format_duration(metadata.duration_seconds) if metadata else ""
    return MediaRow(
        path=str(item.path),
        name=item.name,
        media_type="Audio",
        bpm=_format_optional(metadata.bpm) if metadata else "",
        key=_format_optional(metadata.key) if metadata else "",
        duration=duration,
        rating=rating,
        tags=tags,
        created=created,
        modified=modified,
    )


def _midi_row(
    item: MidiItem, created: str, modified: str, rating: str, tags: str
) -> MediaRow:
    metadata = item.metadata
    has_dur = metadata and metadata.duration_seconds
    duration = _format_duration(metadata.duration_seconds) if has_dur else ""
    return MediaRow(
        path=str(item.path),
        name=item.name,
        media_type="MIDI",
        bpm=_format_optional(metadata.bpm) if metadata else "",
        key=_format_optional(metadata.key) if metadata else "",
        duration=duration,
        rating=rating,
        tags=tags,
        created=created,
        modified=modified,
    )
