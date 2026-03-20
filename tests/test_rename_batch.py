import unittest
from pathlib import Path

from birka.application.rename_batch import BuildRenamePlan, RenameTemplate
from birka.domain.media import AudioItem, AudioMetadata


class RenameBatchTests(unittest.TestCase):
    def test_builds_rename_plan(self) -> None:
        item = AudioItem(
            path=Path("/tmp/clip.wav"),
            name="clip.wav",
            metadata=AudioMetadata(1.0, 44100, 2, bpm=120.0, key="C#m"),
        )
        template = RenameTemplate("[BPM]_[Key]_[OriginalName]")
        plan = BuildRenamePlan(template).execute([item])

        self.assertEqual(plan[0].new_name, "120.0_C#m_clip.wav")

    def test_template_handles_missing_metadata(self) -> None:
        item = AudioItem(
            path=Path("/tmp/clip.wav"),
            name="clip.wav",
            metadata=AudioMetadata(1.0, 44100, 2, bpm=None, key=None),
        )
        template = RenameTemplate("[BPM]_[Key]_[OriginalName]")
        plan = BuildRenamePlan(template).execute([item])

        self.assertEqual(plan[0].new_name, "__clip.wav")
