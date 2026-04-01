from __future__ import annotations

from PyQt6 import QtCore


class MediaFilterProxyModel(QtCore.QSortFilterProxyModel):
    BPM_MAX_DEFAULT = 400
    DURATION_MAX_DEFAULT = 36000

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._bpm_min = 0
        self._bpm_max = self.BPM_MAX_DEFAULT
        self._key = ""
        self._type = ""
        self._include_unknown_bpm = True
        self._duration_min = 0
        self._duration_max = self.DURATION_MAX_DEFAULT

    def set_text_filter(self, text: str) -> None:
        self._text = text.strip().lower()
        self.invalidateFilter()

    def set_bpm_range(self, bpm_min: int, bpm_max: int) -> None:
        self._bpm_min = bpm_min
        self._bpm_max = bpm_max
        self.invalidateFilter()

    def set_key_filter(self, key: str) -> None:
        self._key = key.strip().lower()
        self.invalidateFilter()

    def set_type_filter(self, media_type: str) -> None:
        self._type = media_type.strip().lower()
        self.invalidateFilter()

    def set_include_unknown_bpm(self, value: bool) -> None:
        self._include_unknown_bpm = value
        self.invalidateFilter()

    def set_duration_range(self, min_seconds: int, max_seconds: int) -> None:
        self._duration_min = min_seconds
        self._duration_max = max_seconds
        self.invalidateFilter()

    def filterAcceptsRow(
        self, source_row: int, source_parent: QtCore.QModelIndex
    ) -> bool:  # noqa: N802
        model = self.sourceModel()
        if model is None:
            return True
        row_values = []
        for col in range(model.columnCount()):
            idx = model.index(source_row, col, source_parent)
            value = model.data(idx, QtCore.Qt.ItemDataRole.DisplayRole)
            row_values.append(str(value) if value is not None else "")
        text_blob = " ".join(row_values).lower()

        if self._text and self._text not in text_blob:
            return False

        bpm_value = _parse_bpm(row_values[2]) if len(row_values) > 2 else None
        if bpm_value is not None:
            if bpm_value < self._bpm_min or bpm_value > self._bpm_max:
                return False
        else:
            if not self._include_unknown_bpm or not (
                self._bpm_min <= 0 and self._bpm_max >= self.BPM_MAX_DEFAULT
            ):
                return False

        if self._key:
            key_value = row_values[3].lower() if len(row_values) > 3 else ""
            if self._key not in key_value:
                return False

        if self._type:
            type_value = row_values[1].lower() if len(row_values) > 1 else ""
            if self._type not in type_value:
                return False

        duration_value = _parse_duration(row_values[4]) if len(row_values) > 4 else None
        if duration_value is not None:
            if (
                duration_value < self._duration_min
                or duration_value > self._duration_max
            ):
                return False
        else:
            if not (
                self._duration_min <= 0
                and self._duration_max >= self.DURATION_MAX_DEFAULT
            ):
                return False

        return True


def _parse_bpm(value: str):  # noqa: ANN001
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_duration(value: str) -> int | None:
    if not value:
        return None
    if ":" not in value:
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    try:
        minutes = int(parts[0])
        seconds = int(parts[1])
    except ValueError:
        return None
    return minutes * 60 + seconds
