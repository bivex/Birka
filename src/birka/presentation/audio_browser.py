from __future__ import annotations

import json
from pathlib import Path

from PyQt6 import QtWidgets

from birka.infrastructure.json_user_metadata_store import JsonUserMetadataStore
from birka.presentation.library_tab import LibraryTab


class AudioBrowserWindow(QtWidgets.QMainWindow):
    def __init__(self, roots: list[Path]) -> None:
        super().__init__()
        self.setWindowTitle("Birka Audio Browser")
        self.setMinimumSize(900, 600)
        self._metadata_store = JsonUserMetadataStore(Path("/Volumes/External/Code/Birka/data/user_metadata.json"))
        self._tabs = QtWidgets.QTabWidget(self)
        self.setCentralWidget(self._tabs)
        for root in roots:
            self._add_tab(root)
        self._build_menu()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("Library")
        add_action = menu.addAction("Add Folder")
        add_action.triggered.connect(self._add_folder)
        export_action = menu.addAction("Export Preset")
        export_action.triggered.connect(self._export_preset)
        import_action = menu.addAction("Import Preset")
        import_action.triggered.connect(self._import_preset)

    def _add_tab(self, root: Path) -> None:
        tab = LibraryTab(root, self._metadata_store, self)
        tab.folder_opened.connect(self._add_tab)
        idx = self._tabs.addTab(tab, root.name or str(root))
        self._tabs.setCurrentIndex(idx)

    def _add_folder(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Folder")
        if directory:
            self._add_tab(Path(directory))

    def _export_preset(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Preset", filter="JSON Files (*.json)")
        if not path:
            return
        roots = [self._tabs.widget(i).root for i in range(self._tabs.count())]
        payload = {"roots": [str(root) for root in roots]}
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _import_preset(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import Preset", filter="JSON Files (*.json)")
        if not path:
            return
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        roots = [Path(raw) for raw in payload.get("roots", [])]
        self._tabs.clear()
        for root in roots:
            self._add_tab(root)
