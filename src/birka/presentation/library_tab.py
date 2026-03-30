from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

from PyQt6 import QtCore, QtGui, QtMultimedia, QtWidgets

from birka.application.load_library import LoadLibrary
from birka.application.user_metadata import UserMetadata, UserMetadataStore
from birka.domain.media import MediaItem, Rating
from birka.infrastructure.file_scanner import FileSystemScanner
from birka.infrastructure.metadata_readers import AudioMidiMetadataReader
from birka.infrastructure.midi_renderer import render_midi_to_mp3
from birka.infrastructure.waveform_provider import WaveformProvider
from birka.presentation.media_presenter import MediaPresenter
from birka.presentation.file_drag_table import FileDragTableView
from birka.presentation.media_filter_proxy import MediaFilterProxyModel
from birka.presentation.media_table_model import MediaTableModel
from birka.presentation.pagination_proxy import PaginationProxyModel
from birka.presentation.rename_dialog import RenameCoordinator
from birka.presentation.waveform_widget import WaveformWidget
from birka.presentation.zarr_library_view import ZarrLibraryView


def _render_midi_to_tmp_wav(midi_path: Path) -> Path | None:
    """Render MIDI to a temporary WAV file using fluidsynth."""
    from birka.infrastructure.midi_renderer import _find_soundfont
    soundfont = _find_soundfont()
    if soundfont is None:
        return None
    if shutil.which("fluidsynth") is None:
        return None
    tmp_dir = Path(tempfile.mkdtemp(prefix="birka_midi_"))
    wav_path = tmp_dir / (midi_path.stem + ".wav")
    cmd = [
        "fluidsynth", "-i", "-ni", "-g", "0.8",
        "-F", str(wav_path),
        str(soundfont), str(midi_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not wav_path.exists():
        wav_path.unlink(missing_ok=True)
        return None
    return wav_path


class _RefreshWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(list)

    def __init__(self, root: Path, metadata_store: UserMetadataStore) -> None:
        super().__init__()
        self._root = root
        self._metadata_store = metadata_store

    def run(self) -> None:
        scanner = FileSystemScanner([".wav", ".mid", ".midi"])
        reader = AudioMidiMetadataReader()
        loader = LoadLibrary(scanner, reader, self._metadata_store)
        items = loader.execute(self._root)
        self.finished.emit(items)


class LibraryTab(QtWidgets.QWidget):
    folder_opened = QtCore.pyqtSignal(Path)

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
        self._zarr_view: ZarrLibraryView | None = None
        self._tmp_midi_wav: Path | None = None
        self._seeking = False

        self._player = QtMultimedia.QMediaPlayer(self)
        self._audio_output = QtMultimedia.QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status)

        self._refresh_thread: QtCore.QThread | None = None
        self._refresh_worker: _RefreshWorker | None = None
        self._first_load = True

        self._build_ui()
        self.reload()

        self._auto_refresh_timer = QtCore.QTimer(self)
        self._auto_refresh_timer.timeout.connect(self.reload)
        self._auto_refresh_timer.start(10_000)

    def reload(self) -> None:
        if self._refresh_thread is not None:
            return
        worker = _RefreshWorker(self.root, self._metadata_store)
        thread = QtCore.QThread()
        worker.moveToThread(thread)
        worker.finished.connect(self._apply_refresh)
        thread.started.connect(worker.run)
        self._refresh_thread = thread
        self._refresh_worker = worker
        thread.start()

    def _apply_refresh(self, items: List[MediaItem]) -> None:
        old_paths: set[str] = set()
        if not self._first_load:
            selection = self._pager.mapSelectionToSource(self._table.selectionModel().selection())
            old_paths = {self._model.row_at(i.row()).path for i in selection.indexes()} if selection.indexes() else set()

        self._items = items
        self._item_by_path = {str(item.path): item for item in items}
        self._model = MediaTableModel(self._presenter.to_rows(items))
        self._filter.setSourceModel(self._model)
        self._table.setModel(self._pager)

        if not self._first_load:
            self._restore_selection(old_paths)

        self._table.resizeColumnsToContents()
        self._update_page_label()
        self._update_count_label()
        if self._zarr_view is not None:
            self._zarr_view.set_items(items)

        sel_model = self._table.selectionModel()
        if sel_model is not None:
            if self._selection_connected:
                try:
                    sel_model.selectionChanged.disconnect(self._on_selection_changed)
                except TypeError:
                    pass
            sel_model.selectionChanged.connect(self._on_selection_changed)
            self._selection_connected = True
        self._first_load = False

        if self._refresh_thread is not None:
            self._refresh_thread.quit()
            self._refresh_thread.wait()
            self._refresh_thread = None
            self._refresh_worker = None

    def _restore_selection(self, old_paths: set[str]) -> None:
        if not old_paths:
            return
        new_rows = set()
        for row in range(self._model.rowCount()):
            if self._model.row_at(row).path in old_paths:
                new_rows.add(row)
        if not new_rows:
            return
        selection = QtCore.QItemSelection()
        for row in new_rows:
            for col in range(self._filter.columnCount()):
                src_idx = self._model.index(row, col)
                filter_idx = self._filter.mapFromSource(src_idx)
                pager_idx = self._pager.mapFromSource(filter_idx)
                if pager_idx.isValid():
                    selection.select(pager_idx, pager_idx)
        if selection.indexes():
            if self._selection_connected:
                try:
                    self._table.selectionModel().selectionChanged.disconnect(self._on_selection_changed)
                except TypeError:
                    pass
            self._table.selectionModel().select(selection, QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows)
            self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

    def stop_all(self) -> None:
        self._player.stop()
        self._cleanup_tmp_wav()

    def _cleanup_tmp_wav(self) -> None:
        if self._tmp_midi_wav is not None:
            try:
                self._tmp_midi_wav.unlink()
                self._tmp_midi_wav.parent.rmdir()
            except OSError:
                pass
            self._tmp_midi_wav = None

    def _build_ui(self) -> None:
        self._filter = MediaFilterProxyModel(self)
        self._filter.modelReset.connect(self._update_page_label)
        self._filter.layoutChanged.connect(self._update_page_label)
        self._filter.modelReset.connect(self._update_count_label)
        self._filter.layoutChanged.connect(self._update_count_label)
        self._pager = PaginationProxyModel(page_size=50, parent=self)
        self._pager.setSourceModel(self._filter)

        self._search = QtWidgets.QLineEdit(self)
        self._search.setPlaceholderText("Search by name, type, BPM, key, tags...")
        self._search.textChanged.connect(self._filter.set_text_filter)

        self._bpm_min = QtWidgets.QSpinBox(self)
        self._bpm_min.setRange(0, 400)
        self._bpm_min.setPrefix("BPM min: ")
        self._bpm_min.valueChanged.connect(self._apply_meta_filters)
        self._bpm_max = QtWidgets.QSpinBox(self)
        self._bpm_max.setRange(0, 400)
        self._bpm_max.setPrefix("BPM max: ")
        self._bpm_max.setValue(400)
        self._bpm_max.valueChanged.connect(self._apply_meta_filters)
        self._key_filter = QtWidgets.QLineEdit(self)
        self._key_filter.setPlaceholderText("Key (e.g., C#m)")
        self._key_filter.textChanged.connect(self._apply_meta_filters)

        self._type_filter = QtWidgets.QComboBox(self)
        self._type_filter.addItem("All", "")
        self._type_filter.addItem("Audio", "audio")
        self._type_filter.addItem("MIDI", "midi")
        self._type_filter.currentIndexChanged.connect(self._apply_meta_filters)

        self._include_unknown_bpm = QtWidgets.QCheckBox("Include unknown BPM", self)
        self._include_unknown_bpm.setChecked(True)
        self._include_unknown_bpm.stateChanged.connect(self._apply_meta_filters)

        self._duration_min = QtWidgets.QSpinBox(self)
        self._duration_min.setRange(0, 3600)
        self._duration_min.setPrefix("Dur min: ")
        self._duration_min.setSuffix("s")
        self._duration_min.valueChanged.connect(self._apply_meta_filters)
        self._duration_max = QtWidgets.QSpinBox(self)
        self._duration_max.setRange(0, 3600)
        self._duration_max.setPrefix("Dur max: ")
        self._duration_max.setSuffix("s")
        self._duration_max.setValue(3600)
        self._duration_max.valueChanged.connect(self._apply_meta_filters)

        self._table = FileDragTableView(self._selected_paths, self)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().sortIndicatorChanged.connect(self._pager.sort)
        self._table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)


        self._waveform = WaveformWidget(self)
        self._waveform.position_changed.connect(self._waveform_seek)
        play_button = QtWidgets.QPushButton("Play", self)
        stop_button = QtWidgets.QPushButton("Stop", self)
        play_button.clicked.connect(self._play_selected)
        stop_button.clicked.connect(self._stop_playback)

        self._seek_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, self)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.sliderPressed.connect(self._seek_started)
        self._seek_slider.sliderReleased.connect(self._seek_finished)
        self._seek_slider.sliderMoved.connect(self._seek_moved)

        self._time_label = QtWidgets.QLabel("0:00 / 0:00", self)

        volume_label = QtWidgets.QLabel("Vol", self)
        self._volume_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, self)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(100)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._audio_output.setVolume(0.8)

        controls_row = QtWidgets.QHBoxLayout()
        controls_row.addWidget(play_button)
        controls_row.addWidget(stop_button)
        controls_row.addWidget(self._seek_slider, 1)
        controls_row.addWidget(self._time_label)
        controls_row.addWidget(volume_label)
        controls_row.addWidget(self._volume_slider)

        self._template_input = QtWidgets.QLineEdit(self)
        self._template_input.setPlaceholderText("Rename template: [BPM]_[Key]_[OriginalName]")
        self._template_input.setText("[BPM]_[Key]_[OriginalName]")
        rename_button = QtWidgets.QPushButton("Preview Rename", self)
        rename_button.clicked.connect(self._preview_rename)

        open_button = QtWidgets.QPushButton("Open Library", self)
        open_button.clicked.connect(self._open_library)

        refresh_button = QtWidgets.QPushButton("Refresh", self)
        refresh_button.clicked.connect(self.reload)

        rename_row = QtWidgets.QHBoxLayout()
        rename_row.addWidget(open_button)
        rename_row.addWidget(refresh_button)
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

        self._delete_button = QtWidgets.QPushButton("Delete Selected", self)
        self._delete_button.setShortcut("Delete")
        self._delete_button.clicked.connect(self._delete_selected)
        tags_row.addWidget(self._delete_button)

        sort_button = QtWidgets.QPushButton("Sort Files", self)
        sort_button.clicked.connect(self._sort_files)
        tags_row.addWidget(sort_button)

        open_folder_button = QtWidgets.QPushButton("Open Folder", self)
        open_folder_button.clicked.connect(self._open_selected_folder)
        tags_row.addWidget(open_folder_button)

        render_button = QtWidgets.QPushButton("Render MIDI→MP3", self)
        render_button.clicked.connect(self._render_midi)
        tags_row.addWidget(render_button)

        pager_row = QtWidgets.QHBoxLayout()
        self._count_label = QtWidgets.QLabel("Files: 0", self)
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
        pager_row.addWidget(self._count_label)
        pager_row.addWidget(self._page_label)
        pager_row.addStretch(1)
        pager_row.addWidget(QtWidgets.QLabel("Page size", self))
        pager_row.addWidget(self._page_size)

        list_page = QtWidgets.QWidget(self)
        list_layout = QtWidgets.QVBoxLayout(list_page)
        filter_row = QtWidgets.QHBoxLayout()
        filter_row.addWidget(self._bpm_min)
        filter_row.addWidget(self._bpm_max)
        filter_row.addWidget(self._key_filter)
        filter_row.addWidget(self._type_filter)
        filter_row.addWidget(self._include_unknown_bpm)
        filter_row.addWidget(self._duration_min)
        filter_row.addWidget(self._duration_max)
        filter_row.addStretch(1)

        list_layout.addWidget(self._search)
        list_layout.addLayout(filter_row)
        list_layout.addLayout(rename_row)
        list_layout.addWidget(self._table)
        list_layout.addWidget(self._waveform)
        list_layout.addLayout(controls_row)
        list_layout.addLayout(tags_row)
        list_layout.addLayout(pager_row)

        self._zarr_view = ZarrLibraryView(self.root, [], self)
        tree_page = QtWidgets.QWidget(self)
        tree_layout = QtWidgets.QVBoxLayout(tree_page)
        tree_layout.addWidget(self._zarr_view)

        self._tabs = QtWidgets.QTabWidget(self)
        self._tabs.addTab(list_page, "List")
        self._tabs.addTab(tree_page, "Tree")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._tabs)

    # --- Seek slider ---

    def _seek_started(self) -> None:
        self._seeking = True

    def _seek_finished(self) -> None:
        self._seeking = False
        pos = self._seek_slider.value()
        self._player.setPosition(pos)

    def _seek_moved(self, value: int) -> None:
        self._update_time_label(value, self._player.duration())

    def _on_position_changed(self, pos: int) -> None:
        if not self._seeking:
            self._seek_slider.setValue(pos)
        self._waveform.set_position(pos, self._player.duration())
        self._update_time_label(pos, self._player.duration())

    def _on_duration_changed(self, duration: int) -> None:
        self._seek_slider.setRange(0, duration)
        self._waveform.set_position(self._player.position(), duration)
        self._update_time_label(self._player.position(), duration)

    def _waveform_seek(self, position_ms: int) -> None:
        self._player.setPosition(position_ms)

    def _on_volume_changed(self, value: int) -> None:
        self._audio_output.setVolume(value / 100.0)

    def _on_media_status(self, status: QtMultimedia.QMediaPlayer.MediaStatus) -> None:
        if status == QtMultimedia.QMediaPlayer.MediaStatus.LoadedMedia:
            self._player.play()

    def _update_time_label(self, pos: int, duration: int) -> None:
        self._time_label.setText(f"{_format_ms(pos)} / {_format_ms(duration)}")

    # --- Selection / items ---

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
        self._stop_playback()
        if item.path.suffix.lower() in {".mid", ".midi"}:
            wav = _render_midi_to_tmp_wav(item.path)
            if wav is None:
                QtWidgets.QMessageBox.information(
                    self, "MIDI",
                    "Cannot render MIDI. Check fluidsynth + soundfont.",
                )
                return
            self._tmp_midi_wav = wav
            samples = self._waveform_provider.load(wav)
            self._waveform.set_samples(samples)
            url = QtCore.QUrl.fromLocalFile(str(wav))
            self._player.setSource(url)
            return
        url = QtCore.QUrl.fromLocalFile(str(item.path))
        self._player.setSource(url)

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

    def _open_library(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.root)))

    def _render_midi(self) -> None:
        items = self._selected_items()
        if not items:
            QtWidgets.QMessageBox.information(self, "Render", "Select one or more MIDI files.")
            return
        midi_items = [i for i in items if i.path.suffix.lower() in {".mid", ".midi"}]
        if not midi_items:
            QtWidgets.QMessageBox.information(self, "Render", "No MIDI files in selection.")
            return
        output_dir = self.root / "rendered_mp3"
        failures = []
        for item in midi_items:
            result = render_midi_to_mp3(item.path, output_dir)
            if result is None:
                failures.append(item.path.name)
        if failures:
            QtWidgets.QMessageBox.warning(
                self, "Render",
                f"Failed to render:\n" + "\n".join(failures),
            )
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(output_dir)))

    def _open_selected_folder(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Folder with Audio/MIDI")
        if directory:
            self.folder_opened.emit(Path(directory))

    def _show_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self._first_selected_item()
        if item is None:
            return
        menu = QtWidgets.QMenu(self)
        open_action = menu.addAction("Reveal in Finder")
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == open_action:
            self._reveal_in_finder(item.path)

    def _reveal_in_finder(self, path: Path) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.parent)))

    def _stop_playback(self) -> None:
        self._player.stop()
        self._player.setSource(QtCore.QUrl())
        self._cleanup_tmp_wav()
        self._seek_slider.setRange(0, 0)
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00 / 0:00")
        self._waveform.set_position(0, 0)

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
            QtWidgets.QMessageBox.warning(self, "Delete", "Some files could not be deleted:\n" + "\n".join(failures))
        self.reload()

    def _sort_files(self) -> None:
        items = self._selected_items()
        if not items:
            QtWidgets.QMessageBox.information(self, "Sort", "Select one or more rows.")
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Sort Files",
            "Move selected files into data/{type}/{bpm}/ folders?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        failures = []
        for item in items:
            target_dir = self._build_sort_path(item)
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / item.path.name
            try:
                item.path.rename(target_path)
                self._metadata_store.delete(item.path)
                if item.rating is not None or item.tags:
                    self._metadata_store.save(
                        target_path,
                        UserMetadata(rating=item.rating, tags=list(item.tags)),
                    )
            except OSError as exc:
                failures.append(f"{item.path.name}: {exc}")
        if failures:
            QtWidgets.QMessageBox.warning(self, "Sort", "Some files could not be moved:\n" + "\n".join(failures))
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

    def _update_count_label(self) -> None:
        total = self._filter.rowCount()
        self._count_label.setText(f"Files: {total}")

    def _apply_meta_filters(self) -> None:
        bpm_min = self._bpm_min.value()
        bpm_max = self._bpm_max.value()
        key = self._key_filter.text().strip().lower()
        self._filter.set_bpm_range(bpm_min, bpm_max)
        self._filter.set_key_filter(key)
        self._filter.set_type_filter(self._type_filter.currentData() or "")
        self._filter.set_include_unknown_bpm(self._include_unknown_bpm.isChecked())
        self._filter.set_duration_range(self._duration_min.value(), self._duration_max.value())

    def _selected_paths(self) -> list[str]:
        return [str(item.path) for item in self._selected_items()]

    def _build_sort_path(self, item: MediaItem) -> Path:
        return _sort_path_for_item(self.root, item)


def _format_ms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _sort_path_for_item(root: Path, item: MediaItem) -> Path:
    suffix = item.path.suffix.lower().lstrip(".")
    media_type = "wav" if suffix == "wav" else "midi" if suffix in {"mid", "midi"} else "other"
    bpm_value = None
    metadata = getattr(item, "metadata", None)
    if metadata is not None and getattr(metadata, "bpm", None) is not None:
        try:
            bpm_value = int(round(float(metadata.bpm)))
        except (TypeError, ValueError):
            bpm_value = None
    bpm_folder = f"{bpm_value}bpm" if bpm_value is not None else "unknown-bpm"
    return root / media_type / bpm_folder
