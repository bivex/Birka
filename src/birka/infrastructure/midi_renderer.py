from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional, Tuple

try:
    try:
        from tsfpy import TinySoundFont, TSF_STEREO_INTERLEAVED
    except ImportError:
        import sys
        # Search parent directories for tsfpy.py and add its directory to sys.path
        _current = Path(__file__).resolve().parent
        _found = False
        for _parent in _current.parents:
            if (_parent / "tsfpy.py").exists():
                if str(_parent) not in sys.path:
                    sys.path.insert(0, str(_parent))
                _found = True
                break
        if not _found:
            raise ImportError("tsfpy.py not found in parent directories")
        from tsfpy import TinySoundFont, TSF_STEREO_INTERLEAVED

    _TSF_AVAILABLE = True
except ImportError:
    TinySoundFont = None
    TSF_STEREO_INTERLEAVED = 0
    _TSF_AVAILABLE = False

FLUIDSYNTH_GAIN = "0.8"
LOUDNORM_TARGET = "loudnorm=I=-16:TP=-1.5:LRA=11"
MP3_BITRATE = "320k"
_TSF_BUFFER_FRAMES = 2048


def _backend_name() -> str:
    return "tsf" if _TSF_AVAILABLE else "fluidsynth"


def render_midi_to_mp3(midi_path: Path, output_dir: Path) -> Optional[Path]:
    """Render a MIDI file to MP3 via fluidsynth/tsf + ffmpeg loudness normalization."""
    soundfont = _find_soundfont()
    if soundfont is None or shutil.which("ffmpeg") is None:
        return None
    if _TSF_AVAILABLE:
        return _render_tsf_to_mp3(midi_path, output_dir, soundfont)
    if shutil.which("fluidsynth") is None:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = output_dir / (midi_path.stem + ".mp3")
    tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
    try:
        if not _synth_to_wav(soundfont, midi_path, tmp_wav):
            return None
        stats = _measure_stats(tmp_wav)
        af = _build_loudnorm_filter(stats)
        if not _encode_mp3(tmp_wav, af, mp3_path):
            return None
        return mp3_path
    finally:
        tmp_wav.unlink(missing_ok=True)


def render_midi_to_wav(
    midi_path: Path, output_path: Path, sample_rate: int = 44100, polyphony: int = 256
) -> bool:
    """Render a single MIDI to WAV via fluidsynth/tsf. No normalization (fast)."""
    soundfont = _find_soundfont()
    if soundfont is None:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if _TSF_AVAILABLE:
        return _synth_tsf_to_wav(
            soundfont,
            midi_path,
            output_path,
            sample_rate=sample_rate,
            polyphony=polyphony,
        )
    if shutil.which("fluidsynth") is None:
        return False
    return _synth_to_wav(
        soundfont, midi_path, output_path, sample_rate=sample_rate, polyphony=polyphony
    )


