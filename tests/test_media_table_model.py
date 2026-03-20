import unittest

from PyQt6 import QtCore

from birka.presentation.media_presenter import MediaRow
from birka.presentation.media_table_model import MediaTableModel


class MediaTableModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QtCore.QCoreApplication.instance() is None:
            cls._app = QtCore.QCoreApplication([])

    def test_model_exposes_rows_and_columns(self) -> None:
        rows = [
            MediaRow(name="clip.wav", media_type="Audio", bpm="", key="", duration="00:01", rating=""),
            MediaRow(name="pattern.mid", media_type="MIDI", bpm="120", key="Am", duration="", rating=""),
        ]
        model = MediaTableModel(rows)

        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.columnCount(), 6)

    def test_model_formats_cells(self) -> None:
        rows = [
            MediaRow(name="clip.wav", media_type="Audio", bpm="120.0", key="C#m", duration="01:05", rating="4")
        ]
        model = MediaTableModel(rows)
        index_duration = model.index(0, 4)
        index_bpm = model.index(0, 2)
        index_rating = model.index(0, 5)

        self.assertEqual(model.data(index_duration, QtCore.Qt.ItemDataRole.DisplayRole), "01:05")
        self.assertEqual(model.data(index_bpm, QtCore.Qt.ItemDataRole.DisplayRole), "120.0")
        self.assertEqual(model.data(index_rating, QtCore.Qt.ItemDataRole.DisplayRole), "4")

    def test_model_filters_rows(self) -> None:
        rows = [
            MediaRow(name="kick.wav", media_type="Audio", bpm="128", key="C", duration="00:01", rating=""),
            MediaRow(name="snare.wav", media_type="Audio", bpm="90", key="D", duration="00:01", rating=""),
        ]
        model = MediaTableModel(rows)

        model.set_filter("128")
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole), "kick.wav")

        model.set_filter("")
        self.assertEqual(model.rowCount(), 2)
