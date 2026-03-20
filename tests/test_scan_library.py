import tempfile
import unittest
import wave
from pathlib import Path

from birka.application.scan_library import ScanLibrary
from birka.infrastructure.file_scanner import FileSystemScanner
from birka.infrastructure.metadata_readers import AudioMidiMetadataReader


class ScanLibraryTests(unittest.TestCase):
    def test_scan_library_returns_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav_path = root / "clip.wav"
            with wave.open(str(wav_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(22050)
                wav.writeframes(b"\x00\x00" * 22050)

            scanner = FileSystemScanner([".wav", ".mid", ".midi"])
            reader = AudioMidiMetadataReader()
            use_case = ScanLibrary(scanner, reader)

            items = use_case.execute(root)

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].name, "clip.wav")
            self.assertEqual(items[0].metadata.sample_rate_hz, 22050)
