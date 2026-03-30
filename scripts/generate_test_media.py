from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path


SAMPLE_WIDTH_BYTES = 2
DEFAULT_TONE_HZ = 440.0
DEFAULT_AMPLITUDE = 0.2
MIDI_TICKS_PER_BEAT = 480
MIDI_NOTE = 60
MIDI_VELOCITY = 100


def build_wav(
    path: Path, seconds: float, sample_rate: int, channels: int, bpm: float, key: str
) -> None:
    frames = int(seconds * sample_rate)
    tone_hz = DEFAULT_TONE_HZ
    amplitude = DEFAULT_AMPLITUDE
    samples = bytearray()
    for n in range(frames):
        value = int(
            amplitude * 32767 * math.sin(2 * math.pi * tone_hz * (n / sample_rate))
        )
        packed = struct.pack("<h", value)
        samples.extend(packed * channels)

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(sample_rate)
        wav.writeframes(samples)

    _inject_bext(path, f"BPM={bpm} KEY={key}".encode("utf-8"))


def _inject_bext(path: Path, description: bytes) -> None:
    raw = path.read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return
    fmt_offset = raw.find(b"fmt ")
    data_offset = raw.find(b"data")
    if fmt_offset == -1 or data_offset == -1:
        return
    _ = data_offset
    bext_payload = description.ljust(256, b"\x00")
    bext_chunk = b"bext" + struct.pack("<I", len(bext_payload)) + bext_payload
    header = raw[:12]
    # Rebuild: header + fmt chunk + bext + rest (data and others)
    fmt_chunk_size = struct.unpack("<I", raw[fmt_offset + 4 : fmt_offset + 8])[0]
    fmt_end = fmt_offset + 8 + fmt_chunk_size
    new_body = raw[12:fmt_end] + bext_chunk + raw[fmt_end:]
    riff_size = 4 + len(new_body)
    rebuilt = header[:4] + struct.pack("<I", riff_size) + header[8:12] + new_body
    path.write_bytes(rebuilt)


def build_midi(path: Path, bpm: float, key_signature: str) -> None:
    ticks_per_beat = MIDI_TICKS_PER_BEAT
    tempo = int(60_000_000 / bpm)
    tempo_event = b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big")
    key_event = b"\x00\xff\x59\x02" + _encode_key_signature(key_signature)
    note_on = bytes([0x00, 0x90, MIDI_NOTE, MIDI_VELOCITY])
    note_off = bytes([0x83, 0x60, 0x80, MIDI_NOTE, 0x00])
    end_event = b"\x00\xff\x2f\x00"
    track_data = tempo_event + key_event + note_on + note_off + end_event
    header = b"MThd" + struct.pack(">IHHH", 6, 1, 1, ticks_per_beat)
    track = b"MTrk" + struct.pack(">I", len(track_data)) + track_data
    path.write_bytes(header + track)


def _encode_key_signature(key: str) -> bytes:
    mapping = {
        "Cb": -7,
        "Gb": -6,
        "Db": -5,
        "Ab": -4,
        "Eb": -3,
        "Bb": -2,
        "F": -1,
        "C": 0,
        "G": 1,
        "D": 2,
        "A": 3,
        "E": 4,
        "B": 5,
        "F#": 6,
        "C#": 7,
    }
    minor_mapping = {
        "Abm": -7,
        "Ebm": -6,
        "Bbm": -5,
        "Fm": -4,
        "Cm": -3,
        "Gm": -2,
        "Dm": -1,
        "Am": 0,
        "Em": 1,
        "Bm": 2,
        "F#m": 3,
        "C#m": 4,
        "G#m": 5,
        "D#m": 6,
        "A#m": 7,
    }
    normalized = key.strip()
    if normalized.endswith("m"):
        sf = minor_mapping.get(normalized, 0)
        mi = 1
    else:
        sf = mapping.get(normalized, 0)
        mi = 0
    return struct.pack("bb", sf, mi)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate test WAV and MIDI files for Birka"
    )
    parser.add_argument(
        "--out",
        default="/Volumes/External/Code/Birka/data/library",
        help="Output folder",
    )
    parser.add_argument("--bpm", type=float, default=128.0)
    parser.add_argument("--key", default="C#m")
    parser.add_argument("--seconds", type=float, default=1.0)
    parser.add_argument("--sample-rate", type=int, default=44100)
    parser.add_argument("--channels", type=int, default=2)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_key = args.key.replace("#", "s").replace("b", "b")
    stem = f"test_{int(args.bpm)}_{safe_key}"
    wav_path = out_dir / f"{stem}.wav"
    midi_path = out_dir / f"{stem}.mid"

    build_wav(
        wav_path, args.seconds, args.sample_rate, args.channels, args.bpm, args.key
    )
    build_midi(midi_path, args.bpm, args.key)

    print(f"Generated {wav_path} and {midi_path}")


if __name__ == "__main__":
    main()
