from __future__ import annotations

from PyQt6 import QtWidgets

from birka.domain.media import MediaItem
from birka.presentation.media_presenter import MediaPresenter
from birka.presentation.media_table_model import MediaTableModel


class AudioBrowserWindow(QtWidgets.QMainWindow):
    def __init__(self, items: list[MediaItem]) -> None:
        super().__init__()
        self.setWindowTitle("Birka Audio Browser")
        self.setMinimumSize(900, 600)

        presenter = MediaPresenter()
        model = MediaTableModel(presenter.to_rows(items))

        search = QtWidgets.QLineEdit(self)
        search.setPlaceholderText("Search by name, type, BPM, key...")
        search.textChanged.connect(model.set_filter)

        table = QtWidgets.QTableView(self)
        table.setModel(model)
        table.horizontalHeader().setStretchLastSection(True)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)

        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)
        layout.addWidget(search)
        layout.addWidget(table)
        self.setCentralWidget(container)
