from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from birka.domain.media import MediaItem


@dataclass(frozen=True)
class RenameEntry:
    path: Path
    new_name: str


class RenameTemplate:
    def __init__(self, template: str) -> None:
        self._template = template

    def render(self, item: MediaItem) -> str:
        name = item.name
        stem = Path(name).stem
        suffix = Path(name).suffix
        bpm = _string_or_empty(getattr(item, "metadata", None), "bpm")
        key = _string_or_empty(getattr(item, "metadata", None), "key")
        result = (
            self._template
            .replace("[BPM]", bpm)
            .replace("[Key]", key)
            .replace("[OriginalName]", stem)
        )
        return f"{result}{suffix}"


class BuildRenamePlan:
    def __init__(self, template: RenameTemplate) -> None:
        self._template = template

    def execute(self, items: Iterable[MediaItem]) -> List[RenameEntry]:
        entries: List[RenameEntry] = []
        for item in items:
            entries.append(RenameEntry(path=item.path, new_name=self._template.render(item)))
        return entries


class FileRenamer:
    def rename(self, entries: Iterable[RenameEntry]) -> None:
        for entry in entries:
            entry.path.rename(entry.path.with_name(entry.new_name))


def _string_or_empty(metadata, attr: str) -> str:  # noqa: ANN001
    if metadata is None:
        return ""
    value = getattr(metadata, attr, None)
    if value is None:
        return ""
    return str(value)
