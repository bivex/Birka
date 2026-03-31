from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional, Tuple


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
        "fluidsynth",
        "-i",
        "-ni",
        "-g",
        "0.8",
        "-F",
        str(wav_path),
        str(soundfont),
        str(midi_path),
    ]
    result = subprocess.run(cmd_wav, capture_output=True, text=True)
    if result.returncode != 0 or not wav_path.exists():
        wav_path.unlink(missing_ok=True)
        return None

    _normalize_wav(wav_path)

    cmd_mp3 = ["ffmpeg", "-y", "-i", str(wav_path), "-b:a", "320k", str(mp3_path)]
    result = subprocess.run(cmd_mp3, capture_output=True, text=True)
    wav_path.unlink(missing_ok=True)
    if result.returncode != 0 or not mp3_path.exists():
        mp3_path.unlink(missing_ok=True)
        return None

    return mp3_path


def render_midi_to_mp3_batch(
    midi_paths: List[Path],
    output_dir: Path,
    on_progress: Optional[Callable[[int, int, Path, bool], None]] = None,
) -> Tuple[List[Path], List[Path]]:
    """Render multiple MIDI files to MP3 in parallel using all available CPU cores.

    Args:
        midi_paths: List of MIDI file paths to render.
        output_dir: Directory for output MP3 files.
        on_progress: Optional callback(completed, total, midi_path, success).

    Returns:
        Tuple of (successful_output_paths, failed_midi_paths).
    """
    soundfont = _find_soundfont()
    if soundfont is None:
        return [], list(midi_paths)
    if shutil.which("ffmpeg") is None:
        return [], list(midi_paths)

    output_dir.mkdir(parents=True, exist_ok=True)
    max_workers = min(len(midi_paths), os.cpu_count() or 4)
    results: List[Tuple[Path, Optional[Path]]] = []

    def _render_one(midi_path: Path) -> Tuple[Path, Optional[Path]]:
        wav_path = output_dir / (midi_path.stem + ".wav")
        mp3_path = output_dir / (midi_path.stem + ".mp3")

        cmd_wav = [
            "fluidsynth",
            "-i",
            "-ni",
            "-g",
            "0.8",
            "-F",
            str(wav_path),
            str(soundfont),
            str(midi_path),
        ]
        result = subprocess.run(cmd_wav, capture_output=True, text=True)
        if result.returncode != 0 or not wav_path.exists():
            wav_path.unlink(missing_ok=True)
            return midi_path, None

        _normalize_wav(wav_path)

        cmd_mp3 = ["ffmpeg", "-y", "-i", str(wav_path), "-b:a", "320k", str(mp3_path)]
        result = subprocess.run(cmd_mp3, capture_output=True, text=True)
        wav_path.unlink(missing_ok=True)
        if result.returncode != 0 or not mp3_path.exists():
            mp3_path.unlink(missing_ok=True)
            return midi_path, None

        return midi_path, mp3_path

    completed = 0
    total = len(midi_paths)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_render_one, p): p for p in midi_paths}
        for future in as_completed(futures):
            midi_path, mp3_path = future.result()
            completed += 1
            success = mp3_path is not None
            if on_progress:
                on_progress(completed, total, midi_path, success)
            results.append((midi_path, mp3_path))

    successful = [mp3 for _, mp3 in results if mp3 is not None]
    failed = [mid for mid, mp3 in results if mp3 is None]
    return successful, failed


def _normalize_wav(wav_path: Path) -> bool:
    """Normalize WAV volume using ffmpeg loudnorm (EBU R128 two-pass).

    Returns True if normalization was applied, False if skipped (e.g. ffmpeg unavailable).
    """
    if shutil.which("ffmpeg") is None:
        return False
    if not wav_path.exists():
        return False

    # Pass 1: measure loudness stats
    measure_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(wav_path),
        "-af",
        "loudnorm=print_format=json",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(measure_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False

    # Parse JSON stats from stderr
    stats = _parse_loudnorm_stats(result.stderr)
    if stats is None:
        return False

    # Pass 2: apply normalization with measured stats
    af = (
        f"loudnorm=I=-16:TP=-1.5:LRA=11:"
        f"measured_I={stats['input_i']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:"
        f"linear=true:print_format=json"
    )
    tmp_normalized = wav_path.with_suffix(".normalized.wav")
    apply_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(wav_path),
        "-af",
        af,
        str(tmp_normalized),
    ]
    result = subprocess.run(apply_cmd, capture_output=True, text=True)
    if result.returncode != 0 or not tmp_normalized.exists():
        tmp_normalized.unlink(missing_ok=True)
        return False

    tmp_normalized.replace(wav_path)
    return True


def _parse_loudnorm_stats(stderr: str) -> Optional[dict]:
    """Extract loudnorm JSON statistics from ffmpeg stderr output."""
    # Find the JSON block between the last pair of braces
    matches = list(re.finditer(r"\{[^{}]+\}", stderr, re.DOTALL))
    if not matches:
        return None
    try:
        data = json.loads(matches[-1].group())
    except (json.JSONDecodeError, ValueError):
        return None
    required_keys = {
        "input_i",
        "input_lra",
        "input_tp",
        "input_thresh",
        "target_offset",
    }
    if not required_keys.issubset(data):
        return None
    return data


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
