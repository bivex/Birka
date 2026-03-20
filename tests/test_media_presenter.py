import unittest
from pathlib import Path

from birka.domain.media import AudioItem, AudioMetadata, MidiItem, MidiMetadata, Rating
from birka.presentation.media_presenter import MediaPresenter


class MediaPresenterTests(unittest.TestCase):
    def test_maps_audio_item(self) -> None:
        item = AudioItem(
            path=Path("/tmp/clip.wav"),
            name="clip.wav",
            metadata=AudioMetadata(65.0, 44100, 2, bpm=120.0, key="C#m"),
            rating=Rating(5),
        )
        presenter = MediaPresenter()

        rows = presenter.to_rows([item])

        self.assertEqual(rows[0].path, "/tmp/clip.wav")
        self.assertEqual(rows[0].media_type, "Audio")
        self.assertEqual(rows[0].bpm, "120.0")
        self.assertEqual(rows[0].key, "C#m")
        self.assertEqual(rows[0].duration, "01:05")
        self.assertEqual(rows[0].rating, "5")
        self.assertEqual(rows[0].tags, "")

    def test_maps_midi_item(self) -> None:
        item = MidiItem(
            path=Path("/tmp/pattern.mid"),
            name="pattern.mid",
            metadata=MidiMetadata(480, 2, bpm=90.0, key="Am"),
        )
        presenter = MediaPresenter()

        rows = presenter.to_rows([item])

        self.assertEqual(rows[0].path, "/tmp/pattern.mid")
        self.assertEqual(rows[0].media_type, "MIDI")
        self.assertEqual(rows[0].bpm, "90.0")
        self.assertEqual(rows[0].key, "Am")
        self.assertEqual(rows[0].tags, "")
        self.assertEqual(rows[0].duration, "")
