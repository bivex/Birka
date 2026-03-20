import tempfile
import unittest
from pathlib import Path

from birka.application.user_metadata import UserMetadata
from birka.domain.media import Rating
from birka.infrastructure.json_user_metadata_store import JsonUserMetadataStore


class UserMetadataStoreTests(unittest.TestCase):
    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meta.json"
            store = JsonUserMetadataStore(path)
            store.save(Path("/tmp/clip.wav"), UserMetadata(rating=Rating(3), tags=["kick", "dry"]))

            loaded = store.load_all()

            self.assertIn(Path("/tmp/clip.wav"), loaded)
            self.assertEqual(loaded[Path("/tmp/clip.wav")].rating.value, 3)
            self.assertEqual(loaded[Path("/tmp/clip.wav")].tags, ["kick", "dry"])

    def test_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meta.json"
            store = JsonUserMetadataStore(path)
            target = Path("/tmp/clip.wav")
            store.save(target, UserMetadata(rating=Rating(2), tags=["fx"]))

            store.delete(target)

            loaded = store.load_all()
            self.assertNotIn(target, loaded)
