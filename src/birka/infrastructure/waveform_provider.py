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
