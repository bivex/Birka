from __future__ import annotations

from PyQt6 import QtCore


class MediaFilterProxyModel(QtCore.QSortFilterProxyModel):
    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._bpm_min = 0
        self._bpm_max = 400
        self._key = ""

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

    def filterAcceptsRow(self, source_row: int, source_parent: QtCore.QModelIndex) -> bool:  # noqa: N802
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
            if not (self._bpm_min <= 0 and self._bpm_max >= 400):
                return False

        if self._key:
            key_value = row_values[3].lower() if len(row_values) > 3 else ""
            if self._key not in key_value:
                return False

        return True


def _parse_bpm(value: str):  # noqa: ANN001
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
