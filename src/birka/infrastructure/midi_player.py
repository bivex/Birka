from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


class MidiPlayer:
    STARTUP_DELAY_SECONDS = 0.15

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None

    def play(self, path: Path) -> bool:
        self.stop()
        command = _select_player_command(path)
        print(f"[MIDI] play request: path={path} command={command}")
        if command is None:
            return False
        self._process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        print(f"[MIDI] started pid={self._process.pid}")
        time.sleep(self.STARTUP_DELAY_SECONDS)
        if self._process.poll() is not None:
            stdout, stderr = self._process.communicate(timeout=2)
            if stdout:
                print(f"[MIDI] stdout: {stdout}")
            if stderr:
                print(f"[MIDI] stderr: {stderr}")
            self._process = None
            return False
        return True

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            print(f"[MIDI] stopping pid={self._process.pid}")
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                print(f"[MIDI] kill pid={self._process.pid}")
                self._process.kill()
                self._process.wait(timeout=2)
        self._process = None

    def is_available(self) -> bool:
        return _select_player_command(Path("test.mid")) is not None


def _select_player_command(path: Path) -> Optional[list[str]]:
    if shutil.which("timidity"):
        return ["timidity", "-id", str(path)]
    if shutil.which("fluidsynth"):
        soundfont = _find_soundfont()
        if soundfont is None:
            print("[MIDI] fluidsynth found but no soundfont available.")
            return None
        command = ["fluidsynth", "-i", "-ni", "-g", "0.8", str(soundfont), str(path)]
        if sys.platform == "darwin":
            command.insert(1, "-a")
            command.insert(2, "coreaudio")
        return command
    return None


def _find_soundfont() -> Optional[Path]:
    env = os.environ.get("BIRKA_SOUNDFONT")
    if env and Path(env).exists():
        return Path(env)
    candidates = [
        Path("/Volumes/External/Code/Birka/data/FluidR3 GM.sf2"),
        Path("/Volumes/External/Code/Birka/data/FluidR3_GM.sf2"),
        Path("/opt/homebrew/share/soundfonts/FluidR3_GM.sf2"),
        Path("/opt/homebrew/share/soundfonts/default.sf2"),
        Path("/usr/local/share/soundfonts/FluidR3_GM.sf2"),
        Path("/usr/local/share/soundfonts/default.sf2"),
    ]
    for path in candidates:
        if path.exists():
            return path
    for base in [
        Path("/opt/homebrew/share/soundfonts"),
        Path("/usr/local/share/soundfonts"),
    ]:
        if base.exists():
            for sf2 in base.glob("*.sf2"):
                return sf2
    return None
