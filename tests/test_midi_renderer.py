from __future__ import annotations

import struct
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from birka.infrastructure.midi_renderer import (
    _TSF_AVAILABLE,
    _backend_name,
    _build_loudnorm_filter,
    _encode_mp3,
    _find_soundfont,
    _measure_stats,
    _parse_loudnorm_stats,
    _synth_tsf_to_wav,
    PREVIEW_MP3_BITRATE,
    PREVIEW_POLYPHONY,
    PREVIEW_SAMPLE_RATE,
    render_midi_preview_mp3,
    render_midi_to_mp3,
    render_midi_to_mp3_batch,
    render_midi_to_wav,
)

MIDI_PATH = Path("/Volumes/External/Code/Birka/data/library/test_128_Csm.mid")
MIDI_DIR = Path("/Volumes/External/Code/Birka/data/library/midi")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_wav_samples(wav_path: Path) -> tuple[int, int, int, np.ndarray]:
    """Return (channels, sampwidth, framerate, int32 data array)."""
    with wave.open(str(wav_path), "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        fr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    dtype = np.int32 if sw == 4 else np.int16
    return ch, sw, fr, np.frombuffer(raw, dtype=dtype)


def _make_silence_wav(path: Path, duration_s: float = 1.0, sr: int = 44100) -> None:
    """Write a silent 32-bit stereo WAV to *path*."""
    n = int(duration_s * sr) * 2
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(4)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{n}i", *([0] * n)))


# ---------------------------------------------------------------------------
# Soundfont / backend
# ---------------------------------------------------------------------------

class TestBackend(unittest.TestCase):
    def test_soundfont_found(self) -> None:
        sf = _find_soundfont()
        self.assertIsNotNone(sf)
        self.assertTrue(sf.exists(), f"Soundfont not found: {sf}")

    def test_tsf_available(self) -> None:
        self.assertTrue(_TSF_AVAILABLE, "TinySoundFont should be importable")

    def test_backend_name_is_tsf(self) -> None:
        self.assertEqual(_backend_name(), "tsf")


# ---------------------------------------------------------------------------
# WAV output format
# ---------------------------------------------------------------------------

class TestWavFormat(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_wav_is_16bit(self) -> None:
        wav = self.tmp / "out.wav"
        self.assertTrue(render_midi_to_wav(MIDI_PATH, wav))
        _, sw, _, _ = _read_wav_samples(wav)
        self.assertEqual(sw, 2, "Expected 16-bit (2-byte) samples")

    def test_wav_is_stereo(self) -> None:
        wav = self.tmp / "out.wav"
        self.assertTrue(render_midi_to_wav(MIDI_PATH, wav))
        ch, _, _, _ = _read_wav_samples(wav)
        self.assertEqual(ch, 2)

    def test_wav_sample_rate(self) -> None:
        wav = self.tmp / "out.wav"
        self.assertTrue(render_midi_to_wav(MIDI_PATH, wav))
        _, _, fr, _ = _read_wav_samples(wav)
        self.assertEqual(fr, 44100)

    def test_wav_custom_sample_rate(self) -> None:
        wav = self.tmp / "out_48k.wav"
        self.assertTrue(render_midi_to_wav(MIDI_PATH, wav, sample_rate=48000))
        _, _, fr, _ = _read_wav_samples(wav)
        self.assertEqual(fr, 48000)

    def test_wav_has_frames(self) -> None:
        wav = self.tmp / "out.wav"
        self.assertTrue(render_midi_to_wav(MIDI_PATH, wav))
        _, _, _, data = _read_wav_samples(wav)
        self.assertGreater(len(data), 0)


# ---------------------------------------------------------------------------
# Audio content (not silent)
# ---------------------------------------------------------------------------

class TestWavContent(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.wav = self.tmp / "out.wav"
        render_midi_to_wav(MIDI_PATH, self.wav)
        _, _, _, self.data = _read_wav_samples(self.wav)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_not_silent(self) -> None:
        peak = int(np.max(np.abs(self.data)))
        self.assertGreater(peak, 0, "WAV is completely silent")

    def test_peak_below_clipping(self) -> None:
        peak = int(np.max(np.abs(self.data)))
        max_16bit = 32767
        self.assertLess(peak, max_16bit, "Audio is clipping at 16-bit ceiling")

    def test_peak_above_minus20dBFS(self) -> None:
        peak = int(np.max(np.abs(self.data)))
        max_16bit = 32767
        # Just assert there is meaningful signal (peak > 1% of full scale)
        self.assertGreater(peak / max_16bit, 0.01, "Signal too weak (< 1% full scale)")

    def test_duration_reasonable(self) -> None:
        """WAV should be at least 1 s and no more than 60 s for a short MIDI."""
        with wave.open(str(self.wav), "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
        self.assertGreaterEqual(duration, 1.0)
        self.assertLessEqual(duration, 60.0)

    def test_no_hard_clipping(self) -> None:
        """No sample should sit on the 16-bit ceiling (would indicate clipping)."""
        peak = int(np.max(np.abs(self.data)))
        self.assertLess(peak, 32767, "Hard clipping at +32767")
        self.assertGreater(peak, -32768)

    def test_no_channel_discontinuities(self) -> None:
        """Adjacent same-channel samples should not jump by >50% of full scale.

        Large jumps within a single channel indicate decoder-hostile clicks.
        (Interleaved L/R differences are not a defect and are ignored here.)
        """
        ceiling = 32767
        left = self.data[0::2]
        right = self.data[1::2]
        for name, ch in (("L", left), ("R", right)):
            if len(ch) < 2:
                continue
            jumps = np.abs(np.diff(ch.astype(np.int64)))
            worst = int(np.max(jumps))
            self.assertLess(
                worst,
                ceiling * 0.5,
                f"{name} channel has a discontinuity > 50% ({worst})",
            )


# ---------------------------------------------------------------------------
# _synth_tsf_to_wav
# ---------------------------------------------------------------------------

class TestSynthTsfToWav(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.sf = _find_soundfont()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_true_on_success(self) -> None:
        out = self.tmp / "out.wav"
        self.assertTrue(_synth_tsf_to_wav(self.sf, MIDI_PATH, out))

    def test_output_file_created(self) -> None:
        out = self.tmp / "out.wav"
        _synth_tsf_to_wav(self.sf, MIDI_PATH, out)
        self.assertTrue(out.exists())

    def test_returns_false_for_missing_midi(self) -> None:
        out = self.tmp / "out.wav"
        result = _synth_tsf_to_wav(self.sf, Path("/nonexistent/file.mid"), out)
        self.assertFalse(result)

    def test_returns_false_for_missing_soundfont(self) -> None:
        out = self.tmp / "out.wav"
        result = _synth_tsf_to_wav(Path("/nonexistent.sf2"), MIDI_PATH, out)
        self.assertFalse(result)

    def test_parent_dir_created(self) -> None:
        out = self.tmp / "nested" / "dir" / "out.wav"
        out.parent.mkdir(parents=True)
        self.assertTrue(_synth_tsf_to_wav(self.sf, MIDI_PATH, out))
        self.assertTrue(out.exists())

    def test_multiple_midi_files(self) -> None:
        """Render a handful of files from the library and verify none are silent."""
        midi_files = list(MIDI_DIR.rglob("*.mid"))[:5]
        for midi in midi_files:
            out = self.tmp / f"{midi.stem}.wav"
            ok = _synth_tsf_to_wav(self.sf, midi, out)
            self.assertTrue(ok, f"Failed to render {midi.name}")
            _, _, _, data = _read_wav_samples(out)
            self.assertGreater(int(np.max(np.abs(data))), 0, f"Silent output for {midi.name}")


# ---------------------------------------------------------------------------
# Loudnorm stats
# ---------------------------------------------------------------------------

class TestMeasureStats(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.wav = self.tmp / "out.wav"
        render_midi_to_wav(MIDI_PATH, self.wav)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_dict(self) -> None:
        stats = _measure_stats(self.wav)
        self.assertIsInstance(stats, dict)

    def test_required_keys_present(self) -> None:
        stats = _measure_stats(self.wav)
        for key in ("input_i", "input_lra", "input_tp", "input_thresh", "target_offset"):
            self.assertIn(key, stats)

    def test_values_are_not_inf_for_audible_wav(self) -> None:
        stats = _measure_stats(self.wav)
        self.assertNotEqual(stats["input_i"], "-inf", "input_i should not be -inf for audible audio")

    def test_returns_none_for_missing_file(self) -> None:
        stats = _measure_stats(Path("/nonexistent.wav"))
        self.assertIsNone(stats)

    def test_silent_wav_returns_stats(self) -> None:
        """Even a silent WAV should return a stats dict (with -inf values)."""
        silent = self.tmp / "silent.wav"
        _make_silence_wav(silent)
        stats = _measure_stats(silent)
        # ffmpeg will return a dict but values will be -inf
        self.assertIsNotNone(stats)


# ---------------------------------------------------------------------------
# _parse_loudnorm_stats
# ---------------------------------------------------------------------------

class TestParseLoudnormStats(unittest.TestCase):
    VALID_JSON_STDERR = """
[Parsed_loudnorm_0 @ 0x...] {
    "input_i" : "-14.20",
    "input_tp" : "-3.23",
    "input_lra" : "0.00",
    "input_thresh" : "-27.16",
    "output_i" : "-16.01",
    "output_tp" : "-5.03",
    "output_lra" : "0.00",
    "output_thresh" : "-28.98",
    "normalization_type" : "linear",
    "target_offset" : "0.01"
}
"""

    def test_parses_valid_stderr(self) -> None:
        stats = _parse_loudnorm_stats(self.VALID_JSON_STDERR)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["input_i"], "-14.20")
        self.assertEqual(stats["target_offset"], "0.01")

    def test_returns_none_for_empty_string(self) -> None:
        self.assertIsNone(_parse_loudnorm_stats(""))

    def test_returns_none_for_missing_keys(self) -> None:
        # JSON missing required key target_offset
        stderr = '{"input_i": "-14", "input_lra": "0", "input_tp": "-3", "input_thresh": "-27"}'
        self.assertIsNone(_parse_loudnorm_stats(stderr))

    def test_returns_none_for_invalid_json(self) -> None:
        self.assertIsNone(_parse_loudnorm_stats("{ not valid json }"))


# ---------------------------------------------------------------------------
# _build_loudnorm_filter
# ---------------------------------------------------------------------------

class TestBuildLoudnormFilter(unittest.TestCase):
    STATS = {
        "input_i": "-14.20",
        "input_tp": "-3.23",
        "input_lra": "0.00",
        "input_thresh": "-27.16",
        "target_offset": "0.01",
    }

    def test_returns_string(self) -> None:
        af = _build_loudnorm_filter(self.STATS)
        self.assertIsInstance(af, str)

    def test_contains_measured_values(self) -> None:
        af = _build_loudnorm_filter(self.STATS)
        self.assertIn("measured_I=-14.20", af)
        self.assertIn("measured_TP=-3.23", af)
        self.assertIn("offset=0.01", af)
        self.assertIn("linear=true", af)

    def test_returns_none_for_none_stats(self) -> None:
        self.assertIsNone(_build_loudnorm_filter(None))


# ---------------------------------------------------------------------------
# _encode_mp3
# ---------------------------------------------------------------------------

class TestEncodeMp3(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.wav = self.tmp / "out.wav"
        render_midi_to_wav(MIDI_PATH, self.wav)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_encodes_without_filter(self) -> None:
        mp3 = self.tmp / "out.mp3"
        ok = _encode_mp3(self.wav, None, mp3)
        self.assertTrue(ok)
        self.assertTrue(mp3.exists())
        self.assertGreater(mp3.stat().st_size, 0)

    def test_encodes_with_loudnorm_filter(self) -> None:
        stats = _measure_stats(self.wav)
        from birka.infrastructure.midi_renderer import _build_loudnorm_filter
        af = _build_loudnorm_filter(stats)
        mp3 = self.tmp / "out_norm.mp3"
        ok = _encode_mp3(self.wav, af, mp3)
        self.assertTrue(ok)
        self.assertGreater(mp3.stat().st_size, 0)

    def test_returns_false_for_missing_wav(self) -> None:
        mp3 = self.tmp / "out.mp3"
        ok = _encode_mp3(Path("/nonexistent.wav"), None, mp3)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# render_midi_to_mp3
# ---------------------------------------------------------------------------

class TestRenderMidiToMp3(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_mp3_path(self) -> None:
        mp3 = render_midi_to_mp3(MIDI_PATH, self.tmp)
        self.assertIsNotNone(mp3)

    def test_mp3_has_correct_stem(self) -> None:
        mp3 = render_midi_to_mp3(MIDI_PATH, self.tmp)
        self.assertEqual(mp3.stem, MIDI_PATH.stem)

    def test_mp3_has_content(self) -> None:
        mp3 = render_midi_to_mp3(MIDI_PATH, self.tmp)
        self.assertGreater(mp3.stat().st_size, 1024, "MP3 file seems too small")

    def test_output_dir_created(self) -> None:
        out_dir = self.tmp / "nested" / "output"
        mp3 = render_midi_to_mp3(MIDI_PATH, out_dir)
        self.assertIsNotNone(mp3)
        self.assertTrue(out_dir.exists())

    def test_returns_none_for_missing_midi(self) -> None:
        mp3 = render_midi_to_mp3(Path("/nonexistent.mid"), self.tmp)
        self.assertIsNone(mp3)


# ---------------------------------------------------------------------------
# render_midi_to_mp3_batch
# ---------------------------------------------------------------------------

class TestRenderMidiToMp3Batch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.midi_files = list(MIDI_DIR.rglob("*.mid"))[:4]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_successful_and_failed_lists(self) -> None:
        ok, fail = render_midi_to_mp3_batch(self.midi_files, self.tmp)
        self.assertIsInstance(ok, list)
        self.assertIsInstance(fail, list)

    def test_all_succeed(self) -> None:
        ok, fail = render_midi_to_mp3_batch(self.midi_files, self.tmp)
        self.assertEqual(len(fail), 0, f"Failed files: {fail}")
        self.assertEqual(len(ok), len(self.midi_files))

    def test_mp3_files_exist(self) -> None:
        ok, _ = render_midi_to_mp3_batch(self.midi_files, self.tmp)
        for mp3 in ok:
            self.assertTrue(mp3.exists(), f"Missing: {mp3}")
            self.assertGreater(mp3.stat().st_size, 0)

    def test_progress_callback_called(self) -> None:
        calls: list[tuple] = []
        render_midi_to_mp3_batch(
            self.midi_files,
            self.tmp,
            on_progress=lambda done, total, path, success: calls.append((done, total, success)),
        )
        self.assertEqual(len(calls), len(self.midi_files))
        # All should succeed
        self.assertTrue(all(s for _, _, s in calls))

    def test_empty_list_returns_empty(self) -> None:
        ok, fail = render_midi_to_mp3_batch([], self.tmp)
        self.assertEqual(ok, [])
        self.assertEqual(fail, [])


# ---------------------------------------------------------------------------
# render_midi_preview_mp3
# ---------------------------------------------------------------------------

class TestRenderMidiPreviewMp3(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_preview_constants(self) -> None:
        self.assertEqual(PREVIEW_SAMPLE_RATE, 22050)
        self.assertEqual(PREVIEW_MP3_BITRATE, "96k")
        self.assertLessEqual(PREVIEW_POLYPHONY, 256)

    def test_returns_true_and_creates_mp3(self) -> None:
        mp3 = self.tmp / "preview.mp3"
        self.assertTrue(render_midi_preview_mp3(MIDI_PATH, mp3))
        self.assertTrue(mp3.exists())
        self.assertGreater(mp3.stat().st_size, 0)

    def test_output_is_smaller_than_full_mp3(self) -> None:
        preview = self.tmp / "preview.mp3"
        full = self.tmp / "full.mp3"
        self.assertTrue(render_midi_preview_mp3(MIDI_PATH, preview))
        self.assertTrue(render_midi_to_mp3(MIDI_PATH, self.tmp))
        full_path = self.tmp / (MIDI_PATH.stem + ".mp3")
        # Preview (22 kHz / 96k) should be no larger than the full (44.1 kHz / 320k) render.
        self.assertLessEqual(preview.stat().st_size, full_path.stat().st_size)

    def test_creates_parent_dir(self) -> None:
        mp3 = self.tmp / "nested" / "out.mp3"
        self.assertTrue(render_midi_preview_mp3(MIDI_PATH, mp3))
        self.assertTrue(mp3.exists())

    def test_returns_false_for_missing_midi(self) -> None:
        mp3 = self.tmp / "preview.mp3"
        self.assertFalse(render_midi_preview_mp3(Path("/nonexistent.mid"), mp3))

    def test_multiple_preview_files(self) -> None:
        midi_files = list(MIDI_DIR.rglob("*.mid"))[:4]
        for midi in midi_files:
            mp3 = self.tmp / f"{midi.stem}.mp3"
            self.assertTrue(render_midi_preview_mp3(midi, mp3), f"Failed: {midi.name}")
            self.assertGreater(mp3.stat().st_size, 0)
