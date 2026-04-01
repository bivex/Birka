from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional, Tuple

FLUIDSYNTH_GAIN = "0.8"
LOUDNORM_TARGET = "loudnorm=I=-16:TP=-1.5:LRA=11"
MP3_BITRATE = "320k"


def render_midi_to_mp3(midi_path: Path, output_dir: Path) -> Optional[Path]:
    """Render a MIDI file to MP3 via piped fluidsynth→ffmpeg pipeline."""
    soundfont = _find_soundfont()
    if soundfont is None or shutil.which("ffmpeg") is None:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = output_dir / (midi_path.stem + ".mp3")
    synth_cmd = _synth_command(soundfont, midi_path)

    stats = _piped_measure(synth_cmd)
    af = _build_loudnorm_filter(stats)
    if not _piped_encode(synth_cmd, af, mp3_path):
        return None
    return mp3_path


def render_midi_to_wav(midi_path: Path, output_path: Path) -> bool:
    """Render a single MIDI to WAV via fluidsynth. No normalization (fast)."""
    soundfont = _find_soundfont()
    if soundfont is None or shutil.which("fluidsynth") is None:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "fluidsynth",
        "-i",
        "-ni",
        "-g",
        FLUIDSYNTH_GAIN,
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
    """Render multiple MIDI files to MP3 in parallel using all CPU cores."""
    soundfont = _find_soundfont()
    if soundfont is None or shutil.which("ffmpeg") is None:
        return [], list(midi_paths)

    output_dir.mkdir(parents=True, exist_ok=True)
    max_workers = min(len(midi_paths), os.cpu_count() or 4)
    results: List[Tuple[Path, Optional[Path]]] = []

    def _render_one(midi_path: Path) -> Tuple[Path, Optional[Path]]:
        mp3_path = output_dir / (midi_path.stem + ".mp3")
        synth_cmd = _synth_command(soundfont, midi_path)
        stats = _piped_measure(synth_cmd)
        af = _build_loudnorm_filter(stats)
        if _piped_encode(synth_cmd, af, mp3_path):
            return midi_path, mp3_path
        return midi_path, None

    completed = 0
    total = len(midi_paths)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_render_one, p): p for p in midi_paths}
        for future in as_completed(futures):
            midi_path, mp3_path = future.result()
            completed += 1
            if on_progress:
                on_progress(completed, total, midi_path, mp3_path is not None)
            results.append((midi_path, mp3_path))

    successful = [mp3 for _, mp3 in results if mp3 is not None]
    failed = [mid for mid, mp3 in results if mp3 is None]
    return successful, failed


def _synth_command(soundfont: Path, midi_path: Path) -> List[str]:
    """Build fluidsynth command that outputs WAV to stdout."""
    return [
        "fluidsynth",
        "-i",
        "-ni",
        "-g",
        FLUIDSYNTH_GAIN,
        "-F",
        "-",
        str(soundfont),
        str(midi_path),
    ]


def _piped_measure(synth_cmd: List[str]) -> Optional[dict]:
    """Run fluidsynth | ffmpeg loudnorm measure, return stats dict or None."""
    synth = subprocess.Popen(
        synth_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-af",
            f"{LOUDNORM_TARGET}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        stdin=synth.stdout,
        capture_output=True,
        text=True,
    )
    synth.wait()
    return _parse_loudnorm_stats(result.stderr)


def _piped_encode(
    synth_cmd: List[str],
    audio_filter: Optional[str],
    mp3_path: Path,
) -> bool:
    """Run fluidsynth | ffmpeg normalize+encode, write MP3 to mp3_path."""
    synth = subprocess.Popen(
        synth_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    encode_cmd = ["ffmpeg", "-y", "-f", "wav", "-i", "pipe:0"]
    if audio_filter:
        encode_cmd += ["-af", audio_filter]
    encode_cmd += ["-b:a", MP3_BITRATE, str(mp3_path)]

    result = subprocess.run(
        encode_cmd,
        stdin=synth.stdout,
        capture_output=True,
        text=True,
    )
    synth.wait()
    if result.returncode != 0 or not mp3_path.exists():
        mp3_path.unlink(missing_ok=True)
        return False
    return True


def _build_loudnorm_filter(stats: Optional[dict]) -> Optional[str]:
    """Build ffmpeg loudnorm filter string from measured stats."""
    if stats is None:
        return None
    return (
        f"{LOUDNORM_TARGET}:"
        f"measured_I={stats['input_i']}:"
        f"measured_LRA={stats['input_lra']}:"
        f"measured_TP={stats['input_tp']}:"
        f"measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:"
        f"linear=true"
    )


def _normalize_wav(wav_path: Path) -> bool:
    if shutil.which("ffmpeg") is None or not wav_path.exists():
        return False

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

    af = _build_loudnorm_filter(_parse_loudnorm_stats(result.stderr))
    if af is None:
        return False

    tmp_normalized = wav_path.with_suffix(".normalized.wav")
    apply_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(wav_path),
        "-af",
        f"{af}:print_format=json",
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
