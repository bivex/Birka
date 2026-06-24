from __future__ import annotations

from dataclasses import dataclass
from typing import List

from PyQt6 import QtCore, QtGui

from birka.presentation.media_presenter import MediaRow


# Column indices (keep in sync with _headers).
_COL_NAME = 0
_COL_TYPE = 1
_COL_BPM = 2
_COL_KEY = 3
_COL_DURATION = 4
_COL_RATING = 5
_COL_TAGS = 6
_COL_CREATED = 7
_COL_MODIFIED = 8

# Per-type emoji prefix + accent colour, shown in the Type column so the kind of
# file reads at a glance instead of parsing the word "Audio"/"MIDI". Extra
# spacing after the icon gives it room to breathe (feedback: icons too small).
_TYPE_ICON = {
    "Audio": "🎵",
    "MIDI": "🎹",
    "Unknown": "❓",
}
_TYPE_COLOR = {
    "Audio": "#00ffaa",   # emerald (matches Play button accent)
    "MIDI": "#ff66cc",    # pink
    "Unknown": "#8a8a9a", # muted grey
}

# Visual hierarchy colours: primary (Name) bright, secondary (timestamps) dim.
_PRIMARY_COLOR = "#f2f2f7"
_DIM_COLOR = "#6c6c7a"
_PLACEHOLDER_COLOR = "#3c3c46"

# Columns de-emphasised as "secondary" metadata.
_SECONDARY_COLS = {_COL_CREATED, _COL_MODIFIED}

# Columns whose values are numeric/short codes → right/centre aligned so they
# line up in neat columns (much easier to scan than left-ragged numbers).
_RIGHT_ALIGNED = {_COL_BPM, _COL_DURATION}
_CENTER_ALIGNED = {_COL_TYPE, _COL_KEY, _COL_RATING}


def _stars(rating: str) -> str:
    """Render a 0-5 numeric rating as filled/empty stars (★★★☆☆).

    Falls back to the raw string if it isn't an int in range.
    """
    try:
        n = int(rating)
    except (TypeError, ValueError):
        return ""
    n = max(0, min(5, n))
    return "★" * n + "☆" * (5 - n)


class MediaTableModel(QtCore.QAbstractTableModel):
    _headers = [
        "Name", "Type", "BPM", "Key", "Duration",
        "Rating", "Tags", "Created", "Modified",
    ]

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

    def _value(self, row: MediaRow, col: int):
        return [
            row.name,
            row.media_type,
            row.bpm,
            row.key,
            row.duration,
            row.rating,
            row.tags,
            row.created,
            row.modified,
        ][col]

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole):  # noqa: ANN001
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        Role = QtCore.Qt.ItemDataRole

        if role == Role.DisplayRole:
            if col == _COL_TYPE:
                # Two spaces after the icon so it reads bigger / less cramped.
                icon = _TYPE_ICON.get(row.media_type, "")
                return f"{icon}  {row.media_type}".strip()
            if col == _COL_RATING:
                return _stars(row.rating)
            if col == _COL_TAGS:
                return row.tags if row.tags else "—"  # placeholder for empty
            return self._value(row, col)

        # Raw underlying value for filtering/sorting (the proxy reads this so the
        # decorative DisplayRole — stars, emoji — never corrupts numeric sort or
        # text search).
        if role == Role.UserRole:
            return self._value(row, col)

        # Right/centre alignment for numeric and code columns.
        if role == Role.TextAlignmentRole:
            if col in _RIGHT_ALIGNED:
                return int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            if col in _CENTER_ALIGNED:
                return int(QtCore.Qt.AlignmentFlag.AlignCenter)
            return int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)

        # Visual hierarchy via font weight/size: Name is the primary anchor
        # (bold), the secondary timestamp columns are smaller so the eye skips
        # over them. Everything else uses the default font.
        if role == Role.FontRole:
            if col == _COL_NAME:
                f = QtGui.QFont()
                f.setBold(True)
                return f
            if col in _SECONDARY_COLS:
                f = QtGui.QFont()
                f.setPointSize(max(8, f.pointSize() - 1))
                return f
            return None

        # Colour hierarchy: Name bright, timestamps dim, empty-tag placeholder
        # very dim, Type accent-coloured, rating gold.
        if role == Role.ForegroundRole:
            if col == _COL_TYPE:
                return QtGui.QColor(_TYPE_COLOR.get(row.media_type, "#e3e3e8"))
            if col == _COL_RATING and _stars(row.rating):
                return QtGui.QColor("#ffcc00")  # gold stars
            if col == _COL_NAME:
                return QtGui.QColor(_PRIMARY_COLOR)
            if col in _SECONDARY_COLS:
                return QtGui.QColor(_DIM_COLOR)
            if col == _COL_TAGS and not row.tags:
                return QtGui.QColor(_PLACEHOLDER_COLOR)
            return None

        # Tooltips: full path on the name, full timestamps (the cells show the
        # compact "2d ago" / "24 Jun, 22:42" form), full tags.
        if role == Role.ToolTipRole:
            if col == _COL_NAME:
                return row.path
            if col == _COL_TAGS and row.tags:
                return row.tags
            if col == _COL_CREATED and row.created_full:
                return f"Created: {row.created_full}"
            if col == _COL_MODIFIED and row.modified_full:
                return f"Modified: {row.modified_full}"
            return None

        return None

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
        [row.name, row.media_type, row.bpm, row.key, row.duration, row.rating, row.tags, row.created, row.modified]
    ).lower()
    return needle in haystack
