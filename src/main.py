from __future__ import annotations

import sys
from pathlib import Path

from PyQt6 import QtWidgets

from birka.presentation.audio_browser import AudioBrowserWindow


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    app = QtWidgets.QApplication(sys.argv)
    # Release the cached sfizz Synth instances before the interpreter tears
    # down. Without this, pysfizz's nanobind-bound Synth objects leak at
    # process exit (their background file-pool threads delay shutdown and
    # emit "nanobind: leaked N instances" warnings).
    app.aboutToQuit.connect(_dispose_sfizz)
    window = AudioBrowserWindow([project_root / "data" / "library"])
    window.show()
    return app.exec()


def _dispose_sfizz() -> None:
    try:
        from birka.infrastructure.midi_renderer import dispose_sfizz_cache

        dispose_sfizz_cache()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
