from __future__ import annotations

import sys
from pathlib import Path

from PyQt6 import QtWidgets

from birka.presentation.audio_browser import AudioBrowserWindow


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    app = QtWidgets.QApplication(sys.argv)

    def _on_about_to_quit() -> None:
        try:
            sys.stderr.flush()
        except Exception:
            pass
        try:
            from birka.infrastructure.midi_renderer import (
                dispose_sfizz_cache,
                dispose_vst_chain_cache,
            )

            dispose_sfizz_cache()
        except Exception:
            pass
        try:
            from birka.infrastructure.midi_renderer import dispose_vst_chain_cache

            dispose_vst_chain_cache()
        except Exception:
            pass

    app.aboutToQuit.connect(_on_about_to_quit)
    window = AudioBrowserWindow([project_root / "data" / "library"])
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
