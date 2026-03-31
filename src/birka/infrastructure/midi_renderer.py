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
    """Render a MIDI file to MP3 via fluidsynth (MIDI→WAV) then ffmpeg (WAV→MP3).

    Uses piped pipeline: fluidsynth stdout → ffmpeg loudnorm → ffmpeg MP3.
    No intermediate WAV files written to disk.
    """
    soundfont = _find_soundfont()
    if soundfont is None:
        return None
    if shutil.which("ffmpeg") is None:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = output_dir / (midi_path.stem + ".mp3")

    # Pass 1: fluidsynth → ffmpeg loudnorm measure (pipe, no temp file)
    measure_cmd = [
        "fluidsynth",
        "-i",
        "-ni",
        "-g",
        "0.8",
        "-F",
        "-",
        str(soundfont),
        str(midi_path),
    ]
    norm_filter = "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json"

    synth_proc = subprocess.Popen(
        measure_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    measure_ffmpeg = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-af",
            norm_filter,
            "-f",
            "null",
            "-",
        ],
        stdin=synth_proc.stdout,
        capture_output=True,
        text=True,
    )
    synth_proc.wait()

    stats = _parse_loudnorm_stats(measure_ffmpeg.stderr)

    # Pass 2: fluidsynth → ffmpeg normalize+encode (pipe, no temp files)
    synth_proc2 = subprocess.Popen(
        measure_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if stats is not None:
        af = (
            f"loudnorm=I=-16:TP=-1.5:LRA=11:"
            f"measured_I={stats['input_i']}:"
            f"measured_LRA={stats['input_lra']}:"
            f"measured_TP={stats['input_tp']}:"
            f"measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:"
            f"linear=true"
        )
        encode_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-af",
            af,
            "-b:a",
            "320k",
            str(mp3_path),
        ]
    else:
        encode_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-b:a",
            "320k",
            str(mp3_path),
        ]

    encode_result = subprocess.run(
        encode_cmd,
        stdin=synth_proc2.stdout,
        capture_output=True,
        text=True,
    )
    synth_proc2.wait()

    if encode_result.returncode != 0 or not mp3_path.exists():
        mp3_path.unlink(missing_ok=True)
        return None

    return mp3_path


def render_midi_to_wav(midi_path: Path, output_path: Path) -> bool:
    """Render a single MIDI to WAV via fluidsynth. No normalization (fast)."""
    soundfont = _find_soundfont()
    if soundfont is None:
        return False
    if shutil.which("fluidsynth") is None:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "fluidsynth",
        "-i",
        "-ni",
        "-g",
        "0.8",
        "-F",
        str(output_path),
        str(soundfont),
        str(midi_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and output_path.exists()


def render_midi_to_mp3_batch(
    midi_paths: List[Path],
    output_dir: Path,
    on_progress: Optional[Callable[[int, int, Path, bool], None]] = None,
) -> Tuple[List[Path], List[Path]]:
    """Render multiple MIDI files to MP3 in parallel using all available CPU cores.

    Uses piped pipeline per file: fluidsynth → ffmpeg (no intermediate WAV on disk).

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
        mp3_path = output_dir / (midi_path.stem + ".mp3")
        synth_cmd = [
            "fluidsynth",
            "-i",
            "-ni",
            "-g",
            "0.8",
            "-F",
            "-",
            str(soundfont),
            str(midi_path),
        ]

        # Pass 1: measure loudness
        synth1 = subprocess.Popen(
            synth_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        measure = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                "-f",
                "null",
                "-",
            ],
            stdin=synth1.stdout,
            capture_output=True,
            text=True,
        )
        synth1.wait()
        stats = _parse_loudnorm_stats(measure.stderr)

        # Pass 2: normalize + encode
        synth2 = subprocess.Popen(
            synth_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if stats is not None:
            af = (
                f"loudnorm=I=-16:TP=-1.5:LRA=11:"
                f"measured_I={stats['input_i']}:"
                f"measured_LRA={stats['input_lra']}:"
                f"measured_TP={stats['input_tp']}:"
                f"measured_thresh={stats['input_thresh']}:"
                f"offset={stats['target_offset']}:"
                f"linear=true"
            )
            encode_cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-af",
                af,
                "-b:a",
                "320k",
                str(mp3_path),
            ]
        else:
            encode_cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-b:a",
                "320k",
                str(mp3_path),
            ]
        enc = subprocess.run(
            encode_cmd,
            stdin=synth2.stdout,
            capture_output=True,
            text=True,
        )
        synth2.wait()

        if enc.returncode != 0 or not mp3_path.exists():
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
