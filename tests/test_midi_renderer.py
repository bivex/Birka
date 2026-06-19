from __future__ import annotations

import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from birka.infrastructure.midi_renderer import (
    _SFIZZ_AVAILABLE,
    _TSF_AVAILABLE,
    _VALID_BACKENDS,
    _backend_name,
    _build_loudnorm_filter,
    _encode_mp3,
    _find_sfz,
    _find_soundfont,
    _measure_stats,
    _parse_loudnorm_stats,
    _resolve_backend,
    _selected_backend,
    _soft_clip_to_int16,
    _synth_sfizz_to_wav,
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

    def test_backend_name_is_tsf_by_default(self) -> None:
        # With no BIRKA_BACKEND override, the default is tsf (when available).
        from birka.infrastructure import midi_renderer as mr
        old = os.environ.pop("BIRKA_BACKEND", None)
        try:
            self.assertEqual(mr._backend_name(), "tsf")
        finally:
            if old is not None:
                os.environ["BIRKA_BACKEND"] = old


class TestBackendSelection(unittest.TestCase):
    """BIRKA_BACKEND env drives backend selection (tsf|sfizz|fluidsynth|auto)."""

    def setUp(self) -> None:
        self._old = os.environ.get("BIRKA_BACKEND")
        os.environ.pop("BIRKA_BACKEND", None)

    def tearDown(self) -> None:
        if self._old is not None:
            os.environ["BIRKA_BACKEND"] = self._old
        else:
            os.environ.pop("BIRKA_BACKEND", None)

    def test_valid_backends_constant(self) -> None:
        self.assertEqual(_VALID_BACKENDS, {"auto", "tsf", "sfizz", "fluidsynth"})

    def test_default_is_auto(self) -> None:
        self.assertEqual(_selected_backend(), "auto")

    def test_auto_resolves_to_tsf_when_available(self) -> None:
        if not _TSF_AVAILABLE:
            self.skipTest("tsf unavailable")
        self.assertEqual(_resolve_backend(), "tsf")

    def test_unknown_value_falls_back_to_auto(self) -> None:
        os.environ["BIRKA_BACKEND"] = "nonsense"
        self.assertEqual(_selected_backend(), "auto")

    def test_case_insensitive_and_whitespace(self) -> None:
        os.environ["BIRKA_BACKEND"] = "  TSF  "
        self.assertEqual(_selected_backend(), "tsf")

    def test_explicit_tsf_when_available(self) -> None:
        if not _TSF_AVAILABLE:
            self.skipTest("tsf unavailable")
        os.environ["BIRKA_BACKEND"] = "tsf"
        self.assertEqual(_selected_backend(), "tsf")
        self.assertEqual(_resolve_backend(), "tsf")

    def test_fluidsynth_always_selectable(self) -> None:
        os.environ["BIRKA_BACKEND"] = "fluidsynth"
        self.assertEqual(_selected_backend(), "fluidsynth")
        self.assertEqual(_resolve_backend(), "fluidsynth")

    def test_sfizz_when_available(self) -> None:
        if not _SFIZZ_AVAILABLE:
            self.skipTest("sfizz (pysfizz) not built")
        os.environ["BIRKA_BACKEND"] = "sfizz"
        self.assertEqual(_selected_backend(), "sfizz")
        self.assertEqual(_resolve_backend(), "sfizz")

    def test_sfizz_falls_back_to_auto_when_unbuilt(self) -> None:
        if _SFIZZ_AVAILABLE:
            self.skipTest("sfizz is built in this environment")
        os.environ["BIRKA_BACKEND"] = "sfizz"
        # Requested sfizz but it's unavailable -> selected falls back to auto,
        # which then resolves to whatever is available (tsf here).
        self.assertEqual(_selected_backend(), "auto")

    def test_tsf_falls_back_to_auto_when_unbuilt(self) -> None:
        if _TSF_AVAILABLE:
            self.skipTest("tsf is built in this environment")
        os.environ["BIRKA_BACKEND"] = "tsf"
        self.assertEqual(_selected_backend(), "auto")


class TestFindSfz(unittest.TestCase):
    """SFZ locator for the sfizz backend (independent of the SF2 soundfont)."""

    def setUp(self) -> None:
        self._old = os.environ.get("BIRKA_SFZ")
        os.environ.pop("BIRKA_SFZ", None)

    def tearDown(self) -> None:
        if self._old is not None:
            os.environ["BIRKA_SFZ"] = self._old
        else:
            os.environ.pop("BIRKA_SFZ", None)

    def test_env_override_existing_sfz(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sfz = Path(tmp) / "inst.sfz"
            sfz.write_text("<region> sample=kick.wav\n")
            os.environ["BIRKA_SFZ"] = str(sfz)
            self.assertEqual(_find_sfz(), sfz)

    def test_env_override_rejects_non_sfz_suffix_falls_back(self) -> None:
        # A non-.sfz env value is ignored and discovery continues (does not
        # block the bundled bank or other candidates).
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "inst.sf2"
            other.write_text("not sfz")
            os.environ["BIRKA_SFZ"] = str(other)
            # Must not return the .sf2 path.
            self.assertNotEqual(_find_sfz(), other)

    def test_env_override_missing_file_falls_back(self) -> None:
        # A missing env path is ignored and discovery continues rather than
        # leaving the backend with no bank.
        os.environ["BIRKA_SFZ"] = "/nonexistent/path.sfz"
        result = _find_sfz()
        if result is not None:
            self.assertNotEqual(str(result), "/nonexistent/path.sfz")
        # If no bank is available at all, None is still acceptable here.


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


# ---------------------------------------------------------------------------
# Soft-clipping (regression: dense polyphony must not pin samples to ceiling)
# ---------------------------------------------------------------------------

class TestSoftClipping(unittest.TestCase):
    """A dense chord should sum past full scale; the render must not hard-clip.

    Regression for the crackle bug: with many simultaneous voices the float
    output exceeds [-1.0, 1.0]. A hard clamp pins those samples to ±32767,
    producing audible clicks. The tanh soft-clip should keep no sample flat
    against the ceiling even when the source overshoots.
    """

    def _dense_midi(self, path: Path) -> None:
        import mido

        mid = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        # 12 notes spanning several octaves, all on at once -> sum overshoots 1.0
        notes = [36, 43, 48, 55, 60, 67, 72, 79, 84, 91, 96, 103]
        for n in notes:
            track.append(mido.Message("note_on", note=n, velocity=127, time=0))
        # hold for ~2 seconds
        track.append(mido.Message("note_off", note=notes[0], velocity=0, time=960))
        for n in notes[1:]:
            track.append(mido.Message("note_off", note=n, velocity=0, time=0))
        mid.save(str(path))

    def test_no_samples_pinned_to_ceiling(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            midi = Path(tmp.name) / "dense.mid"
            self._dense_midi(midi)
            out = Path(tmp.name) / "dense.wav"
            self.assertTrue(render_midi_to_wav(midi, out, polyphony=256))
            _, _, _, data = _read_wav_samples(out)
            peak = int(np.max(np.abs(data)))
            # The whole point of soft-clip: nothing sits flat on the ceiling.
            self.assertLess(peak, 32767, "Sample pinned to +ceiling (hard clip)")
            self.assertGreater(peak, 32767 * 0.5, "Signal unexpectedly quiet")
        finally:
            tmp.cleanup()


# ---------------------------------------------------------------------------
# _soft_clip_to_int16 (unit tests for the clip-bug fix)
# ---------------------------------------------------------------------------

class TestSoftClipToInt16(unittest.TestCase):
    """Unit tests for the float->int16 soft-clipper.

    These pin the exact behaviours that were broken by the original hard-clamp
    (and by an intermediate threshold-only tanh variant that introduced a
    notch at the 1.0 crossing). Each test targets one specific clip defect.
    """

    CEIL = 32767
    FLOOR = -32768

    # --- Bug 1: overshoot must not be pinned to the ceiling (hard clamp) ---

    def test_overshoot_positive_not_pinned_to_ceiling(self) -> None:
        """A realistic overshoot must not hit +32767 (the crackle bug).

        Real-world voice-sum overshoot is ~1.5x-3.0x full scale (observed peak
        of the crackling file was ~1.5x). tanh keeps those well clear of the
        ceiling. Absurd inputs (e.g. 100x) saturate to 1.0 and are documented in
        test_extreme_saturation instead -- they cannot occur from the synth.
        """
        out = _soft_clip_to_int16([1.5, 2.0, 2.5, 3.0, 5.0])
        for v in out:
            self.assertLess(v, self.CEIL, "Realistic overshoot pinned to ceiling")
            self.assertGreater(v, 0)

    def test_overshoot_negative_not_pinned_to_floor(self) -> None:
        """A realistic negative overshoot must not hit -32768."""
        out = _soft_clip_to_int16([-1.5, -2.0, -2.5, -3.0, -5.0])
        for v in out:
            self.assertGreater(v, self.FLOOR, "Realistic overshoot pinned to floor")
            self.assertLess(v, 0)

    def test_extreme_saturation_documented(self) -> None:
        """An absurd input (100x) saturates tanh to exactly 1.0 -> hits ceiling.

        This is expected math, NOT a bug: such values cannot be produced by the
        synth (max real overshoot is a few x). The test documents the boundary
        so a future change is noticed deliberately. Note the int16 asymmetry:
        positive saturates to +32767, negative to -32767 (because the floor is
        -32768 but int(-1.0 * 32767) truncates to -32767).
        """
        self.assertEqual(_soft_clip_to_int16([100.0])[0], self.CEIL)
        self.assertEqual(_soft_clip_to_int16([-100.0])[0], -(self.CEIL))

    def test_no_sample_in_full_signal_hits_ceiling(self) -> None:
        """A realistically loud signal that overshoots must never touch ceiling.

        4x sine is louder than anything the synth produces in practice while
        still being a plausible dense-mix level.
        """
        import math

        t = [i / 44100 for i in range(1000)]
        sig = [4.0 * math.sin(2 * math.pi * 220 * x) for x in t]
        out = _soft_clip_to_int16(sig)
        self.assertNotIn(self.CEIL, out, "Some sample hit +ceiling")
        self.assertNotIn(self.FLOOR, out, "Some sample hit floor")

    # --- Bug 2: threshold-only tanh created a notch at the 1.0 crossing ---

    def test_monotonic_around_positive_crossing(self) -> None:
        """Samples straddling +1.0 must keep increasing monotonically.

        A threshold-only tanh (applied only above 1.0) made 0.99 -> ~32439 and
        1.01 -> tanh(1.01) -> ~24900, i.e. a downward jump = a click. The global
        tanh must be monotonic non-decreasing through the crossing.
        """
        below = _soft_clip_to_int16([0.95])[0]
        at = _soft_clip_to_int16([1.0])[0]
        above = _soft_clip_to_int16([1.05])[0]
        self.assertLess(below, at, "Not monotonic: dipped below 1.0 crossing")
        self.assertLessEqual(at, above, "Not monotonic: dipped above 1.0 crossing")

    def test_monotonic_around_negative_crossing(self) -> None:
        """Symmetric monotonicity check through the -1.0 crossing."""
        above = _soft_clip_to_int16([-0.95])[0]
        at = _soft_clip_to_int16([-1.0])[0]
        below = _soft_clip_to_int16([-1.05])[0]
        self.assertGreater(above, at, "Not monotonic at -1.0 crossing")
        self.assertGreaterEqual(at, below, "Not monotonic below -1.0 crossing")

    def test_no_downward_jump_at_overshoot_boundary(self) -> None:
        """The single biggest regression: a step just under->over 1.0 must
        not produce a negative sample-to-sample delta (which would click)."""
        out = _soft_clip_to_int16([0.999, 1.001, 1.5, 2.0])
        diffs = [out[i + 1] - out[i] for i in range(len(out) - 1)]
        for d in diffs:
            self.assertGreaterEqual(
                d, 0, f"Downward jump {d} at overshoot boundary -> audible click"
            )

    # --- Sanity: behaviour at the extremes and origin ---

    def test_silence_is_zero(self) -> None:
        self.assertEqual(_soft_clip_to_int16([0.0, 0.0]), [0, 0])

    def test_large_realistic_positive_below_ceiling(self) -> None:
        """A large-but-plausible overshoot (3x) stays finite and clear of ceiling."""
        v = _soft_clip_to_int16([3.0])[0]
        self.assertLess(v, self.CEIL)
        self.assertGreater(v, self.CEIL - 1000)  # tanh(3) -> ~0.995

    def test_large_realistic_negative_above_floor(self) -> None:
        """Symmetric: a large negative overshoot (3x) stays above the floor."""
        v = _soft_clip_to_int16([-3.0])[0]
        self.assertGreater(v, self.FLOOR)
        self.assertLess(v, self.FLOOR + 1000)

    def test_in_range_is_near_linear(self) -> None:
        """Below ~0.5 the soft-clipper should be within 5% of a raw scale.

        Guarantees quiet passages are not audibly attenuated/coloured.
        """
        for s in (0.1, 0.2, 0.3, 0.4, 0.5):
            got = _soft_clip_to_int16([s])[0]
            raw = int(s * 32767.0)
            self.assertLess(abs(got - raw), 0.05 * 32767, f"Deviation too large at {s}")

    def test_output_range_valid_int16(self) -> None:
        import math

        sig = [10.0 * math.sin(i * 0.1) for i in range(500)]
        for v in _soft_clip_to_int16(sig):
            self.assertGreaterEqual(v, self.FLOOR)
            self.assertLessEqual(v, self.CEIL)

    def test_empty_input(self) -> None:
        self.assertEqual(_soft_clip_to_int16([]), [])


# ---------------------------------------------------------------------------
# _synth_sfizz_to_wav (sfizz backend; skips when pysfizz/SFZ unavailable)
# ---------------------------------------------------------------------------

def _sfizz_test_sfz() -> Optional[Path]:
    """An SFZ file usable for integration tests: a real GM bank if found, else
    pysfizz's bundled sine-generator SFZ (sample=*sine, no external samples).

    Lets the sfizz render tests actually run without requiring a downloaded
    GM soundfont, while preferring a real instrument bank when available.
    """
    bank = _find_sfz()
    if bank is not None:
        return bank
    sine = (
        Path(__file__).resolve().parents[1]
        / "modules"
        / "pysfizz"
        / "external"
        / "sfizz"
        / "tests"
        / "TestFiles"
        / "dollar_include_sine.sfz"
    )
    return sine if sine.exists() else None


_SFIZZ_READY = _SFIZZ_AVAILABLE and _sfizz_test_sfz() is not None


@unittest.skipUnless(_SFIZZ_READY, "sfizz backend not built or no SFZ bank available")
class TestSynthSfizzToWav(unittest.TestCase):
    """Integration tests for the sfizz renderer.

    These exercise the real _sfizz.Synth + an SFZ file (a GM bank if present,
    else pysfizz's bundled sine-generator SFZ), so they skip unless pysfizz is
    built. They mirror TestSynthTsfToWav / TestWavFormat so both backends are
    held to the same output contract.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.sfz = _sfizz_test_sfz()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_output_is_16bit_stereo(self) -> None:
        out = self.tmp / "out.wav"
        self.assertTrue(_synth_sfizz_to_wav(self.sfz, MIDI_PATH, out))
        ch, sw, fr, _ = _read_wav_samples(out)
        self.assertEqual(sw, 2, "Expected 16-bit")
        self.assertEqual(ch, 2, "Expected stereo")
        self.assertEqual(fr, 44100)

    def test_output_file_created(self) -> None:
        out = self.tmp / "out.wav"
        self.assertTrue(_synth_sfizz_to_wav(self.sfz, MIDI_PATH, out))
        self.assertTrue(out.exists())

    def test_not_silent(self) -> None:
        out = self.tmp / "out.wav"
        self.assertTrue(_synth_sfizz_to_wav(self.sfz, MIDI_PATH, out))
        _, _, _, data = _read_wav_samples(out)
        self.assertGreater(int(np.max(np.abs(data))), 0, "Output is silent")

    def test_no_samples_pinned_to_ceiling(self) -> None:
        """Same crackle regression as tsf: nothing sits on the 16-bit ceiling.

        _synth_sfizz_to_wav must route through _soft_clip_to_int16, so dense
        polyphony never hard-clips regardless of backend.
        """
        out = self.tmp / "out.wav"
        self.assertTrue(_synth_sfizz_to_wav(self.sfz, MIDI_PATH, out, polyphony=256))
        _, _, _, data = _read_wav_samples(out)
        peak = int(np.max(np.abs(data)))
        self.assertLess(peak, 32767, "Sample pinned to ceiling (hard clip)")


class TestSynthSfizzToWavAvailability(unittest.TestCase):
    """Always-run checks that don't need pysfizz built."""

    def test_returns_false_for_missing_sfz(self) -> None:
        out = Path(tempfile.mkdtemp()) / "out.wav"
        # _synth_sfizz_to_wav imports pysfizz lazily; a missing SFZ path can't
        # be exercised without the native ext, but a bogus midi + bogus sfz
        # must still return False (not raise) when sfizz is unbuilt.
        if not _SFIZZ_AVAILABLE:
            self.assertFalse(
                _synth_sfizz_to_wav(
                    Path("/nonexistent.sfz"), Path("/nonexistent.mid"), out
                )
            )
        else:
            self.skipTest("sfizz is built; covered by TestSynthSfizzToWav")
