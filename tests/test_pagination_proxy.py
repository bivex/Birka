import unittest

from PyQt6 import QtCore, QtGui

from birka.presentation.pagination_proxy import PaginationProxyModel


class PaginationProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QtCore.QCoreApplication.instance() is None:
            cls._app = QtCore.QCoreApplication([])

    def test_pagination_window(self) -> None:
        model = QtGui.QStandardItemModel(10, 1)
        for row in range(10):
            model.setItem(row, 0, QtGui.QStandardItem(str(row)))

        proxy = PaginationProxyModel(page_size=4)
        proxy.setSourceModel(model)

        self.assertEqual(proxy.rowCount(), 4)
        self.assertEqual(proxy.page_count(), 3)

        proxy.set_page_index(2)
        self.assertEqual(proxy.rowCount(), 2)

        index = proxy.index(0, 0)
        source = proxy.mapToSource(index)
        self.assertEqual(source.row(), 8)
