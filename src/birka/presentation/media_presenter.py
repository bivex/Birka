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
    # Full "YYYY-MM-DD HH:MM" timestamps for tooltips; the created/modified
    # fields above are the compact display form ("24 Jun, 22:42").
    created_full: str = ""
    modified_full: str = ""


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
            created_dt = datetime.fromtimestamp(stat.st_ctime)
            modified_dt = datetime.fromtimestamp(stat.st_mtime)
            created_full = created_dt.strftime("%Y-%m-%d %H:%M")
            modified_full = modified_dt.strftime("%Y-%m-%d %H:%M")
            created = _format_compact_date(created_dt)
            modified = _format_compact_date(modified_dt)
        else:
            created = modified = created_full = modified_full = ""
        rating = _format_rating(item)
        tags = _format_tags(item)
        if isinstance(item, AudioItem):
            return _audio_row(
                item, created, modified, rating, tags, created_full, modified_full
            )
        if isinstance(item, MidiItem):
            return _midi_row(
                item, created, modified, rating, tags, created_full, modified_full
            )
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
            created_full=created_full,
            modified_full=modified_full,
        )


def _format_optional(value) -> str:  # noqa: ANN001
    if value is None:
        return ""
    return str(value)


def _format_compact_date(dt: datetime) -> str:
    """Compact, low-noise timestamp for the table.

    Recent files read as a relative age ("just now", "5m ago", "3h ago",
    "2d ago"); older ones as "24 Jun, 22:42" (current year) or "24 Jun 2025"
    (other years). The full "YYYY-MM-DD HH:MM" stays available for tooltips.
    """
    now = datetime.now()
    delta = now - dt
    secs = delta.total_seconds()
    if secs < 0:
        # Clock skew / future mtime — just show the absolute compact form.
        pass
    elif secs < 60:
        return "just now"
    elif secs < 3600:
        return f"{int(secs // 60)}m ago"
    elif secs < 86400:
        return f"{int(secs // 3600)}h ago"
    elif secs < 7 * 86400:
        return f"{int(secs // 86400)}d ago"
    if dt.year == now.year:
        return dt.strftime("%-d %b, %H:%M")
    return dt.strftime("%-d %b %Y")


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
    item: AudioItem, created: str, modified: str, rating: str, tags: str,
    created_full: str = "", modified_full: str = "",
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
        created_full=created_full,
        modified_full=modified_full,
    )


def _midi_row(
    item: MidiItem, created: str, modified: str, rating: str, tags: str,
    created_full: str = "", modified_full: str = "",
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
        created_full=created_full,
        modified_full=modified_full,
    )
