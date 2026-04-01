from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PyQt6 import QtCore, QtWidgets

from birka.domain.media import MediaItem


class ZarrLibraryView(QtWidgets.QWidget):
    MODULE_PATH = "/Volumes/External/Code/Birka/modules/zarr-view"

    def __init__(
        self,
        root: Path,
        items: Iterable[MediaItem],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._root = root
        self._items = list(items)
        self._viewer: Optional[QtWidgets.QWidget] = None
        self._status = QtWidgets.QLabel(self)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._status)
        self._build_view(layout)

    def set_items(self, items: Iterable[MediaItem]) -> None:
        self._items = list(items)
        if self._viewer is None:
            return
        root = _build_zarr_hierarchy(self._root, self._items)
        self._viewer.setTree(root)

    def _build_view(self, layout: QtWidgets.QVBoxLayout) -> None:
        try:
            import sys
            from pathlib import Path as _Path

            module_path = _Path(self.MODULE_PATH)
            if module_path.exists() and str(module_path) not in sys.path:
                sys.path.append(str(module_path))

            _apply_qt_compat()
            import zarr  # type: ignore
            from zarrview.ZarrViewer import ZarrViewer  # type: ignore
        except Exception as exc:  # noqa: BLE001
            self._status.setText(
                f"Zarr view unavailable. Install zarr and zarrview.\nDetails: {exc}"
            )
            return

        root = _build_zarr_hierarchy(self._root, self._items)
        viewer = ZarrViewer(root)
        self._viewer = viewer
        self._status.setText("Zarr tree view")
        layout.addWidget(viewer)


def _build_zarr_hierarchy(root: Path, items: Iterable[MediaItem]):
    import zarr  # type: ignore

    zroot = zarr.group()
    for item in items:
        rel = item.path.relative_to(root)
        group = zroot
        if rel.parent != Path("."):
            for part in rel.parent.parts:
                group = group.require_group(part)
        dataset = group.create_dataset(rel.name, shape=(), dtype="i1", overwrite=True)
        dataset.attrs["type"] = item.__class__.__name__
        dataset.attrs["path"] = str(item.path)
        if item.rating is not None:
            dataset.attrs["rating"] = item.rating.value
        if item.tags:
            dataset.attrs["tags"] = list(item.tags)
        metadata = getattr(item, "metadata", None)
        if metadata is not None:
            if hasattr(metadata, "bpm") and metadata.bpm is not None:
                dataset.attrs["bpm"] = metadata.bpm
            if hasattr(metadata, "key") and metadata.key is not None:
                dataset.attrs["key"] = metadata.key
            if (
                hasattr(metadata, "duration_seconds")
                and metadata.duration_seconds is not None
            ):
                dataset.attrs["duration_seconds"] = metadata.duration_seconds
            if hasattr(metadata, "sample_rate_hz"):
                dataset.attrs["sample_rate_hz"] = metadata.sample_rate_hz
            if hasattr(metadata, "channels"):
                dataset.attrs["channels"] = metadata.channels
            if hasattr(metadata, "track_count"):
                dataset.attrs["track_count"] = metadata.track_count
            if hasattr(metadata, "ticks_per_beat"):
                dataset.attrs["ticks_per_beat"] = metadata.ticks_per_beat
    return zroot


def _apply_qt_compat() -> None:
    if not hasattr(QtCore.Qt, "DropActions") and hasattr(QtCore.Qt, "DropAction"):
        QtCore.Qt.DropActions = QtCore.Qt.DropAction  # type: ignore[attr-defined]
