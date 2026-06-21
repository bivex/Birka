from __future__ import annotations

import os
import re
import struct
import wave
from pathlib import Path
from typing import Optional

from birka.application.media_ports import MetadataReader
from birka.domain.media import (
    AudioItem,
    AudioMetadata,
    MediaItem,
    MidiItem,
    MidiMetadata,
)

MIDI_HEADER_ID = b"MThd"
MIDI_TRACK_ID = b"MTrk"
RIFF_ID = b"RIFF"
WAVE_ID = b"WAVE"
MIDI_META_EVENT = 0xFF
MIDI_SYSEX_EVENTS = (0xF0, 0xF7)
MIDI_TEMPO_META = 0x51
MIDI_KEY_META = 0x59
MIDI_DEFAULT_BPM = 120.0
MIDI_HEADER_MIN = 14
MIDI_HEADER_SIZE = 6
RIFF_HEADER_SIZE = 12
WAV_CHUNK_HEADER = 8
WAV_META_CHUNKS = {b"bext", b"LIST", b"INFO", b"ICMT", b"INAM"}
MIDI_STATUS_NOTE_OFF_MAX = 0xBF
MIDI_STATUS_PROGRAM_CHANGE_MAX = 0xDF
MIDI_STATUS_PITCH_MAX = 0xEF
MIDI_DIVISION_SMPTE_MASK = 0x8000
MIDI_STATUS_BYTE_MASK = 0x80
MIDI_SYSEX_THRESHOLD = 0xF0
MIDI_MICROSECONDS_PER_MINUTE = 60_000_000
MIDI_SECONDS_PER_MINUTE = 60.0
MIDI_VLQ_CONTINUATION_MASK = 0x7F
MIDI_VLQ_MSB_MASK = 0x80
MIDI_NOTE_ON_LOWER = 0x80
MIDI_PROGRAM_CHANGE_LOWER = 0xC0
MIDI_PITCH_BEND_LOWER = 0xE0

_MIDI_DATA_LENGTHS = [0] * 256
for _s in range(0x80, 0xC0):
    _MIDI_DATA_LENGTHS[_s] = 2
for _s in range(0xC0, 0xE0):
    _MIDI_DATA_LENGTHS[_s] = 1
for _s in range(0xE0, 0xF0):
    _MIDI_DATA_LENGTHS[_s] = 2


class AudioMidiMetadataReader(MetadataReader):
    def read(self, path: Path) -> MediaItem:
        suffix = path.suffix.lower()
        if suffix == ".wav":
            return _read_wav(path)
        if suffix in {".mid", ".midi"}:
            return _read_midi(path)
        return MediaItem(path=path, name=path.name)


def _parse_wav_header_fallback(path: Path) -> tuple[int, int, int]:
    fmt_tag = None
    channels = 0
    rate = 0
    block_align = 0
    data_size = None

    try:
        with open(path, "rb") as f:
            # Skip RIFF header
            f.seek(12)
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                chunk_id = header[:4]
                chunk_size = struct.unpack("<I", header[4:8])[0]
                if chunk_id == b"fmt ":
                    fmt_body = f.read(chunk_size)
                    if len(fmt_body) >= 16:
                        fmt_tag, channels, rate, _, block_align, _ = struct.unpack("<HHIIHH", fmt_body[:16])
                elif chunk_id == b"data":
                    data_size = chunk_size
                    break
                else:
                    # Skip chunk, alignment padding if size is odd
                    f.seek(chunk_size + (chunk_size & 1), 1)
    except Exception:
        pass

    if fmt_tag is None or data_size is None or block_align == 0 or rate == 0 or channels == 0:
        raise ValueError("Invalid or unsupported WAV metadata")

    frames = data_size // block_align
    return frames, rate, channels


def _read_wav(path: Path) -> AudioItem:
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            channels = wav.getnchannels()
    except wave.Error:
        try:
            frames, rate, channels = _parse_wav_header_fallback(path)
        except Exception:
            frames, rate, channels = 0, 0, 0

    duration = frames / rate if rate else 0.0
    bpm, key = _extract_bpm_key_from_wav(path)
    if bpm is None or key is None:
        fallback_bpm, fallback_key = _extract_bpm_key_from_name(path.stem)
        bpm = bpm if bpm is not None else fallback_bpm
        key = key if key is not None else fallback_key
    metadata = AudioMetadata(
        duration_seconds=duration,
        sample_rate_hz=rate,
        channels=channels,
        bpm=bpm,
        key=key,
    )
    return AudioItem(path=path, name=path.name, metadata=metadata)


def _read_midi(path: Path) -> MidiItem:
    data = path.read_bytes()
    ticks_per_beat, track_count, bpm, key, duration_seconds = _parse_midi(data)
    if bpm is None or key is None:
        fallback_bpm, fallback_key = _extract_bpm_key_from_name(path.stem)
        bpm = bpm if bpm is not None else fallback_bpm
        key = key if key is not None else fallback_key
    metadata = None
    if ticks_per_beat is not None and track_count is not None:
        metadata = MidiMetadata(
            ticks_per_beat=ticks_per_beat,
            track_count=track_count,
            duration_seconds=duration_seconds,
            bpm=bpm,
            key=key,
        )
    return MidiItem(path=path, name=path.name, metadata=metadata)


