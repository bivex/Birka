from __future__ import annotations

import faulthandler
import logging
import os
import signal
import sys
from pathlib import Path

from PyQt6 import QtWidgets

from birka.presentation.audio_browser import AudioBrowserWindow

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"


def main() -> int:
    faulthandler.enable()
    logging.basicConfig(
        level=logging.DEBUG,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    logging.getLogger("PyQt6").setLevel(logging.WARNING)
    logging.getLogger("ffmpeg").setLevel(logging.WARNING)
    logging.getLogger("numcodecs").setLevel(logging.WARNING)
    # SIGUSR1 dumps all thread stacks to stderr — send it when GUI freezes:
    # kill -USR1 <pid>
    faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)

    project_root = Path(__file__).resolve().parents[1]
    app = QtWidgets.QApplication(sys.argv)
    app.aboutToQuit.connect(_dispose_audio_backends)
    window = AudioBrowserWindow([project_root / "data" / "library"])
    window.show()
    print(
        f"[birka] PID={os.getpid()} — send 'kill -USR1 {os.getpid()}' to dump stacks",
        flush=True,
    )
    exit_code = app.exec()
    # Native VST3 plugins (e.g. SlateCore / Fresh Air) spawn C++ threads that
    # are invisible to Python's threading module. These non-daemon threads keep
    # the process alive after app.exec() returns. os._exit() bypasses Python
    # teardown (which would segfault anyway via objc_msgSend on freed objects)
    # and terminates immediately. _dispose_audio_backends() above already ran
    # via aboutToQuit before this point.
    os._exit(exit_code)


def _dispose_audio_backends() -> None:
    """Tear down native audio backends before the interpreter finalizes.

    Drops the DAWdreamer VST engine (its plugin processors — notably Fresh
    Air / SlateCore — hold Objective-C objects whose destructors segfault if
    they run during interpreter teardown) and the sfizz synth cache. The
    module also registers this via atexit as a safety net for exit paths
    where aboutToQuit never fires (e.g. an unhandled exception -> Py_Exit).
    """
    for disposer in ("dispose_vst_chain_cache", "dispose_sfizz_cache"):
        try:
            from birka.infrastructure import midi_renderer

            getattr(midi_renderer, disposer)()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
