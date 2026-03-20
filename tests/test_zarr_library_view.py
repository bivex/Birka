import unittest
from pathlib import Path

from birka.domain.media import AudioItem, AudioMetadata, Rating
from birka.presentation.zarr_library_view import _build_zarr_hierarchy


class ZarrLibraryViewTests(unittest.TestCase):
    def test_builds_hierarchy_with_attrs(self) -> None:
        try:
            import zarr  # noqa: F401
        except Exception:  # noqa: BLE001
            self.skipTest("zarr not installed")

        root = Path("/tmp/library")
        item = AudioItem(
            path=root / "drums" / "kick.wav",
            name="kick.wav",
            metadata=AudioMetadata(1.0, 44100, 2, bpm=120.0, key="C"),
            rating=Rating(5),
            tags=("one-shot",),
        )

        zroot = _build_zarr_hierarchy(root, [item])
        dataset = zroot["drums"]["kick.wav"]

        self.assertEqual(dataset.attrs["bpm"], 120.0)
        self.assertEqual(dataset.attrs["key"], "C")
        self.assertEqual(dataset.attrs["rating"], 5)
        self.assertEqual(dataset.attrs["tags"], ["one-shot"])
