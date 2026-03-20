from __future__ import annotations

from pathlib import Path
from typing import List

from PyQt6 import QtCore, QtMultimedia, QtWidgets

from birka.application.load_library import LoadLibrary
from birka.application.user_metadata import UserMetadata, UserMetadataStore
from birka.domain.media import MediaItem, Rating
from birka.infrastructure.file_scanner import FileSystemScanner
from birka.infrastructure.metadata_readers import AudioMidiMetadataReader
from birka.infrastructure.waveform_provider import WaveformProvider
from birka.presentation.media_presenter import MediaPresenter
from birka.presentation.media_table_model import MediaTableModel
from birka.presentation.pagination_proxy import PaginationProxyModel
from birka.presentation.rename_dialog import RenameCoordinator
from birka.presentation.waveform_widget import WaveformWidget


class LibraryTab(QtWidgets.QWidget):
    def __init__(self, root: Path, metadata_store: UserMetadataStore, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = root
        self._metadata_store = metadata_store
        self._scanner = FileSystemScanner([".wav", ".mid", ".midi"])
        self._reader = AudioMidiMetadataReader()
        self._loader = LoadLibrary(self._scanner, self._reader, self._metadata_store)
        self._waveform_provider = WaveformProvider()
        self._presenter = MediaPresenter()
        self._rename = RenameCoordinator(self)

        self._items: List[MediaItem] = []
        self._item_by_path: dict[str, MediaItem] = {}
        self._selection_connected = False

        self._player = QtMultimedia.QMediaPlayer(self)
        self._audio_output = QtMultimedia.QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)

        self._build_ui()
        self.reload()

    def reload(self) -> None:
        self._items = self._loader.execute(self.root)
        self._item_by_path = {str(item.path): item for item in self._items}
        self._model = MediaTableModel(self._presenter.to_rows(self._items))
        self._filter.setSourceModel(self._model)
        self._table.setModel(self._pager)
        self._table.resizeColumnsToContents()
        self._update_page_label()
        if self._selection_connected:
            try:
                self._table.selectionModel().selectionChanged.disconnect(self._on_selection_changed)
            except TypeError:
                pass
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._selection_connected = True

    def _build_ui(self) -> None:
        self._filter = QtCore.QSortFilterProxyModel(self)
        self._filter.setFilterKeyColumn(-1)
        self._filter.setFilterCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        self._filter.modelReset.connect(self._update_page_label)
        self._filter.layoutChanged.connect(self._update_page_label)
        self._pager = PaginationProxyModel(page_size=50, parent=self)
        self._pager.setSourceModel(self._filter)

        self._search = QtWidgets.QLineEdit(self)
        self._search.setPlaceholderText("Search by name, type, BPM, key, tags...")
        self._search.textChanged.connect(self._filter.setFilterFixedString)

        self._table = QtWidgets.QTableView(self)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().sortIndicatorChanged.connect(self._pager.sort)


        self._waveform = WaveformWidget(self)
        play_button = QtWidgets.QPushButton("Play", self)
        stop_button = QtWidgets.QPushButton("Stop", self)
        play_button.clicked.connect(self._play_selected)
        stop_button.clicked.connect(self._player.stop)

        controls_row = QtWidgets.QHBoxLayout()
        controls_row.addWidget(play_button)
        controls_row.addWidget(stop_button)
        controls_row.addStretch(1)

        self._template_input = QtWidgets.QLineEdit(self)
        self._template_input.setPlaceholderText("Rename template: [BPM]_[Key]_[OriginalName]")
        self._template_input.setText("[BPM]_[Key]_[OriginalName]")
        rename_button = QtWidgets.QPushButton("Preview Rename", self)
        rename_button.clicked.connect(self._preview_rename)

        rename_row = QtWidgets.QHBoxLayout()
        rename_row.addWidget(self._template_input)
        rename_row.addWidget(rename_button)

        self._tags_input = QtWidgets.QLineEdit(self)
        self._tags_input.setPlaceholderText("Tags (comma separated)")
        self._rating_combo = QtWidgets.QComboBox(self)
        self._rating_combo.addItem("", None)
        for value in range(0, 6):
            self._rating_combo.addItem(str(value), value)
        apply_button = QtWidgets.QPushButton("Apply Tags/Rating", self)
        apply_button.clicked.connect(self._apply_tags_rating)

        tags_row = QtWidgets.QHBoxLayout()
        tags_row.addWidget(self._tags_input)
        tags_row.addWidget(self._rating_combo)
        tags_row.addWidget(apply_button)

        delete_button = QtWidgets.QPushButton("Delete Selected", self)
        delete_button.clicked.connect(self._delete_selected)

        pager_row = QtWidgets.QHBoxLayout()
        self._page_label = QtWidgets.QLabel("Page 1/1", self)
        self._page_size = QtWidgets.QComboBox(self)
        for size in (25, 50, 100, 200):
            self._page_size.addItem(str(size), size)
        self._page_size.setCurrentText("50")
        self._page_size.currentIndexChanged.connect(self._on_page_size_changed)
        prev_button = QtWidgets.QPushButton("Prev", self)
        next_button = QtWidgets.QPushButton("Next", self)
        prev_button.clicked.connect(self._prev_page)
        next_button.clicked.connect(self._next_page)
        pager_row.addWidget(prev_button)
        pager_row.addWidget(next_button)
        pager_row.addWidget(self._page_label)
        pager_row.addStretch(1)
        pager_row.addWidget(QtWidgets.QLabel("Page size", self))
        pager_row.addWidget(self._page_size)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._search)
        layout.addLayout(rename_row)
        layout.addWidget(self._table)
        layout.addWidget(self._waveform)
        layout.addLayout(controls_row)
        layout.addLayout(tags_row)
        layout.addWidget(delete_button)
        layout.addLayout(pager_row)

    def _on_selection_changed(self) -> None:
        item = self._first_selected_item()
        if item is None:
            self._waveform.set_samples([])
            return
        samples = self._waveform_provider.load(item.path)
        self._waveform.set_samples(samples)

    def _first_selected_item(self) -> MediaItem | None:
        selection = self._pager.mapSelectionToSource(self._table.selectionModel().selection())
        indexes = selection.indexes()
        if not indexes:
            return None
        filter_index = indexes[0]
        source_index = self._filter.mapToSource(filter_index)
        media_row = self._model.row_at(source_index.row())
        return self._item_by_path.get(media_row.path)

    def _play_selected(self) -> None:
        item = self._first_selected_item()
        if item is None:
            QtWidgets.QMessageBox.information(self, "Play", "Select a file first.")
            return
        url = QtCore.QUrl.fromLocalFile(str(item.path))
        self._player.setSource(url)
        self._player.play()

    def _preview_rename(self) -> None:
        items = self._selected_items()
        if not items:
            QtWidgets.QMessageBox.information(self, "Rename", "Select one or more rows to rename.")
            return
        template = self._template_input.text()
        self._rename.preview_and_apply(items, template)
        self.reload()

    def _selected_items(self) -> List[MediaItem]:
        selection = self._pager.mapSelectionToSource(self._table.selectionModel().selection())
        indexes = selection.indexes()
        if not indexes:
            return []
        rows = {self._filter.mapToSource(index).row() for index in indexes}
        items = []
        for row in rows:
            media_row = self._model.row_at(row)
            item = self._item_by_path.get(media_row.path)
            if item is not None:
                items.append(item)
        return items

    def _apply_tags_rating(self) -> None:
        items = self._selected_items()
        if not items:
            QtWidgets.QMessageBox.information(self, "Tags", "Select one or more rows.")
            return
        tags = [tag.strip() for tag in self._tags_input.text().split(",") if tag.strip()]
        rating_value = self._rating_combo.currentData()
        rating = Rating(rating_value) if rating_value is not None else None
        for item in items:
            self._metadata_store.save(item.path, UserMetadata(rating=rating, tags=tags))
        self.reload()

    def _delete_selected(self) -> None:
        items = self._selected_items()
        if not items:
            QtWidgets.QMessageBox.information(self, "Delete", "Select one or more rows.")
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Delete",
            f"Delete {len(items)} file(s) from disk?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        failures = []
        for item in items:
            try:
                item.path.unlink()
                self._metadata_store.delete(item.path)
            except OSError as exc:
                failures.append(f"{item.path.name}: {exc}")
        if failures:
            QtWidgets.QMessageBox.warning(self, "Delete", "Some files могли не удалиться:\n" + "\n".join(failures))
        self.reload()

    def _on_page_size_changed(self) -> None:
        size = self._page_size.currentData()
        if isinstance(size, int):
            self._pager.set_page_size(size)
            self._update_page_label()

    def _prev_page(self) -> None:
        if self._pager.page_index() > 0:
            self._pager.set_page_index(self._pager.page_index() - 1)
            self._update_page_label()

    def _next_page(self) -> None:
        if self._pager.page_index() + 1 < self._pager.page_count():
            self._pager.set_page_index(self._pager.page_index() + 1)
            self._update_page_label()

    def _update_page_label(self) -> None:
        current = self._pager.page_index() + 1
        total = self._pager.page_count()
        self._page_label.setText(f"Page {current}/{total}")
