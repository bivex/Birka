import unittest
from pathlib import Path

from birka.domain.media import AudioItem, AudioMetadata
from birka.presentation.library_tab import _sort_path_for_item


class SortPathTests(unittest.TestCase):
    def test_sort_path_with_bpm(self) -> None:
        root = Path("/tmp/library")
        item = AudioItem(
            path=root / "kick.wav",
            name="kick.wav",
            metadata=AudioMetadata(1.0, 44100, 2, bpm=120.0, key="C"),
        )
        target = _sort_path_for_item(root, item)
        self.assertEqual(target, root / "wav" / "120bpm")

    def test_sort_path_unknown_bpm(self) -> None:
        root = Path("/tmp/library")
        item = AudioItem(
            path=root / "kick.wav",
            name="kick.wav",
            metadata=AudioMetadata(1.0, 44100, 2, bpm=None, key=None),
        )
        target = _sort_path_for_item(root, item)
        self.assertEqual(target, root / "wav" / "unknown-bpm")
