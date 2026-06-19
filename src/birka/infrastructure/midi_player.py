from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence


def _render_midi_to_wav(midi_path: Path, wav_path: Path, soundfont: Path) -> bool:
    system = platform.system()
    if system == "Darwin":
        return _render_darwin(midi_path, wav_path, soundfont)
    return _render_generic(midi_path, wav_path, soundfont)


def _render_darwin(midi_path: Path, wav_path: Path, soundfont: Path) -> bool:
    abspath = midi_path.resolve()
    script = f'''
set midiFile to POSIX file "{abspath}" as alias
set sf2File to POSIX file "{soundfont.resolve()}" as alias
set outFile to POSIX file "{wav_path.resolve()}" as alias
tell application "Logic Pro"
    set doc to open midiFile
    set outFile to (export doc as AIFF file outFile)
    close doc saving no
end tell
'''
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".applescript", delete=False
        ) as sc:
            sc.write(script)
            sp = sc.name
        p = subprocess.run(["osascript", sp], capture_output=True, text=True)
        return p.returncode == 0 and wav_path.exists()
    except Exception:
        return False
    finally:
        try:
            os.unlink(sp)
        except Exception:
            pass


def _render_generic(midi_path: Path, wav_path: Path, soundfont: Path) -> bool:
    candidates = ["fluidsynth", "ffmpeg"]
    player = next((c for c in candidates if shutil.which(c)), None)
    if not player:
        return False
    subprocess.run(
        [player, "-y", "-i", str(midi_path), str(wav_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return wav_path.exists()
