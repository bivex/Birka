from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Rating:
    value: int

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 5:
            raise ValueError("Rating must be between 0 and 5.")


@dataclass(frozen=True)
class AudioMetadata:
    duration_seconds: float
    sample_rate_hz: int
    channels: int
    bpm: Optional[float] = None
    key: Optional[str] = None


@dataclass(frozen=True)
class MidiMetadata:
    ticks_per_beat: int
    track_count: int
    bpm: Optional[float] = None
    key: Optional[str] = None


@dataclass(frozen=True)
class MediaItem:
    path: Path
    name: str
    rating: Optional[Rating] = None


@dataclass(frozen=True)
class AudioItem(MediaItem):
    metadata: AudioMetadata | None = None


@dataclass(frozen=True)
class MidiItem(MediaItem):
    metadata: MidiMetadata | None = None
