import unittest

from PyQt6 import QtCore

from birka.presentation.media_filter_proxy import MediaFilterProxyModel
from birka.presentation.media_presenter import MediaRow
from birka.presentation.media_table_model import MediaTableModel


class MetaFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QtCore.QCoreApplication.instance() is None:
            cls._app = QtCore.QCoreApplication([])

    def test_bpm_range_filter(self) -> None:
        rows = [
            MediaRow(path="/tmp/a.wav", name="a.wav", media_type="Audio", bpm="90", key="C", duration="", rating="", tags=""),
            MediaRow(path="/tmp/b.wav", name="b.wav", media_type="Audio", bpm="120", key="C", duration="", rating="", tags=""),
        ]
        model = MediaTableModel(rows)
        proxy = MediaFilterProxyModel()
        proxy.setSourceModel(model)

        proxy.set_bpm_range(80, 100)

        self.assertEqual(proxy.rowCount(), 1)
        index = proxy.index(0, 0)
        self.assertEqual(proxy.data(index, QtCore.Qt.ItemDataRole.DisplayRole), "a.wav")

    def test_key_filter(self) -> None:
        rows = [
            MediaRow(path="/tmp/a.wav", name="a.wav", media_type="Audio", bpm="90", key="C#m", duration="", rating="", tags=""),
            MediaRow(path="/tmp/b.wav", name="b.wav", media_type="Audio", bpm="90", key="Dm", duration="", rating="", tags=""),
        ]
        model = MediaTableModel(rows)
        proxy = MediaFilterProxyModel()
        proxy.setSourceModel(model)

        proxy.set_key_filter("c#m")

        self.assertEqual(proxy.rowCount(), 1)
        index = proxy.index(0, 0)
        self.assertEqual(proxy.data(index, QtCore.Qt.ItemDataRole.DisplayRole), "a.wav")

    def test_type_filter(self) -> None:
        rows = [
            MediaRow(path="/tmp/a.wav", name="a.wav", media_type="Audio", bpm="90", key="C", duration="", rating="", tags=""),
            MediaRow(path="/tmp/b.mid", name="b.mid", media_type="MIDI", bpm="90", key="C", duration="", rating="", tags=""),
        ]
        model = MediaTableModel(rows)
        proxy = MediaFilterProxyModel()
        proxy.setSourceModel(model)

        proxy.set_type_filter("midi")

        self.assertEqual(proxy.rowCount(), 1)
        index = proxy.index(0, 0)
        self.assertEqual(proxy.data(index, QtCore.Qt.ItemDataRole.DisplayRole), "b.mid")

    def test_unknown_bpm_filter(self) -> None:
        rows = [
            MediaRow(path="/tmp/a.wav", name="a.wav", media_type="Audio", bpm="", key="C", duration="", rating="", tags=""),
            MediaRow(path="/tmp/b.wav", name="b.wav", media_type="Audio", bpm="120", key="C", duration="", rating="", tags=""),
        ]
        model = MediaTableModel(rows)
        proxy = MediaFilterProxyModel()
        proxy.setSourceModel(model)

        proxy.set_bpm_range(80, 100)
        proxy.set_include_unknown_bpm(True)
        self.assertEqual(proxy.rowCount(), 0)

        proxy.set_bpm_range(0, 400)
        proxy.set_include_unknown_bpm(False)
        self.assertEqual(proxy.rowCount(), 1)

    def test_duration_range_filter(self) -> None:
        rows = [
            MediaRow(path="/tmp/a.wav", name="a.wav", media_type="Audio", bpm="90", key="C", duration="00:30", rating="", tags=""),
            MediaRow(path="/tmp/b.wav", name="b.wav", media_type="Audio", bpm="90", key="C", duration="01:10", rating="", tags=""),
        ]
        model = MediaTableModel(rows)
        proxy = MediaFilterProxyModel()
        proxy.setSourceModel(model)

        proxy.set_duration_range(0, 40)
        self.assertEqual(proxy.rowCount(), 1)
        index = proxy.index(0, 0)
        self.assertEqual(proxy.data(index, QtCore.Qt.ItemDataRole.DisplayRole), "a.wav")
