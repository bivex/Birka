"""Capture fresh README screenshots of the Birka GUI (List + Tree views).

Builds AudioBrowserWindow offscreen against data/library, waits for the async
library scan to populate, switches to each tab, and grabs a PNG. Run:
  QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/_capture_screenshots.py
"""
import sys, time
from pathlib import Path

sys.path.insert(0, "/Volumes/External/Code/Birka/src")
from PyQt6 import QtWidgets, QtCore

from birka.presentation.audio_browser import AudioBrowserWindow

OUT = Path("/Volumes/External/Code/Birka/docs/screenshots")
OUT.mkdir(parents=True, exist_ok=True)
LIB = Path("/Volumes/External/Code/Birka/data/library")

app = QtWidgets.QApplication(sys.argv)
win = AudioBrowserWindow([LIB])
win.resize(1920, 1080)
win.show()

# Pump the event loop so the async _RefreshWorker scan finishes and rows land
# in the table before we grab. ~2.5s is plenty for a small demo library.
deadline = time.time() + 3.5
while time.time() < deadline:
    app.processEvents()
    time.sleep(0.05)

tab = win._tabs.widget(0)  # the LibraryTab
inner = tab._tabs  # List / Tree QTabWidget


def grab(name):
    app.processEvents()
    time.sleep(0.1)
    app.processEvents()
    win.grab().save(str(OUT / name))
    print("saved", OUT / name)


# List view (tab 0)
inner.setCurrentIndex(0)
grab("birka_gui_list.png")

# Tree view (tab 1) — build it then grab
inner.setCurrentIndex(1)
for _ in range(20):
    app.processEvents()
    time.sleep(0.05)
grab("birka_gui_tree.png")

print("done")