def _parse_midi(
    data: bytes,
) -> tuple[
    Optional[int], Optional[int], Optional[float], Optional[str], Optional[float]
]:
    if len(data) < MIDI_HEADER_MIN or data[:4] != MIDI_HEADER_ID:
        return None, None, None, None, None
    header_length = struct.unpack(">I", data[4:8])[0]
    if header_length < MIDI_HEADER_SIZE or len(data) < 8 + header_length:
        return None, None, None, None, None
    _, ntrks, division = struct.unpack(">HHH", data[8 : 8 + MIDI_HEADER_SIZE])
    if division & MIDI_DIVISION_SMPTE_MASK:
        ticks_per_beat = None
    else:
        ticks_per_beat = division
    bpm = None
    key = None
    max_ticks = 0
    offset = 8 + header_length
    for _ in range(ntrks):
        if offset + 8 > len(data) or data[offset : offset + 4] != MIDI_TRACK_ID:
            break
        track_length = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        track_data = data[offset + 8 : offset + 8 + track_length]
        bpm, key, ticks = _scan_midi_track(track_data, bpm, key)
        max_ticks = max(max_ticks, ticks)
        offset += 8 + track_length
    duration_seconds = _ticks_to_seconds(max_ticks, ticks_per_beat, bpm)
    return ticks_per_beat, ntrks, bpm, key, duration_seconds


def _scan_midi_track(
    data: bytes, bpm: Optional[float], key: Optional[str]
) -> tuple[Optional[float], Optional[str], int]:
    idx = 0
    running_status = None
    ticks = 0
    while idx < len(data):
        delta, idx = _read_vlq(data, idx)
        ticks += delta
        if idx >= len(data):
            break
        status = data[idx]
        if status < MIDI_STATUS_BYTE_MASK and running_status is not None:
            status = running_status
        else:
            idx += 1
            running_status = status if status < MIDI_SYSEX_THRESHOLD else None
        if status == MIDI_META_EVENT:
            if idx >= len(data):
                break
            meta_type = data[idx]
            idx += 1
            length, idx = _read_vlq(data, idx)
            meta_data = data[idx : idx + length]
            idx += length
            if meta_type == MIDI_TEMPO_META and length == 3 and bpm is None:
                tempo = (meta_data[0] << 16) | (meta_data[1] << 8) | meta_data[2]
                if tempo:
                    bpm = round(MIDI_MICROSECONDS_PER_MINUTE / tempo, 3)
            if meta_type == MIDI_KEY_META and length == 2 and key is None:
                sf = meta_data[0]
                sf = sf - 256 if sf >= 128 else sf
                key = _decode_key_signature(sf, meta_data[1])
        elif status in MIDI_SYSEX_EVENTS:
            length, idx = _read_vlq(data, idx)
            idx += length
        else:
            idx += _MIDI_DATA_LENGTHS[status]
    return bpm, key, ticks


def _ticks_to_seconds(
    ticks: int, ticks_per_beat: Optional[int], bpm: Optional[float]
) -> Optional[float]:
    if ticks_per_beat is None or ticks_per_beat <= 0:
        return None
    effective_bpm = bpm or MIDI_DEFAULT_BPM
    return (ticks / ticks_per_beat) * (MIDI_SECONDS_PER_MINUTE / effective_bpm)


def _read_vlq(data: bytes, idx: int) -> tuple[int, int]:
    value = 0
    while idx < len(data):
        byte = data[idx]
        idx += 1
        value = (value << 7) | (byte & MIDI_VLQ_CONTINUATION_MASK)
        if byte & MIDI_VLQ_MSB_MASK == 0:
            break
    return value, idx





def _decode_key_signature(sf: int, mi: int) -> str:
    major_keys = [
        "Cb",
        "Gb",
        "Db",
        "Ab",
        "Eb",
        "Bb",
        "F",
        "C",
        "G",
        "D",
        "A",
        "E",
        "B",
        "F#",
        "C#",
    ]
    minor_keys = [
        "Abm",
        "Ebm",
        "Bbm",
        "Fm",
        "Cm",
        "Gm",
        "Dm",
        "Am",
        "Em",
        "Bm",
        "F#m",
        "C#m",
        "G#m",
        "D#m",
        "A#m",
    ]
    index = sf + 7
    if 0 <= index < len(major_keys):
        return major_keys[index] if mi == 0 else minor_keys[index]
    return ""


ENHARMONIC_MAPPING = {
    # Major
    "A#": "Bb",
    "D#": "Eb",
    "G#": "Ab",
    "E#": "F",
    "B#": "C",
    "Fb": "E",
    # Minor
    "Dbm": "C#m",
    "Gbm": "F#m",
    "Cbm": "Bm",
    "E#m": "Fm",
    "Esm": "Fm",
    "B#m": "Cm",
    "Bsm": "Cm",
    "Fbm": "Em",
}


