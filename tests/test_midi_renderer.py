import tempfile
import unittest
from pathlib import Path
import wave

from birka.infrastructure.midi_renderer import (
    render_midi_to_wav,
    render_midi_to_mp3,
    _find_soundfont,
)


class MidiRendererTests(unittest.TestCase):
    def test_find_soundfont(self) -> None:
        sf = _find_soundfont()
        self.assertIsNotNone(sf)
        self.assertTrue(sf.exists())

    def test_render_midi_to_wav(self) -> None:
        midi_path = Path("/Volumes/External/Code/Birka/data/library/test_128_Csm.mid")
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "output.wav"
            success = render_midi_to_wav(midi_path, wav_path)
            self.assertTrue(success)
            self.assertTrue(wav_path.exists())
            # Verify WAV properties
            with wave.open(str(wav_path), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 2)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertGreater(wf.getnframes(), 0)

    def test_render_midi_to_mp3(self) -> None:
        midi_path = Path("/Volumes/External/Code/Birka/data/library/test_128_Csm.mid")
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            mp3_path = render_midi_to_mp3(midi_path, out_dir)
            self.assertIsNotNone(mp3_path)
            self.assertTrue(mp3_path.exists())
            self.assertGreater(mp3_path.stat().st_size, 0)
            self.assertEqual(mp3_path.suffix, ".mp3")
