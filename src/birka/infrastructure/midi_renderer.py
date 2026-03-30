from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional


def render_midi_to_mp3(midi_path: Path, output_dir: Path) -> Optional[Path]:
    """Render a MIDI file to MP3 via fluidsynth (MIDI→WAV) then ffmpeg (WAV→MP3)."""
    soundfont = _find_soundfont()
    if soundfont is None:
        return None
    if shutil.which("ffmpeg") is None:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / (midi_path.stem + ".wav")
    mp3_path = output_dir / (midi_path.stem + ".mp3")

    cmd_wav = [
        "fluidsynth", "-i", "-ni", "-g", "0.8",
        "-F", str(wav_path),
        str(soundfont), str(midi_path),
    ]
    result = subprocess.run(cmd_wav, capture_output=True, text=True)
    if result.returncode != 0 or not wav_path.exists():
        wav_path.unlink(missing_ok=True)
        return None

    cmd_mp3 = ["ffmpeg", "-y", "-i", str(wav_path), "-b:a", "320k", str(mp3_path)]
    result = subprocess.run(cmd_mp3, capture_output=True, text=True)
    wav_path.unlink(missing_ok=True)
    if result.returncode != 0 or not mp3_path.exists():
        mp3_path.unlink(missing_ok=True)
        return None

    return mp3_path


def _find_soundfont() -> Optional[Path]:
    import os
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
    for base in [Path("/opt/homebrew/share/soundfonts"), Path("/usr/local/share/soundfonts")]:
        if base.exists():
            for sf2 in base.glob("*.sf2"):
                return sf2
    return None