def normalize_key(key_str: str) -> str | None:
    if not key_str:
        return None
    key_str = key_str.strip()
    if not key_str:
        return None
    match = re.match(r"^([A-Ga-g])(#|b|s)?\s*(?:maj|major|min|minor|m)?$", key_str, re.IGNORECASE)
    if not match:
        return None
    root = match.group(1).upper()
    accidental = match.group(2)
    if accidental:
        accidental = accidental.lower()
        if accidental == "s":
            accidental = "#"
    else:
        accidental = ""
    is_minor = False
    lower_str = key_str.lower()
    if lower_str.endswith("m") or "min" in lower_str or "minor" in lower_str:
        is_minor = True
    normalized = f"{root}{accidental}"
    if is_minor:
        normalized += "m"
    normalized = ENHARMONIC_MAPPING.get(normalized, normalized)
    standard_keys = {
        "Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#",
        "Abm", "Ebm", "Bbm", "Fm", "Cm", "Gm", "Dm", "Am", "Em", "Bm", "F#m", "C#m", "G#m", "D#m", "A#m"
    }
    if normalized in standard_keys:
        return normalized
    return None


def _extract_bpm_key_from_wav(path: Path) -> tuple[Optional[float], Optional[str]]:
    # Stream the file chunk-by-chunk instead of read_bytes()-ing the whole
    # thing. The BPM/KEY metadata lives in small bext/LIST/INFO chunks that
    # almost always precede the (potentially huge) data chunk, so we read at
    # most a few KB and stop as soon as we hit "data". For a 500 MB WAV this
    # turns a full-slurp into a sub-KB read.
    payloads: list[bytes] = []
    try:
        with open(str(path), "rb") as fh:
            header = fh.read(RIFF_HEADER_SIZE)
            if len(header) < RIFF_HEADER_SIZE or header[:4] != RIFF_ID or header[8:12] != WAVE_ID:
                return None, None
            while True:
                chunk_hdr = fh.read(WAV_CHUNK_HEADER)
                if len(chunk_hdr) < WAV_CHUNK_HEADER:
                    break
                chunk_id = chunk_hdr[:4]
                size = struct.unpack("<I", chunk_hdr[4:8])[0]
                if chunk_id == b"data":
                    # Audio payload starts here — no more metadata after this.
                    break
                if chunk_id in WAV_META_CHUNKS:
                    # Cap a single chunk read to avoid a malicious/huge chunk
                    # dragging us back to slurp-land; metadata chunks are tiny.
                    payloads.append(fh.read(min(size, 65536)))
                    if size > 65536:
                        fh.seek(size - 65536, os.SEEK_CUR)
                else:
                    # Skip non-metadata chunk bodies without reading them.
                    fh.seek(size, os.SEEK_CUR)
                # Chunks are word-aligned: skip the pad byte for odd sizes.
                if size % 2:
                    fh.seek(1, os.SEEK_CUR)
    except OSError:
        return None, None
    combined = b" ".join(payloads)
    text = combined.decode("utf-8", errors="ignore")
    bpm_match = re.search(r"BPM\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    key_match = re.search(r"KEY\s*[:=]\s*([A-Ga-g](?:#|b)?(?:m|maj|min)?)", text)
    bpm = float(bpm_match.group(1)) if bpm_match else None
    key = key_match.group(1) if key_match else None
    if key:
        key = normalize_key(key)
    return bpm, key


def _extract_bpm_key_from_name(stem: str) -> tuple[Optional[float], Optional[str]]:
    clean_stem = stem.replace("_", " ").replace("-", " ")
    bpm_match = re.search(r"\b(\d{2,3})\s*bpm\b", clean_stem, re.IGNORECASE)
    bpm = None
    if bpm_match:
        bpm = float(bpm_match.group(1))
    else:
        all_nums = re.findall(r"\b(\d{2,3})\b", clean_stem)
        for num_str in reversed(all_nums):
            val = int(num_str)
            if 50 <= val <= 220:
                bpm = float(val)
                break
    minor_pattern = r"(?<![a-zA-Z0-9])([A-Ga-g](?:#|b|s)?\s*(?:m|min|minor))(?![a-zA-Z0-9])"
    major_pattern = r"(?<![a-zA-Z0-9])([A-Ga-g](?:#|b|s)?\s*(?:maj|major))(?![a-zA-Z0-9])"
    accidental_pattern = r"(?<![a-zA-Z0-9])([A-Ga-g](?:#|b|s))(?![a-zA-Z0-9])"
    single_letter_pattern = r"(?<![a-zA-Z0-9])([A-G])(?![a-zA-Z0-9])"
    key = None
    m = re.search(minor_pattern, clean_stem, re.IGNORECASE)
    if m:
        key = normalize_key(m.group(1))
    if key is None:
        m = re.search(major_pattern, clean_stem, re.IGNORECASE)
        if m:
            key = normalize_key(m.group(1))
    if key is None:
        m = re.search(accidental_pattern, clean_stem, re.IGNORECASE)
        if m:
            key = normalize_key(m.group(1))
    if key is None:
        m = re.search(single_letter_pattern, clean_stem)
        if m:
            key = normalize_key(m.group(1))
    return bpm, key
