from __future__ import annotations

import wave
from pathlib import Path
from typing import List


class WaveformProvider:
    def load(self, path: Path, points: int = 200) -> List[float]:
        if path.suffix.lower() != ".wav":
            return []
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            channels = wav.getnchannels()
            sampwidth = wav.getsampwidth()
            if sampwidth != 2:
                return []
            raw = wav.readframes(frames)
        samples = _to_samples(raw, channels)
        if not samples:
            return []
        return _downsample(samples, points)


def _to_samples(raw: bytes, channels: int) -> List[int]:
    if channels <= 0:
        return []
    samples = []
    step = 2 * channels
    for i in range(0, len(raw), step):
        frame = raw[i : i + step]
        if len(frame) < step:
            break
        total = 0
        for c in range(channels):
            sample = int.from_bytes(frame[c * 2 : c * 2 + 2], byteorder="little", signed=True)
            total += sample
        samples.append(total // channels)
    return samples


def _downsample(samples: List[int], points: int) -> List[float]:
    if points <= 0:
        return []
    bucket = max(1, len(samples) // points)
    result: List[float] = []
    for i in range(0, len(samples), bucket):
        chunk = samples[i : i + bucket]
        if not chunk:
            continue
        peak = max(abs(min(chunk)), abs(max(chunk)))
        result.append(peak / 32768.0)
    return result[:points]
