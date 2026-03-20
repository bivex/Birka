from __future__ import annotations

import sys
from pathlib import Path

from PyQt6 import QtWidgets

from birka.presentation.audio_browser import AudioBrowserWindow


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    app = QtWidgets.QApplication(sys.argv)
    window = AudioBrowserWindow([project_root / "data" / "library"])
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
