from __future__ import annotations

from PyQt6 import QtCore


class PaginationProxyModel(QtCore.QAbstractProxyModel):
    def __init__(self, page_size: int = 50, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._page_size = max(1, page_size)
        self._page_index = 0

    def setSourceModel(self, source_model: QtCore.QAbstractItemModel | None) -> None:  # noqa: N802
        if self.sourceModel() is not None:
            self.sourceModel().modelReset.disconnect(self._on_source_reset)
            self.sourceModel().layoutChanged.disconnect(self._on_source_reset)
        super().setSourceModel(source_model)
        if source_model is not None:
            source_model.modelReset.connect(self._on_source_reset)
            source_model.layoutChanged.connect(self._on_source_reset)
        self._page_index = 0
        self.invalidate()

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # noqa: N802
        if parent.isValid() or self.sourceModel() is None:
            return 0
        total = self.sourceModel().rowCount()
        offset = self._page_index * self._page_size
        return max(0, min(self._page_size, total - offset))

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # noqa: N802
        if parent.isValid() or self.sourceModel() is None:
            return 0
        return self.sourceModel().columnCount()

    def index(self, row: int, column: int, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> QtCore.QModelIndex:
        if self.sourceModel() is None or parent.isValid():
            return QtCore.QModelIndex()
        return self.createIndex(row, column)

    def parent(self, index: QtCore.QModelIndex) -> QtCore.QModelIndex:
        return QtCore.QModelIndex()

    def mapToSource(self, proxy_index: QtCore.QModelIndex) -> QtCore.QModelIndex:
        if not proxy_index.isValid() or self.sourceModel() is None:
            return QtCore.QModelIndex()
        source_row = proxy_index.row() + self._page_index * self._page_size
        return self.sourceModel().index(source_row, proxy_index.column())

    def mapFromSource(self, source_index: QtCore.QModelIndex) -> QtCore.QModelIndex:
        if not source_index.isValid():
            return QtCore.QModelIndex()
        offset = self._page_index * self._page_size
        row = source_index.row() - offset
        if row < 0 or row >= self._page_size:
            return QtCore.QModelIndex()
        return self.index(row, source_index.column())

    def set_page_size(self, size: int) -> None:
        self._page_size = max(1, size)
        self._page_index = 0
        self.invalidate()

    def set_page_index(self, index: int) -> None:
        self._page_index = max(0, index)
        self.invalidate()

    def page_index(self) -> int:
        return self._page_index

    def page_count(self) -> int:
        if self.sourceModel() is None:
            return 1
        total = self.sourceModel().rowCount()
        return max(1, (total + self._page_size - 1) // self._page_size)

    def sort(self, column: int, order: QtCore.Qt.SortOrder = QtCore.Qt.SortOrder.AscendingOrder) -> None:
        if self.sourceModel() is not None:
            self.sourceModel().sort(column, order)

    def invalidate(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def _on_source_reset(self) -> None:
        self._page_index = 0
        self.invalidate()
