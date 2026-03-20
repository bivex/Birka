import tempfile
import unittest
import wave
from pathlib import Path

from birka.application.load_library import LoadLibrary
from birka.application.user_metadata import UserMetadata
from birka.domain.media import Rating
from birka.infrastructure.file_scanner import FileSystemScanner
from birka.infrastructure.metadata_readers import AudioMidiMetadataReader
from birka.infrastructure.json_user_metadata_store import JsonUserMetadataStore


class LoadLibraryTests(unittest.TestCase):
    def test_applies_user_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav_path = root / "clip.wav"
            with wave.open(str(wav_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(22050)
                wav.writeframes(b"\x00\x00" * 22050)

            meta_path = root / "meta.json"
            store = JsonUserMetadataStore(meta_path)
            store.save(wav_path, UserMetadata(rating=Rating(4), tags=["kick"]))

            loader = LoadLibrary(FileSystemScanner([".wav"]), AudioMidiMetadataReader(), store)
            items = loader.execute(root)

            self.assertEqual(items[0].rating.value, 4)
            self.assertEqual(items[0].tags, ("kick",))
