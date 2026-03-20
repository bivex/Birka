from __future__ import annotations

import sys
from pathlib import Path

from PyQt6 import QtWidgets

from birka.application.scan_library import ScanLibrary
from birka.infrastructure.file_scanner import FileSystemScanner
from birka.infrastructure.metadata_readers import AudioMidiMetadataReader
from birka.presentation.audio_browser import AudioBrowserWindow


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    library_root = project_root / "data" / "library"
    scanner = FileSystemScanner([".wav", ".mid", ".midi"])
    reader = AudioMidiMetadataReader()
    items = ScanLibrary(scanner, reader).execute(library_root)

    app = QtWidgets.QApplication(sys.argv)
    window = AudioBrowserWindow(items)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
