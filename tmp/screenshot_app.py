from __future__ import annotations

import sys
from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from birka.presentation.audio_browser import AudioBrowserWindow

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "tmp"
OUT_DIR.mkdir(parents=True, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
window = AudioBrowserWindow([PROJECT_ROOT / "data" / "library"])
window.resize(1200, 800)
window.show()

shots: list[tuple[str, int, int]] = [
    ("birka_gui_list.png", 0, 1200),
    ("birka_gui_tree.png", 1, 1600),
]

index = 0


def take_next() -> None:
    global index
    if index >= len(shots):
        app.quit()
        return
    filename, tab_index, delay_ms = shots[index]
    index += 1
    try:
        window._tabs.setCurrentIndex(tab_index)
    except Exception:
        pass
    def grab() -> None:
        screen = app.primaryScreen()
        if screen is None:
            app.quit()
            return
        pixmap = screen.grabWindow(int(window.winId()))
        pixmap.save(str(OUT_DIR / filename))
        QtCore.QTimer.singleShot(300, take_next)
    QtCore.QTimer.singleShot(delay_ms, grab)

QtCore.QTimer.singleShot(1200, take_next)

sys.exit(app.exec())
