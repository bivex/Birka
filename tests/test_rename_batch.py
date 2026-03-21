import tempfile
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

        self.assertEqual(plan.entries[0].new_name, "120.0_C#m_clip.wav")
        self.assertEqual(plan.conflicts, [])

    def test_template_handles_missing_metadata(self) -> None:
        item = AudioItem(
            path=Path("/tmp/clip.wav"),
            name="clip.wav",
            metadata=AudioMetadata(1.0, 44100, 2, bpm=None, key=None),
        )
        template = RenameTemplate("[BPM]_[Key]_[OriginalName]")
        plan = BuildRenamePlan(template).execute([item])

        self.assertEqual(plan.entries[0].new_name, "__clip.wav")

    def test_conflict_on_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "120.0_kick.wav"
            existing.write_text("x")
            item = AudioItem(
                path=root / "kick.wav",
                name="kick.wav",
                metadata=AudioMetadata(1.0, 44100, 2, bpm=120.0, key=""),
            )
            template = RenameTemplate("[BPM]_[OriginalName]")
            plan = BuildRenamePlan(template).execute([item])

            self.assertEqual(len(plan.entries), 0)
            self.assertEqual(len(plan.conflicts), 1)
            self.assertEqual(plan.conflicts[0].reason, "Target exists")

    def test_conflict_on_duplicate_target(self) -> None:
        item_a = AudioItem(
            path=Path("/tmp/a.wav"),
            name="a.wav",
            metadata=AudioMetadata(1.0, 44100, 2, bpm=90.0, key=""),
        )
        item_b = AudioItem(
            path=Path("/tmp/b.wav"),
            name="b.wav",
            metadata=AudioMetadata(1.0, 44100, 2, bpm=90.0, key=""),
        )
        template = RenameTemplate("[BPM].wav")
        plan = BuildRenamePlan(template).execute([item_a, item_b])

        self.assertEqual(len(plan.entries), 1)
        self.assertEqual(len(plan.conflicts), 1)
        self.assertEqual(plan.conflicts[0].reason, "Duplicate target in batch")
