from __future__ import annotations

import struct
import wave
from pathlib import Path
from typing import List


class WaveformProvider:
    POINTS_DEFAULT = 200

    def load(self, path: Path, points: int = 0) -> List[float]:
        effective_points = points or self.POINTS_DEFAULT
        if path.suffix.lower() != ".wav":
            return []
        # IEEE_FLOAT WAVs (WAVE_FORMAT_IEEE_FLOAT, fmt tag 3) -- written by the
        # sfizz backend -- are rejected by the stdlib wave module, so detect and
        # parse them manually. Falls through to the wave module for PCM int.
        float_samples = _maybe_read_float_wav(path)
        if float_samples is not None:
            return _downsample_float(float_samples, effective_points)
        try:
            with wave.open(str(path), "rb") as wav:
                frames = wav.getnframes()
                channels = wav.getnchannels()
                sampwidth = wav.getsampwidth()
                raw = wav.readframes(frames)
        except Exception:
            return []
        samples = _to_samples(raw, channels, sampwidth)
        if not samples:
            return []
        max_value = _max_for_sampwidth(sampwidth)
        return _downsample(samples, effective_points, max_value)


def _max_for_sampwidth(sampwidth: int) -> float:
    """Return the maximum absolute integer value for a given sample byte width."""
    return float(1 << (sampwidth * 8 - 1))


def _to_samples(raw: bytes, channels: int, sampwidth: int) -> List[int]:
    if channels <= 0 or sampwidth not in (1, 2, 3, 4):
        return []

    fmt_map = {1: "b", 2: "h", 4: "i"}
    frame_size = sampwidth * channels

    if sampwidth in fmt_map:
        fmt = f"<{len(raw) // sampwidth}{fmt_map[sampwidth]}"
        all_samples = struct.unpack(fmt, raw[: (len(raw) // frame_size) * frame_size])
        # Mix down to mono by averaging channels
        samples = []
        for i in range(0, len(all_samples), channels):
            frame = all_samples[i : i + channels]
            samples.append(sum(frame) // len(frame))
        return samples
    else:
        # 24-bit: 3 bytes per sample, signed
        samples = []
        for i in range(0, len(raw) - frame_size + 1, frame_size):
            total = 0
            for c in range(channels):
                offset = i + c * 3
                b = raw[offset : offset + 3]
                val = int.from_bytes(b, byteorder="little", signed=True)
                total += val
            samples.append(total // channels)
        return samples


def _downsample(samples: List[int], points: int, max_value: float) -> List[float]:
    if points <= 0:
        return []
    bucket = max(1, len(samples) // points)
    result: List[float] = []
    for i in range(0, len(samples), bucket):
        chunk = samples[i : i + bucket]
        if not chunk:
            continue
        peak = max(abs(min(chunk)), abs(max(chunk)))
        result.append(min(1.0, peak / max_value))
    return result[:points]


def _maybe_read_float_wav(path: Path) -> List[float] | None:
    """Parse a 32-bit IEEE_FLOAT WAV and return mono peak-normalized samples.

    Returns None if the file is not an IEEE_FLOAT WAV (so PCM-int files fall
    back to the wave-module path). Mixes stereo to mono by averaging channels.
    """
    import struct as _struct

    try:
        with open(str(path), "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    fmt_tag = None
    channels = 0
    audio = b""
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos : pos + 4]
        csize = _struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        body = data[pos + 8 : pos + 8 + csize]
        if cid == b"fmt " and len(body) >= 8:
            fmt_tag, channels = _struct.unpack("<HH", body[:4])
        elif cid == b"data":
            audio = body
        pos += 8 + csize + (csize & 1)  # word-aligned chunks
    if fmt_tag != 3 or channels <= 0 or not audio:
        return None  # not IEEE_FLOAT (or empty) -> defer to caller

    count = len(audio) // 4
    floats = list(_struct.unpack(f"<{count}f", audio[: count * 4]))
    # Mix down to mono by averaging channels.
    mono: List[float] = []
    for i in range(0, len(floats) - channels + 1, channels):
        frame = floats[i : i + channels]
        mono.append(sum(frame) / len(frame))
    return mono


def _downsample_float(samples: List[float], points: int) -> List[float]:
    """Downsample float samples to *points* peak values in [0, 1].

    Float samples are already in [-1, 1] (no max_value scaling needed); we just
    take the per-bucket peak absolute amplitude.
    """
    if points <= 0 or not samples:
        return []
    bucket = max(1, len(samples) // points)
    result: List[float] = []
    for i in range(0, len(samples), bucket):
        chunk = samples[i : i + bucket]
        if not chunk:
            continue
        result.append(min(1.0, max(abs(min(chunk)), abs(max(chunk)))))
    return result[:points]
