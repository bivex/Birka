from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import List

from PyQt6 import QtCore, QtGui, QtMultimedia, QtWidgets

from birka.application.load_library import LoadLibrary
from birka.application.user_metadata import UserMetadata, UserMetadataStore
from birka.domain.media import MediaItem, Rating
from birka.infrastructure.file_scanner import FileSystemScanner
from birka.infrastructure.metadata_readers import AudioMidiMetadataReader
from birka.infrastructure.scan_cache import ScanCache
from birka.infrastructure.midi_renderer import (
    _backend_name,
    render_midi_to_mp3_batch,
    render_midi_to_wav_batch,
)
from birka.infrastructure.waveform_provider import WaveformProvider
from birka.presentation.media_presenter import MediaPresenter
from birka.presentation.file_drag_table import FileDragTableView
from birka.presentation.media_filter_proxy import MediaFilterProxyModel
from birka.presentation.media_table_model import MediaTableModel
from birka.presentation.pagination_proxy import PaginationProxyModel
from birka.presentation.rename_dialog import RenameCoordinator
from birka.presentation.waveform_widget import WaveformWidget
from birka.presentation.zarr_library_view import ZarrLibraryView

logger = logging.getLogger(__name__)

AUTO_REFRESH_INTERVAL_MS = 10_000
DEFAULT_PAGE_SIZE = 50
DEFAULT_VOLUME_PERCENT = 80
DEFAULT_VOLUME_FRACTION = 0.8
PAGE_SIZE_OPTIONS = (25, 50, 100, 200)
RATING_RANGE = range(0, 6)
BPM_MAX = 400
DURATION_MAX_SECONDS = 3600
VOLUME_SLIDER_WIDTH = 100
VOLUME_SLIDER_MAX = 100
MIDI_EXTENSIONS = {".mid", ".midi"}


def _render_midi_to_tmp_wav(midi_path: Path) -> Path | None:
    """Render MIDI to a temporary WAV file using sfizz. No normalization (fast)."""
    from birka.infrastructure.midi_renderer import render_midi_to_wav

    tmp_dir = Path(tempfile.mkdtemp(prefix="birka_midi_"))
    wav_path = tmp_dir / (midi_path.stem + ".wav")
    if render_midi_to_wav(
        midi_path, wav_path, sample_rate=96000, polyphony=64, bit_depth=24
    ):
        return wav_path
    return None


def _render_midi_to_tmp_preview_mp3(midi_path: Path) -> Path | None:
    """Render MIDI to a small temp MP3 (22 kHz) for fast preview listening."""
    from birka.infrastructure.midi_renderer import render_midi_preview_mp3

    tmp_dir = Path(tempfile.mkdtemp(prefix="birka_midi_"))
    mp3_path = tmp_dir / (midi_path.stem + ".mp3")
    if render_midi_preview_mp3(midi_path, mp3_path):
        return mp3_path
    return None


class _RefreshWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(list)

    def __init__(
        self, root: Path, metadata_store: UserMetadataStore, cache=None
    ) -> None:
        super().__init__()
        self._root = root
        self._metadata_store = metadata_store
        self._cache = cache

    def run(self) -> None:
        scanner = FileSystemScanner([".wav", ".mid", ".midi"])
        reader = AudioMidiMetadataReader()
        loader = LoadLibrary(scanner, reader, self._metadata_store, cache=self._cache)
        items = loader.execute(self._root)
        self.finished.emit(items)


class _RenderWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int)
    finished = QtCore.pyqtSignal(list, list)

    def __init__(
        self, midi_paths: list[Path], output_dir: Path, quality: int = 2
    ) -> None:
        super().__init__()
        self._midi_paths = midi_paths
        self._output_dir = output_dir
        self._quality = quality

    def run(self) -> None:
        def on_progress(completed: int, total: int, _: Path, __: bool) -> None:
            self.progress.emit(completed, total)

        successful, failed = render_midi_to_mp3_batch(
            self._midi_paths,
            self._output_dir,
            on_progress=on_progress,
            quality=self._quality,
        )
        self.finished.emit(successful, failed)


