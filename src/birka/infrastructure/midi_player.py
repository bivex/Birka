from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional


class MidiPlayer:
    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None

    def play(self, path: Path) -> bool:
        self.stop()
        command = _select_player_command(path)
        if command is None:
            return False
        self._process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
        self._process = None


def _select_player_command(path: Path) -> Optional[list[str]]:
    if shutil.which("timidity"):
        return ["timidity", "-id", str(path)]
    if shutil.which("fluidsynth"):
        return ["fluidsynth", "-i", str(path)]
    return None
