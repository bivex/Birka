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
            MediaRow(path="/tmp/clip.wav", name="clip.wav", media_type="Audio", bpm="", key="", duration="00:01", rating="", tags=""),
            MediaRow(path="/tmp/pattern.mid", name="pattern.mid", media_type="MIDI", bpm="120", key="Am", duration="", rating="", tags=""),
        ]
        model = MediaTableModel(rows)

        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.columnCount(), 7)

    def test_model_formats_cells(self) -> None:
        rows = [
            MediaRow(path="/tmp/clip.wav", name="clip.wav", media_type="Audio", bpm="120.0", key="C#m", duration="01:05", rating="4", tags=""),
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
            MediaRow(path="/tmp/kick.wav", name="kick.wav", media_type="Audio", bpm="128", key="C", duration="00:01", rating="", tags=""),
            MediaRow(path="/tmp/snare.wav", name="snare.wav", media_type="Audio", bpm="90", key="D", duration="00:01", rating="", tags=""),
        ]
        model = MediaTableModel(rows)

        model.set_filter("128")
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole), "kick.wav")

        model.set_filter("")
        self.assertEqual(model.rowCount(), 2)
