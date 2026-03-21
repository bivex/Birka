from __future__ import annotations

from dataclasses import dataclass
from typing import List

from PyQt6 import QtCore

from birka.presentation.media_presenter import MediaRow


class MediaTableModel(QtCore.QAbstractTableModel):
    _headers = ["Name", "Type", "BPM", "Key", "Duration", "Rating", "Tags"]

    def __init__(self, rows: List[MediaRow]) -> None:
        super().__init__()
        self._all_rows = list(rows)
        self._rows = list(rows)

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._headers)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole):  # noqa: ANN001
        if not index.isValid() or role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        row = self._rows[index.row()]
        return [
            row.name,
            row.media_type,
            row.bpm,
            row.key,
            row.duration,
            row.rating,
            row.tags,
        ][index.column()]

    def row_at(self, row: int) -> MediaRow:
        return self._rows[row]

    def headerData(self, section: int, orientation: QtCore.Qt.Orientation, role: int = QtCore.Qt.ItemDataRole.DisplayRole):  # noqa: ANN001
        if role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == QtCore.Qt.Orientation.Horizontal:
            return self._headers[section]
        return str(section + 1)

    def flags(self, index: QtCore.QModelIndex) -> QtCore.Qt.ItemFlags:  # noqa: N802
        base = super().flags(index)
        if not index.isValid():
            return base
        return base | QtCore.Qt.ItemFlag.ItemIsDragEnabled

    def set_filter(self, text: str) -> None:
        needle = text.strip().lower()
        self.beginResetModel()
        if not needle:
            self._rows = list(self._all_rows)
        else:
            self._rows = [
                row
                for row in self._all_rows
                if _row_matches(row, needle)
            ]
        self.endResetModel()


def _row_matches(row: MediaRow, needle: str) -> bool:
    haystack = " ".join(
        [row.name, row.media_type, row.bpm, row.key, row.duration, row.rating, row.tags]
    ).lower()
    return needle in haystack