def render_midi_to_mp3_batch(
    midi_paths: List[Path],
    output_dir: Path,
    on_progress: Optional[Callable[[int, int, Path, bool], None]] = None,
) -> Tuple[List[Path], List[Path]]:
    """Render multiple MIDI files to MP3 in parallel using all CPU cores."""
    soundfont = _find_soundfont()
    if soundfont is None or shutil.which("ffmpeg") is None:
        return [], list(midi_paths)
    if not _TSF_AVAILABLE and shutil.which("fluidsynth") is None:
        return [], list(midi_paths)
    if _TSF_AVAILABLE:
        return _render_tsf_to_mp3_batch(
            midi_paths, output_dir, soundfont, on_progress=on_progress
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    max_workers = min(len(midi_paths), os.cpu_count() or 4)
    results: List[Tuple[Path, Optional[Path]]] = []

    def _render_one(midi_path: Path) -> Tuple[Path, Optional[Path]]:
        mp3_path = output_dir / (midi_path.stem + ".mp3")
        tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
        try:
            if not _synth_to_wav(soundfont, midi_path, tmp_wav):
                return midi_path, None
            stats = _measure_stats(tmp_wav)
            af = _build_loudnorm_filter(stats)
            if _encode_mp3(tmp_wav, af, mp3_path):
                return midi_path, mp3_path
            return midi_path, None
        finally:
            tmp_wav.unlink(missing_ok=True)

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


def _synth_to_wav(
    soundfont: Path,
    midi_path: Path,
    output_path: Path,
    sample_rate: int = 44100,
    polyphony: int = 256,
) -> bool:
    """Run fluidsynth to render MIDI to a WAV file."""
    cmd = [
        "fluidsynth",
        "-i",
        "-ni",
        "-g",
        FLUIDSYNTH_GAIN,
        "-r",
        str(sample_rate),
        "-o",
        f"synth.polyphony={polyphony}",
        "-F",
        str(output_path),
        str(soundfont),
        str(midi_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and output_path.exists()


def _render_tsf_to_mp3(
    midi_path: Path,
    output_dir: Path,
    soundfont: Path,
) -> Optional[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = output_dir / (midi_path.stem + ".mp3")
    tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
    try:
        if not _synth_tsf_to_wav(soundfont, midi_path, tmp_wav):
            return None
        stats = _measure_stats(tmp_wav)
        af = _build_loudnorm_filter(stats)
        if not _encode_mp3(tmp_wav, af, mp3_path):
            return None
        return mp3_path
    finally:
        tmp_wav.unlink(missing_ok=True)


def _render_tsf_to_mp3_batch(
    midi_paths: List[Path],
    output_dir: Path,
    soundfont: Path,
    on_progress: Optional[Callable[[int, int, Path, bool], None]] = None,
) -> Tuple[List[Path], List[Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    max_workers = min(len(midi_paths), os.cpu_count() or 4)
    results: List[Tuple[Path, Optional[Path]]] = []

    def _render_one(midi_path: Path) -> Tuple[Path, Optional[Path]]:
        mp3_path = output_dir / (midi_path.stem + ".mp3")
        tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
        try:
            if not _synth_tsf_to_wav(soundfont, midi_path, tmp_wav):
                return midi_path, None
            stats = _measure_stats(tmp_wav)
            af = _build_loudnorm_filter(stats)
            if _encode_mp3(tmp_wav, af, mp3_path):
                return midi_path, mp3_path
            return midi_path, None
        finally:
            tmp_wav.unlink(missing_ok=True)

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


def _synth_tsf_to_wav(
    soundfont: Path,
    midi_path: Path,
    output_path: Path,
    sample_rate: int = 44100,
    polyphony: int = 256,
) -> bool:
    try:
        import mido as _mido

        mid = _mido.MidiFile(str(midi_path))
    except Exception:
        return False

    events: List[Tuple[float, _mido.Message]] = []
    abs_time = 0.0
    for msg in mid:
        abs_time += msg.time
        if msg.type in (
            "note_on",
            "note_off",
            "program_change",
            "control_change",
            "pitchwheel",
        ):
            events.append((abs_time, msg))

    # Add extra 2 seconds for final note decay, with a minimum duration of 1.0s
    total_seconds = max(1.0, mid.length + 2.0)
    frames_needed = int(total_seconds * sample_rate)

    try:
        with TinySoundFont(str(soundfont)) as synth:
            synth.set_output(
                sample_rate=sample_rate, output_mode=TSF_STEREO_INTERLEAVED
            )
            synth.set_max_voices(max(1, min(polyphony, 512)))

            # Initialize default presets for all 16 MIDI channels
            # Melodic channels default to program 0, channel 9 (percussion) defaults to bank 128 program 0.
            for ch in range(16):
                synth.channel_set_preset_number(ch, 0, midi_drums=(ch == 9))

            samples: List[float] = []
            event_index = 0
            channels = 2
            samples_needed = frames_needed * channels

            while len(samples) < samples_needed:
                current_frames = len(samples) // channels
                render_end = (current_frames + _TSF_BUFFER_FRAMES) / sample_rate
                while (
                    event_index < len(events) and events[event_index][0] <= render_end
                ):
                    _, msg = events[event_index]
                    if msg.type == "note_on" and msg.velocity > 0:
                        synth.channel_note_on(
                            msg.channel, msg.note, msg.velocity / 127.0
                        )
                    elif msg.type in ("note_off", "note_on"):
                        synth.channel_note_off(msg.channel, msg.note)
                    elif msg.type == "program_change":
                        synth.channel_set_preset_number(
                            msg.channel, msg.program, midi_drums=(msg.channel == 9)
                        )
                    elif msg.type == "control_change":
                        synth.channel_midi_control(msg.channel, msg.control, msg.value)
                    elif msg.type == "pitchwheel":
                        synth.channel_set_pitchwheel(msg.channel, msg.pitch + 8192)
                    event_index += 1
                samples.extend(synth.render_float(_TSF_BUFFER_FRAMES))
    except Exception:
        return False
    
    # Scale float samples [-1.0, 1.0] to 32-bit signed integers and clamp
    int32_samples = []
    for s in samples[:samples_needed]:
        if s >= 1.0:
            int32_samples.append(2147483647)
        elif s <= -1.0:
            int32_samples.append(-2147483648)
        else:
            int32_samples.append(int(s * 2147483647.0))

    if not int32_samples:
        return False
    try:
        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(4) # 32-bit (4 bytes)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack(f"<{len(int32_samples)}i", *int32_samples))
    except Exception:
        return False
    return output_path.exists()


def _measure_stats(wav_path: Path) -> Optional[dict]:
    """Run ffmpeg loudnorm measurement pass on a WAV file, return stats dict or None."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-af",
            f"{LOUDNORM_TARGET}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return _parse_loudnorm_stats(result.stderr)


def _encode_mp3(
    wav_path: Path,
    audio_filter: Optional[str],
    mp3_path: Path,
) -> bool:
    """Run ffmpeg to normalize and encode a WAV file to MP3."""
    encode_cmd = ["ffmpeg", "-y", "-i", str(wav_path)]
    if audio_filter:
        encode_cmd += ["-af", audio_filter]
    encode_cmd += ["-b:a", MP3_BITRATE, str(mp3_path)]

    result = subprocess.run(encode_cmd, capture_output=True, text=True)
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

    stats = _measure_stats(wav_path)
    af = _build_loudnorm_filter(stats)
    if af is None:
        return False

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
