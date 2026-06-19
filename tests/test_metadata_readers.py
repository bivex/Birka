import struct
import tempfile
import unittest
from pathlib import Path

from birka.infrastructure.metadata_readers import (
    AudioMidiMetadataReader,
    _extract_bpm_key_from_name,
)


class MetadataReaderTests(unittest.TestCase):
    def test_reads_wav_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "tone.wav"
            wav_path.write_bytes(_build_wav_with_bext(b"BPM=128 KEY=F#m"))

            reader = AudioMidiMetadataReader()
            item = reader.read(wav_path)

            self.assertEqual(item.metadata.sample_rate_hz, 44100)
            self.assertEqual(item.metadata.channels, 2)
            self.assertAlmostEqual(item.metadata.duration_seconds, 1.0, places=2)
            self.assertEqual(item.metadata.bpm, 128.0)
            self.assertEqual(item.metadata.key, "F#m")

    def test_reads_float_wav_metadata(self) -> None:
        # Build an IEEE float WAV (format code 3, 32-bit floats)
        # fmt_chunk size: 16, format tag: 3 (float), channels: 2, sample_rate: 44100, byte_rate: 44100*8, block_align: 8, bits_per_sample: 32
        fmt_chunk = b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, 44100, 44100 * 8, 8, 32)
        data_bytes = b"\x00" * 44100 * 8  # 1 second of stereo float32
        data_chunk = b"data" + struct.pack("<I", len(data_bytes)) + data_bytes
        riff_size = 4 + len(fmt_chunk) + len(data_chunk)
        float_wav = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + fmt_chunk + data_chunk

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "float_tone.wav"
            wav_path.write_bytes(float_wav)

            reader = AudioMidiMetadataReader()
            item = reader.read(wav_path)

            self.assertEqual(item.metadata.sample_rate_hz, 44100)
            self.assertEqual(item.metadata.channels, 2)
            self.assertAlmostEqual(item.metadata.duration_seconds, 1.0, places=2)

    def test_reads_midi_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            midi_path = Path(tmp) / "pattern.mid"
            midi_path.write_bytes(_build_midi_file(track_count=1, ticks_per_beat=480))

            reader = AudioMidiMetadataReader()
            item = reader.read(midi_path)

            self.assertEqual(item.metadata.track_count, 1)
            self.assertEqual(item.metadata.ticks_per_beat, 480)
            self.assertEqual(item.metadata.bpm, 120.0)
            self.assertEqual(item.metadata.key, "C")
            self.assertAlmostEqual(item.metadata.duration_seconds, 0.0, places=2)

    def test_extract_bpm_key_from_name(self) -> None:
        test_cases = [
            ("song_Csm_120", (120.0, "C#m")),
            ("test_159_Fsm", (159.0, "F#m")),
            ("A#_120bpm", (120.0, "Bb")),
            ("Bb_120bpm", (120.0, "Bb")),
            ("track_with_a_nice_melody", (None, None)),
            ("track_with_A_nice_melody", (None, "A")),
            ("130_Db_minor", (130.0, "C#m")),
            ("95_csm", (95.0, "C#m")),
        ]
        for name, expected in test_cases:
            with self.subTest(name=name):
                self.assertEqual(_extract_bpm_key_from_name(name), expected)


def _build_midi_file(track_count: int, ticks_per_beat: int) -> bytes:
    header = b"MThd" + struct.pack(">IHHH", 6, 1, track_count, ticks_per_beat)
    tempo_event = b"\x00\xFF\x51\x03\x07\xA1\x20"
    key_event = b"\x00\xFF\x59\x02\x00\x00"
    end_event = b"\x00\xFF\x2F\x00"
    track_data = tempo_event + key_event + end_event
    track = b"MTrk" + struct.pack(">I", len(track_data)) + track_data
    return header + track


def _build_wav_with_bext(description: bytes) -> bytes:
    fmt_chunk = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 44100 * 4, 4, 16)
    data_bytes = b"\x00\x00" * 44100 * 2
    data_chunk = b"data" + struct.pack("<I", len(data_bytes)) + data_bytes
    bext_payload = description.ljust(256, b"\x00")
    bext_chunk = b"bext" + struct.pack("<I", len(bext_payload)) + bext_payload
    riff_size = 4 + len(fmt_chunk) + len(bext_chunk) + len(data_chunk)
    return b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + fmt_chunk + bext_chunk + data_chunk