class _RenderWavWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int)
    finished = QtCore.pyqtSignal(list, list)

    def __init__(
        self, midi_paths: list[Path], output_dir: Path, quality: int = 2
    ) -> None:
        super().__init__()
        self._midi_paths = midi_paths
        self._output_dir = output_dir
        self._quality = quality

    def run(self) -> None:
        def on_progress(completed: int, total: int, _: Path, __: bool) -> None:
            self.progress.emit(completed, total)

        successful, failed = render_midi_to_wav_batch(
            self._midi_paths,
            self._output_dir,
            on_progress=on_progress,
            quality=self._quality,
        )
        self.finished.emit(successful, failed)


class _MidiPlayRenderWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(str)

    def __init__(self, midi_path: Path, fast: bool = False) -> None:
        super().__init__()
        self._midi_path = midi_path
        self._fast = fast

    def run(self) -> None:
        if self._fast:
            rendered = _render_midi_to_tmp_preview_mp3(self._midi_path)
        else:
            rendered = _render_midi_to_tmp_wav(self._midi_path)
        self.finished.emit(str(rendered) if rendered is not None else "")


class LibraryTab(QtWidgets.QWidget):
    folder_opened = QtCore.pyqtSignal(Path)

    def __init__(
        self,
        root: Path,
        metadata_store: UserMetadataStore,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.root = root
        self._metadata_store = metadata_store
        self._scanner = FileSystemScanner([".wav", ".mid", ".midi"])
        self._reader = AudioMidiMetadataReader()
        # One persistent scan cache per tab. Lives next to user_metadata.json
        # so it survives restarts and makes the 10s auto-refresh near-instant
        # for unchanged files (mtime+size hit → no file parse). Created
        # best-effort: if the data dir isn't writable we fall back to the
        # uncached legacy path.
        self._scan_cache = None
        try:
            cache_path = Path("/Volumes/External/Code/Birka/data/.scan_cache.sqlite")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._scan_cache = ScanCache(cache_path)
        except Exception:
            self._scan_cache = None
        self._loader = LoadLibrary(
            self._scanner, self._reader, self._metadata_store, cache=self._scan_cache
        )
        self._waveform_provider = WaveformProvider()
        self._presenter = MediaPresenter()
        self._rename = RenameCoordinator(self)

        self._items: List[MediaItem] = []
        self._item_by_path: dict[str, MediaItem] = {}
        self._selection_connected = False
        self._zarr_view: ZarrLibraryView | None = None
        self._tmp_midi_wav: Path | None = None
        self._midi_play_thread: QtCore.QThread | None = None
        self._midi_play_worker: _MidiPlayRenderWorker | None = None
        self._loading_midi_path: Path | None = None
        self._seeking = False
        self._current_audio_device_id = str(
            QtMultimedia.QMediaDevices.defaultAudioOutput().id()
        )

        self._player = QtMultimedia.QMediaPlayer(self)
        self._audio_output = QtMultimedia.QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._current_audio_device_id = str(
            QtMultimedia.QMediaDevices.defaultAudioOutput().id()
        )
        self._audio_output_timer = QtCore.QTimer(self)
        self._audio_output_timer.timeout.connect(self._on_audio_outputs_changed)
        self._audio_output_timer.start(500)
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
        self._auto_refresh_timer.start(AUTO_REFRESH_INTERVAL_MS)

    def reload(self) -> None:
        if self._refresh_thread is not None:
            return
        worker = _RefreshWorker(self.root, self._metadata_store, cache=self._scan_cache)
        thread = QtCore.QThread()
        worker.moveToThread(thread)
        worker.finished.connect(self._apply_refresh)
        thread.started.connect(worker.run)
        self._refresh_thread = thread
        self._refresh_worker = worker
        thread.start()

    def _apply_refresh(self, items: List[MediaItem]) -> None:
        old_paths = self._capture_selection_paths()

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

        self._reconnect_selection_model()
        self._first_load = False
        self._cleanup_refresh_thread()

    def _capture_selection_paths(self) -> set[str]:
        if self._first_load:
            return set()
        rows = self._selected_rows_from_table()
        return {self._model.row_at(r).path for r in rows}

    def _reconnect_selection_model(self) -> None:
        sel_model = self._table.selectionModel()
        if sel_model is None:
            return
        if self._selection_connected:
            try:
                sel_model.selectionChanged.disconnect(self._on_selection_changed)
            except TypeError:
                _already_disconnected = True
        sel_model.selectionChanged.connect(self._on_selection_changed)
        self._selection_connected = True

    def _cleanup_refresh_thread(self) -> None:
        if self._refresh_thread is not None:
            self._refresh_thread.quit()
            self._refresh_thread.wait()
            self._refresh_thread = None
            self._refresh_worker = None

    def _restore_selection(self, old_paths: set[str]) -> None:
        if not old_paths:
            return
        new_rows = {
            row
            for row in range(self._model.rowCount())
            if self._model.row_at(row).path in old_paths
        }
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
            self._apply_selection_to_table(selection)

    def _apply_selection_to_table(self, selection: QtCore.QItemSelection) -> None:
        sel_model = self._table.selectionModel()
        if sel_model is None:
            return
        if self._selection_connected:
            try:
                sel_model.selectionChanged.disconnect(self._on_selection_changed)
            except TypeError:
                _already_disconnected = True
        sel_model.select(
            selection,
            QtCore.QItemSelectionModel.SelectionFlag.Select
            | QtCore.QItemSelectionModel.SelectionFlag.Rows,
        )
        sel_model.selectionChanged.connect(self._on_selection_changed)

    def stop_all(self) -> None:
        self._player.stop()
        self._cleanup_tmp_wav()
        self._stop_midi_render_thread()

    def _cleanup_tmp_wav(self) -> None:
        if self._tmp_midi_wav is not None:
            try:
                self._tmp_midi_wav.unlink()
                self._tmp_midi_wav.parent.rmdir()
            except OSError as exc:
                logger.debug("Failed to clean up temp WAV: %s", exc)
            self._tmp_midi_wav = None

    def _stop_midi_render_thread(self) -> None:
        self._loading_midi_path = None
        if self._midi_play_thread is not None:
            try:
                self._midi_play_worker.finished.disconnect(
                    self._on_midi_render_finished
                )
            except (TypeError, RuntimeError):
                pass
            self._midi_play_thread.quit()
            self._midi_play_thread.wait()
            self._midi_play_thread = None
            self._midi_play_worker = None

    def _build_ui(self) -> None:
        self._init_filter_and_pager()
        self._init_search_and_filters()
        self._init_table()
        self._init_playback_controls()
        self._init_rename_controls()
        self._init_tags_controls()
        self._init_pager_controls()
        self._assemble_layout()

    def _init_filter_and_pager(self) -> None:
        self._filter = MediaFilterProxyModel(self)
        self._filter.modelReset.connect(self._update_page_label)
        self._filter.layoutChanged.connect(self._update_page_label)
        self._filter.modelReset.connect(self._update_count_label)
        self._filter.layoutChanged.connect(self._update_count_label)
        self._pager = PaginationProxyModel(page_size=DEFAULT_PAGE_SIZE, parent=self)
        self._pager.setSourceModel(self._filter)

    def _init_search_and_filters(self) -> None:
        self._search = QtWidgets.QLineEdit(self)
        self._search.setPlaceholderText("Search by name, type, BPM, key, tags...")
        self._search.textChanged.connect(self._filter.set_text_filter)

        self._bpm_min = QtWidgets.QSpinBox(self)
        self._bpm_min.setRange(0, BPM_MAX)
        self._bpm_min.setPrefix("BPM min: ")
        self._bpm_min.valueChanged.connect(self._apply_meta_filters)
        self._bpm_max = QtWidgets.QSpinBox(self)
        self._bpm_max.setRange(0, BPM_MAX)
        self._bpm_max.setPrefix("BPM max: ")
        self._bpm_max.setValue(BPM_MAX)
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
        self._duration_min.setRange(0, DURATION_MAX_SECONDS)
        self._duration_min.setPrefix("Dur min: ")
        self._duration_min.setSuffix("s")
        self._duration_min.valueChanged.connect(self._apply_meta_filters)
        self._duration_max = QtWidgets.QSpinBox(self)
        self._duration_max.setRange(0, DURATION_MAX_SECONDS)
        self._duration_max.setPrefix("Dur max: ")
        self._duration_max.setSuffix("s")
        self._duration_max.setValue(DURATION_MAX_SECONDS)
        self._duration_max.valueChanged.connect(self._apply_meta_filters)

    def _init_table(self) -> None:
        self._table = FileDragTableView(self._selected_paths, self)
        self._table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().sortIndicatorChanged.connect(self._pager.sort)
        self._table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

    def _init_playback_controls(self) -> None:
        self._waveform = WaveformWidget(self)
        self._waveform.position_changed.connect(self._waveform_seek)
        self._play_button = QtWidgets.QPushButton("Play", self)
        self._play_button.setObjectName("playButton")
        self._stop_button = QtWidgets.QPushButton("Stop", self)
        self._stop_button.setObjectName("stopButton")
        self._play_button.clicked.connect(self._play_selected)
        self._play_fast_button = QtWidgets.QPushButton("Play Fast", self)
        self._play_fast_button.setObjectName("playFastButton")
        self._play_fast_button.setToolTip(
            "Render selected MIDI to a low-quality MP3 (22 kHz) for fast preview"
        )
        self._play_fast_button.clicked.connect(self._play_selected_fast)
        self._stop_button.clicked.connect(self._stop_playback)

        self._seek_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, self)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.sliderPressed.connect(self._seek_started)
        self._seek_slider.sliderReleased.connect(self._seek_finished)
        self._seek_slider.sliderMoved.connect(self._seek_moved)

        self._time_label = QtWidgets.QLabel("0:00 / 0:00", self)

        self._volume_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, self)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(DEFAULT_VOLUME_PERCENT)
        self._volume_slider.setFixedWidth(VOLUME_SLIDER_WIDTH)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._audio_output.setVolume(DEFAULT_VOLUME_FRACTION)

    def _init_rename_controls(self) -> None:
        self._template_input = QtWidgets.QLineEdit(self)
        self._template_input.setPlaceholderText(
            "Rename template: [BPM]_[Key]_[OriginalName]"
        )
        self._template_input.setText("[BPM]_[Key]_[OriginalName]")
        self._rename_button = QtWidgets.QPushButton("Preview Rename", self)
        self._rename_button.setObjectName("renameButton")
        self._rename_button.clicked.connect(self._preview_rename)
        self._open_button = QtWidgets.QPushButton("Open Library", self)
        self._open_button.setObjectName("openLibraryButton")
        self._open_button.clicked.connect(self._open_library)
        self._refresh_button = QtWidgets.QPushButton("Refresh", self)
        self._refresh_button.setObjectName("refreshButton")
        self._refresh_button.clicked.connect(self.reload)

    def _init_tags_controls(self) -> None:
        self._tags_input = QtWidgets.QLineEdit(self)
        self._tags_input.setPlaceholderText("Tags (comma separated)")
        self._rating_combo = QtWidgets.QComboBox(self)
        self._rating_combo.addItem("", None)
        for value in RATING_RANGE:
            self._rating_combo.addItem(str(value), value)
        self._apply_button = QtWidgets.QPushButton("Apply Tags/Rating", self)
        self._apply_button.setObjectName("applyButton")
        self._apply_button.clicked.connect(self._apply_tags_rating)
        self._delete_button = QtWidgets.QPushButton("Delete Selected", self)
        self._delete_button.setObjectName("deleteButton")
        self._delete_button.setShortcut("Delete")
        self._delete_button.clicked.connect(self._delete_selected)
        self._sort_button = QtWidgets.QPushButton("Sort Files", self)
        self._sort_button.setObjectName("sortButton")
        self._sort_button.clicked.connect(self._sort_files)
        self._open_folder_button = QtWidgets.QPushButton("Open Folder", self)
        self._open_folder_button.setObjectName("openFolderButton")
        self._open_folder_button.clicked.connect(self._open_selected_folder)
        self._render_button = QtWidgets.QPushButton("Render MIDI\u2192MP3", self)
        self._render_button.setObjectName("renderButton")
        self._render_button.clicked.connect(self._render_midi)
        self._render_wav_button = QtWidgets.QPushButton("Render MIDI\u2192WAV", self)
        self._render_wav_button.setObjectName("renderWavButton")
        self._render_wav_button.clicked.connect(self._render_midi_wav)
        self._quality_label = QtWidgets.QLabel("Synth Quality:", self)
        self._quality_combo = QtWidgets.QComboBox(self)
        self._quality_combo.addItem("Fast Quality (Hermite3)", 2)
        self._quality_combo.addItem("Standard Quality (Sinc24)", 6)
        self._quality_combo.addItem("Studio Quality (Sinc72)", 10)
        self._quality_combo.setCurrentIndex(0)

    def _init_pager_controls(self) -> None:
        self._count_label = QtWidgets.QLabel("Files: 0", self)
        self._page_label = QtWidgets.QLabel("Page 1/1", self)
        self._page_size = QtWidgets.QComboBox(self)
        for size in PAGE_SIZE_OPTIONS:
            self._page_size.addItem(str(size), size)
        self._page_size.setCurrentText(str(DEFAULT_PAGE_SIZE))
        self._page_size.currentIndexChanged.connect(self._on_page_size_changed)
        self._prev_button = QtWidgets.QPushButton("Prev", self)
        self._prev_button.setObjectName("prevButton")
        self._next_button = QtWidgets.QPushButton("Next", self)
        self._next_button.setObjectName("nextButton")
        self._prev_button.clicked.connect(self._prev_page)
        self._next_button.clicked.connect(self._next_page)

    def _assemble_layout(self) -> None:
        list_page = self._build_list_page()
        tree_page = self._build_tree_page()

        self._tabs = QtWidgets.QTabWidget(self)
        self._tabs.addTab(list_page, "List")
        self._tabs.addTab(tree_page, "Tree")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._tabs)

    def _build_list_page(self) -> QtWidgets.QWidget:
        filter_layout = self._build_filter_layout()
        ops_layout = self._build_operations_layout()
        controls_row = self._build_controls_row()
        pager_row = self._build_pager_row()

        page = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # 1. Search & Filter Card
        filter_frame = QtWidgets.QFrame(page)
        filter_frame.setObjectName("containerFrame")
        filter_layout_container = QtWidgets.QVBoxLayout(filter_frame)
        filter_layout_container.setContentsMargins(12, 12, 12, 12)
        filter_layout_container.setSpacing(8)
        filter_layout_container.addWidget(self._search)
        filter_layout_container.addLayout(filter_layout)
        layout.addWidget(filter_frame)

        # 2. Main Media Table
        layout.addWidget(self._table, 1)

        # 3. Playback & Waveform Card
        player_frame = QtWidgets.QFrame(page)
        player_frame.setObjectName("containerFrame")
        player_layout = QtWidgets.QVBoxLayout(player_frame)
        player_layout.setContentsMargins(12, 12, 12, 12)
        player_layout.setSpacing(8)
        player_layout.addWidget(self._waveform)
        player_layout.addLayout(controls_row)
        layout.addWidget(player_frame)

        # 4. Operations (Rename & Tags) Card
        ops_frame = QtWidgets.QFrame(page)
        ops_frame.setObjectName("containerFrame")
        ops_layout_container = QtWidgets.QVBoxLayout(ops_frame)
        ops_layout_container.setContentsMargins(12, 12, 12, 12)
        ops_layout_container.setSpacing(8)
        ops_layout_container.addLayout(ops_layout)
        layout.addWidget(ops_frame)

        # 5. Pager bottom row
        layout.addLayout(pager_row)
        return page

    def _build_tree_page(self) -> QtWidgets.QWidget:
        self._zarr_view = ZarrLibraryView(self.root, [], self)
        page = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(page)
        layout.addWidget(self._zarr_view)
        return page

    def _build_filter_layout(self) -> QtWidgets.QGridLayout:
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(8)
        grid.addWidget(self._bpm_min, 0, 0)
        grid.addWidget(self._bpm_max, 0, 1)
        grid.addWidget(self._include_unknown_bpm, 0, 2)
        grid.addWidget(self._key_filter, 0, 3)
        grid.addWidget(self._type_filter, 0, 4)

        grid.addWidget(self._duration_min, 1, 0)
        grid.addWidget(self._duration_max, 1, 1)
        grid.setColumnStretch(5, 1)
        return grid

    def _build_operations_layout(self) -> QtWidgets.QGridLayout:
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(8)

        # Row 0: Rename
        grid.addWidget(self._open_button, 0, 0)
        grid.addWidget(self._refresh_button, 0, 1)
        grid.addWidget(self._template_input, 0, 2, 1, 2)
        grid.addWidget(self._rename_button, 0, 4)

        # Row 1: Tags
        grid.addWidget(self._tags_input, 1, 0, 1, 3)
        grid.addWidget(self._rating_combo, 1, 3)
        grid.addWidget(self._apply_button, 1, 4)

        # Row 2: Actions
        grid.addWidget(self._delete_button, 2, 0)
        grid.addWidget(self._sort_button, 2, 1)
        grid.addWidget(self._open_folder_button, 2, 2)
        grid.addWidget(self._render_button, 2, 3)
        grid.addWidget(self._render_wav_button, 2, 4)

        # Row 3: Quality
        grid.addWidget(self._quality_label, 3, 2)
        grid.addWidget(self._quality_combo, 3, 3, 1, 2)

        return grid

    def _build_controls_row(self) -> QtWidgets.QHBoxLayout:
        volume_label = QtWidgets.QLabel("Vol", self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self._play_button)
        row.addWidget(self._play_fast_button)
        row.addWidget(self._stop_button)
        row.addWidget(self._seek_slider, 1)
        row.addWidget(self._time_label)
        row.addWidget(volume_label)
        row.addWidget(self._volume_slider)
        return row

    def _build_pager_row(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self._prev_button)
        row.addWidget(self._next_button)
        row.addWidget(self._count_label)
        row.addWidget(self._page_label)
        row.addStretch(1)
        row.addWidget(QtWidgets.QLabel("Page size", self))
        row.addWidget(self._page_size)
        return row

    # --- Seek slider ---

    def _seek_started(self) -> None:
        self._seeking = True

    def _seek_finished(self) -> None:
        self._seeking = False
        self._player.setPosition(self._seek_slider.value())

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
        self._audio_output.setVolume(value / VOLUME_SLIDER_MAX)

    def _on_audio_outputs_changed(self) -> None:
        new_device = QtMultimedia.QMediaDevices.defaultAudioOutput()
        if str(new_device.id()) == self._current_audio_device_id:
            return
        self._current_audio_device_id = str(new_device.id())
        volume = self._audio_output.volume()
        self._player.setAudioOutput(None)
        self._audio_output.deleteLater()
        self._audio_output = QtMultimedia.QAudioOutput(new_device, self)
        self._audio_output.setVolume(volume)
        self._player.setAudioOutput(self._audio_output)

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

    def _selected_rows_from_table(self) -> set[int]:
        selection = self._pager.mapSelectionToSource(
            self._table.selectionModel().selection()
        )
        indexes = selection.indexes()
        if not indexes:
            return set()
        return {self._filter.mapToSource(index).row() for index in indexes}

    def _first_selected_item(self) -> MediaItem | None:
        rows = self._selected_rows_from_table()
        if not rows:
            return None
        media_row = self._model.row_at(next(iter(rows)))
        return self._item_by_path.get(media_row.path)

    def _play_selected(self) -> None:
        item = self._first_selected_item()
        if item is None:
            QtWidgets.QMessageBox.information(self, "Play", "Select a file first.")
            return
        self._stop_playback()
        if item.path.suffix.lower() in MIDI_EXTENSIONS:
            self._play_midi(item.path)
            return
        url = QtCore.QUrl.fromLocalFile(str(item.path))
        self._player.setSource(url)

    def _play_selected_fast(self) -> None:
        item = self._first_selected_item()
        if item is None:
            QtWidgets.QMessageBox.information(self, "Play Fast", "Select a file first.")
            return
        if item.path.suffix.lower() not in MIDI_EXTENSIONS:
            self._play_selected()
            return
        self._stop_playback()
        self._play_midi(item.path, fast=True)

    def _play_midi(self, path: Path, fast: bool = False) -> None:
        self._stop_midi_render_thread()

        self._loading_midi_path = path
        button = self._play_fast_button if fast else self._play_button
        button.setEnabled(False)
        button.setText("Loading...")

        self._midi_play_thread = QtCore.QThread()
        self._midi_play_worker = _MidiPlayRenderWorker(path, fast=fast)
        self._midi_play_worker.moveToThread(self._midi_play_thread)
        self._midi_play_thread.started.connect(self._midi_play_worker.run)
        self._midi_play_worker.finished.connect(self._on_midi_render_finished)
        self._midi_play_thread.start()

    def _on_midi_render_finished(self, wav_path_str: str) -> None:
        self._play_button.setEnabled(True)
        self._play_button.setText("Play")
        self._play_fast_button.setEnabled(True)
        self._play_fast_button.setText("Play Fast")

        if self._midi_play_thread is not None:
            self._midi_play_thread.quit()
            self._midi_play_thread.wait()
            self._midi_play_thread = None
            self._midi_play_worker = None

        if not wav_path_str:
            if self._loading_midi_path is not None:
                QtWidgets.QMessageBox.information(
                    self,
                    "MIDI",
                    f"Cannot render MIDI (backend: {_backend_name()}). "
                    "Check the synth backend and its instrument file "
                    "(SF2 soundfont for tsf/fluidsynth, SFZ bank for sfizz).",
                )
                self._loading_midi_path = None
            return

        wav_path = Path(wav_path_str)
        if (
            self._loading_midi_path is not None
            and wav_path.stem == self._loading_midi_path.stem
        ):
            self._tmp_midi_wav = wav_path
            samples = self._waveform_provider.load(wav_path)
            self._waveform.set_samples(samples)
            url = QtCore.QUrl.fromLocalFile(wav_path_str)
            self._player.setSource(url)
        else:
            try:
                wav_path.unlink()
                wav_path.parent.rmdir()
            except OSError as exc:
                logger.debug("Failed to clean up aborted temp WAV: %s", exc)

        self._loading_midi_path = None

    def _preview_rename(self) -> None:
        items = self._selected_items()
        if not items:
            QtWidgets.QMessageBox.information(
                self, "Rename", "Select one or more rows to rename."
            )
            return
        template = self._template_input.text()
        self._rename.preview_and_apply(items, template)
        self.reload()

    def _selected_items(self) -> List[MediaItem]:
        rows = self._selected_rows_from_table()
        if not rows:
            return []
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
        tags = [
            tag.strip() for tag in self._tags_input.text().split(",") if tag.strip()
        ]
        rating_value = self._rating_combo.currentData()
        rating = Rating(rating_value) if rating_value is not None else None
        for item in items:
            self._metadata_store.save(item.path, UserMetadata(rating=rating, tags=tags))
        self.reload()

    def _open_library(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.root)))

    def _render_midi(self) -> None:
        self._start_render("mp3")

    def _render_midi_wav(self) -> None:
        self._start_render("wav")

    def _start_render(self, fmt: str) -> None:
        midi_items = self._filtered_midi_selection()
        if midi_items is None:
            return
        if fmt == "wav":
            output_dir = self.root / "rendered_wav"
            worker_cls = _RenderWavWorker
        else:
            output_dir = self.root / "rendered_mp3"
            worker_cls = _RenderWorker
        midi_paths = [item.path for item in midi_items]
        self._render_format = fmt
        quality = self._quality_combo.currentData() or 2

        self._render_progress = QtWidgets.QProgressDialog(
            "Rendering MIDI files...",
            "Cancel",
            0,
            len(midi_paths),
            self,
        )
        self._render_progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self._render_progress.setMinimumDuration(0)
        self._render_progress.setValue(0)

        worker = worker_cls(midi_paths, output_dir, quality=quality)
        thread = QtCore.QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_render_progress)
        worker.finished.connect(self._on_render_finished)
        thread.started.connect(worker.run)
        self._render_thread = thread
        self._render_worker = worker
        self._render_progress.canceled.connect(self._cancel_render)
        thread.start()

    def _filtered_midi_selection(self) -> list[MediaItem] | None:
        items = self._selected_items()
        if not items:
            QtWidgets.QMessageBox.information(
                self, "Render", "Select one or more MIDI files."
            )
            return None
        midi_items = [i for i in items if i.path.suffix.lower() in MIDI_EXTENSIONS]
        if not midi_items:
            QtWidgets.QMessageBox.information(
                self, "Render", "No MIDI files in selection."
            )
            return None
        return midi_items

    def _on_render_progress(self, completed: int, total: int) -> None:
        if hasattr(self, "_render_progress"):
            self._render_progress.setMaximum(total)
            self._render_progress.setValue(completed)

    def _cancel_render(self) -> None:
        pass

    def _on_render_finished(self, successful: list, failed: list) -> None:
        if hasattr(self, "_render_progress"):
            self._render_progress.close()

        if hasattr(self, "_render_thread") and self._render_thread is not None:
            self._render_thread.quit()
            self._render_thread.wait()
            self._render_thread = None
            self._render_worker = None

        if failed:
            QtWidgets.QMessageBox.warning(
                self,
                "Render",
                f"Rendered {len(successful)}/{len(successful) + len(failed)} files.\n"
                f"Failed:\n" + "\n".join(p.name for p in failed),
            )
        if successful:
            fmt = getattr(self, "_render_format", "mp3")
            output_dir = self.root / (
                "rendered_wav" if fmt == "wav" else "rendered_mp3"
            )
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(output_dir)))

    def _open_selected_folder(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Folder with Audio/MIDI"
        )
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
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(item.path.parent))
            )

    def _stop_playback(self) -> None:
        self._player.stop()
        self._player.setSource(QtCore.QUrl())
        self._cleanup_tmp_wav()
        self._stop_midi_render_thread()
        self._seek_slider.setRange(0, 0)
        self._seek_slider.setValue(0)
        self._time_label.setText("0:00 / 0:00")
        self._waveform.set_position(0, 0)

    def _delete_selected(self) -> None:
        items = self._selected_items()
        if not items:
            QtWidgets.QMessageBox.information(
                self, "Delete", "Select one or more rows."
            )
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Delete",
            f"Delete {len(items)} file(s) from disk?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
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
            QtWidgets.QMessageBox.warning(
                self,
                "Delete",
                "Some files could not be deleted:\n" + "\n".join(failures),
            )
        self.reload()

    def _sort_files(self) -> None:
        items = self._selected_items()
        if not items:
            QtWidgets.QMessageBox.information(self, "Sort", "Select one or more rows.")
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Sort Files",
            "Move selected files into data/{type}/{bpm}/{key}/ folders?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        failures = []
        for item in items:
            target_dir = _sort_path_for_item(self.root, item)
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
            QtWidgets.QMessageBox.warning(
                self, "Sort", "Some files could not be moved:\n" + "\n".join(failures)
            )
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
        self._filter.set_duration_range(
            self._duration_min.value(), self._duration_max.value()
        )

    def _selected_paths(self) -> list[str]:
        return [str(item.path) for item in self._selected_items()]


def _format_ms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _sort_path_for_item(root: Path, item: MediaItem) -> Path:
    suffix = item.path.suffix.lower().lstrip(".")
    media_type = (
        "wav" if suffix == "wav" else "midi" if suffix in {"mid", "midi"} else "other"
    )
    bpm_value = None
    key_value = None
    metadata = getattr(item, "metadata", None)
    if metadata is not None:
        if getattr(metadata, "bpm", None) is not None:
            try:
                bpm_value = int(round(float(metadata.bpm)))
            except (TypeError, ValueError):
                bpm_value = None
        if getattr(metadata, "key", None) is not None:
            key_value = metadata.key
    bpm_folder = f"{bpm_value}bpm" if bpm_value is not None else "unknown-bpm"
    key_folder = (
        key_value if (key_value is not None and key_value != "") else "unknown-key"
    )
    return root / media_type / bpm_folder / key_folder
