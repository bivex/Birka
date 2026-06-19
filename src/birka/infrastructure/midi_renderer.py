from __future__ import annotations

import json
import math
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

import logging

logger_sfizz = logging.getLogger("birka.midi_renderer.sfizz")

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

# sfizz backend (SFZ instruments via pysfizz). Opt-in via BIRKA_BACKEND=sfizz.
# pysfizz ships as a submodule under modules/pysfizz and is SFZ-only, so it is
# independent of the SF2 soundfont used by tsf. The native _sfizz extension is
# NOT built by default; the import below fails gracefully when unbuilt.
#
# We prefer an *installed* pysfizz (pip-installed into the venv, which has the
# compiled _sfizz extension). We only fall back to the source dir under
# modules/pysfizz when that dir actually contains a compiled _sfizz.* extension
# -- otherwise it would shadow the working installed package (pure-Python
# __init__ with no .so => ImportError "partially initialized module").
try:
    try:
        import pysfizz  # noqa: F401
        from pysfizz import _sfizz as _sfizz_check  # noqa: F401
    except ImportError:
        import sys as _sys
        _sfizz_root = Path(__file__).resolve().parent
        _added = False
        for _ancestor in _sfizz_root.parents:
            _candidate = _ancestor / "modules" / "pysfizz"
            _pkg = _candidate / "pysfizz"
            if (_pkg / "__init__.py").exists():
                # Only use the source dir if it actually has a compiled extension,
                # so we never shadow the installed package with a source-only copy.
                if any(_pkg.glob("_sfizz*.so")) or any(_pkg.glob("_sfizz*.pyd")):
                    if str(_candidate) not in _sys.path:
                        _sys.path.insert(0, str(_candidate))
                    _added = True
                break
        if _added:
            import pysfizz  # noqa: F401
            from pysfizz import _sfizz as _sfizz_check  # noqa: F401
        else:
            raise ImportError("pysfizz not importable (no compiled _sfizz extension)")
    _SFIZZ_AVAILABLE = True
except Exception:
    _SFIZZ_AVAILABLE = False

FLUIDSYNTH_GAIN = "0.8"
LOUDNORM_TARGET = "loudnorm=I=-16:TP=-1.5:LRA=11"
MP3_BITRATE = "320k"
PREVIEW_SAMPLE_RATE = 22050
PREVIEW_MP3_BITRATE = "96k"
PREVIEW_POLYPHONY = 64
_TSF_BUFFER_FRAMES = 2048
_SFIZZ_BLOCK_FRAMES = 1024
_VALID_BACKENDS = {"auto", "tsf", "sfizz", "fluidsynth"}


def _selected_backend() -> str:
    """Backend requested via BIRKA_BACKEND (one of _VALID_BACKENDS).

    "auto" is returned for unknown/empty values and for a requested backend
    whose dependency is not available (so callers can fall back).
    """
    choice = os.environ.get("BIRKA_BACKEND", "auto").strip().lower()
    if choice == "sfizz":
        return "sfizz" if _SFIZZ_AVAILABLE else "auto"
    if choice == "tsf":
        return "tsf" if _TSF_AVAILABLE else "auto"
    if choice == "fluidsynth":
        return "fluidsynth"
    return "auto"


def _resolve_backend() -> str:
    """Concrete backend: tsf | sfizz | fluidsynth | none.

    Honours BIRKA_BACKEND. "auto" keeps the historical default (tsf, then
    fluidsynth) and deliberately does NOT auto-promote sfizz, which stays
    opt-in only so behaviour is unchanged unless explicitly requested.
    """
    requested = _selected_backend()
    if requested != "auto":
        return requested
    if _TSF_AVAILABLE:
        return "tsf"
    if shutil.which("fluidsynth") is not None:
        return "fluidsynth"
    return "none"


def _backend_name() -> str:
    """Resolved backend name; kept for tests and diagnostics."""
    return _resolve_backend()


def _synth_to_wav_for_backend(
    backend: str, midi_path: Path, tmp_wav: Path, sample_rate: int, polyphony: int
) -> bool:
    """Synthesize one MIDI to a temp WAV using the given backend.

    Returns False if the backend is unavailable or synthesis fails. sfizz falls
    back to tsf/fluidsynth when no SFZ bank is found.
    """
    if backend == "sfizz":
        sfz = _find_sfz()
        if sfz is not None:
            return _synth_sfizz_to_wav(
                sfz, midi_path, tmp_wav, sample_rate=sample_rate, polyphony=polyphony
            )
        backend = "tsf" if _TSF_AVAILABLE else "fluidsynth"

    soundfont = _find_soundfont()
    if soundfont is None:
        return False
    if backend == "tsf" and _TSF_AVAILABLE:
        return _synth_tsf_to_wav(
            soundfont, midi_path, tmp_wav, sample_rate=sample_rate, polyphony=polyphony
        )
    if backend == "fluidsynth" and shutil.which("fluidsynth") is not None:
        return _synth_to_wav(
            soundfont, midi_path, tmp_wav, sample_rate=sample_rate, polyphony=polyphony
        )
    return False


def render_midi_to_mp3(midi_path: Path, output_dir: Path) -> Optional[Path]:
    """Render a MIDI file to MP3 via the selected backend + ffmpeg loudnorm."""
    if shutil.which("ffmpeg") is None:
        return None
    backend = _resolve_backend()
    if backend == "none":
        return None
    if backend == "tsf" and _TSF_AVAILABLE:
        soundfont = _find_soundfont()
        if soundfont is None:
            return None
        return _render_tsf_to_mp3(midi_path, output_dir, soundfont)
    output_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = output_dir / (midi_path.stem + ".mp3")
    tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
    try:
        if not _synth_to_wav_for_backend(backend, midi_path, tmp_wav, 44100, 256):
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
    """Render a single MIDI to WAV via the selected backend. No normalization."""
    backend = _resolve_backend()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if backend == "sfizz":
        sfz = _find_sfz()
        if sfz is not None:
            return _synth_sfizz_to_wav(
                sfz, midi_path, output_path, sample_rate=sample_rate, polyphony=polyphony
            )
        # no SFZ bank -> fall through to auto resolution
        backend = "tsf" if _TSF_AVAILABLE else "fluidsynth"

    soundfont = _find_soundfont()
    if soundfont is None:
        return False
    if backend == "tsf" and _TSF_AVAILABLE:
        return _synth_tsf_to_wav(
            soundfont,
            midi_path,
            output_path,
            sample_rate=sample_rate,
            polyphony=polyphony,
        )
    if backend == "fluidsynth" and shutil.which("fluidsynth") is not None:
        return _synth_to_wav(
            soundfont, midi_path, output_path, sample_rate=sample_rate, polyphony=polyphony
        )
    return False


def render_midi_preview_mp3(
    midi_path: Path,
    output_path: Path,
    sample_rate: int = PREVIEW_SAMPLE_RATE,
    polyphony: int = PREVIEW_POLYPHONY,
    bitrate: str = PREVIEW_MP3_BITRATE,
) -> bool:
    """Render a MIDI to a small MP3 for fast preview listening.

    Trades audio quality for speed: low sample rate, reduced polyphony, single
    ffmpeg pass (no loudness normalization). Intended for quick listening, not
    for the final rendered library output.
    """
    if shutil.which("ffmpeg") is None:
        return False
    backend = _resolve_backend()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
    try:
        if backend == "sfizz":
            sfz = _find_sfz()
            if sfz is not None:
                if not _synth_sfizz_to_wav(
                    sfz, midi_path, tmp_wav, sample_rate=sample_rate, polyphony=polyphony
                ):
                    return False
                return _encode_mp3(tmp_wav, None, output_path, bitrate=bitrate)
            backend = "tsf" if _TSF_AVAILABLE else "fluidsynth"
        soundfont = _find_soundfont()
        if soundfont is None:
            return False
        if backend == "tsf" and _TSF_AVAILABLE:
            if not _synth_tsf_to_wav(
                soundfont,
                midi_path,
                tmp_wav,
                sample_rate=sample_rate,
                polyphony=polyphony,
            ):
                return False
        else:
            if shutil.which("fluidsynth") is None:
                return False
            if not _synth_to_wav(
                soundfont,
                midi_path,
                tmp_wav,
                sample_rate=sample_rate,
                polyphony=polyphony,
            ):
                return False
        # Single-pass encode, no loudnorm measurement step.
        return _encode_mp3(tmp_wav, None, output_path, bitrate=bitrate)
    finally:
        tmp_wav.unlink(missing_ok=True)


def render_midi_to_mp3_batch(
    midi_paths: List[Path],
    output_dir: Path,
    on_progress: Optional[Callable[[int, int, Path, bool], None]] = None,
) -> Tuple[List[Path], List[Path]]:
    """Render multiple MIDI files to MP3 in parallel using all CPU cores."""
    if shutil.which("ffmpeg") is None:
        return [], list(midi_paths)
    backend = _resolve_backend()
    if backend == "none":
        return [], list(midi_paths)
    if not midi_paths:
        return [], []
    if backend == "tsf" and _TSF_AVAILABLE:
        soundfont = _find_soundfont()
        if soundfont is None:
            return [], list(midi_paths)
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
            if not _synth_to_wav_for_backend(backend, midi_path, tmp_wav, 44100, 256):
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


def render_midi_to_wav_batch(
    midi_paths: List[Path],
    output_dir: Path,
    on_progress: Optional[Callable[[int, int, Path, bool], None]] = None,
    sample_rate: int = 44100,
    polyphony: int = 256,
) -> Tuple[List[Path], List[Path]]:
    """Render multiple MIDI files to WAV in parallel. No normalization (fast)."""
    backend = _resolve_backend()
    if backend == "none":
        return [], list(midi_paths)
    # sfizz needs an SFZ bank; tsf/fluidsynth need an SF2 soundfont.
    if backend != "sfizz" and _find_soundfont() is None:
        return [], list(midi_paths)
    if backend == "sfizz" and _find_sfz() is None:
        return [], list(midi_paths)
    if not midi_paths:
        return [], []
    output_dir.mkdir(parents=True, exist_ok=True)
    max_workers = min(len(midi_paths), os.cpu_count() or 4)
    results: List[Tuple[Path, Optional[Path]]] = []

    def _render_one(midi_path: Path) -> Tuple[Path, Optional[Path]]:
        wav_path = output_dir / (midi_path.stem + ".wav")
        if render_midi_to_wav(
            midi_path,
            wav_path,
            sample_rate=sample_rate,
            polyphony=polyphony,
        ):
            return midi_path, wav_path
        return midi_path, None

    completed = 0
    total = len(midi_paths)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_render_one, p): p for p in midi_paths}
        for future in as_completed(futures):
            midi_path, wav_path = future.result()
            completed += 1
            if on_progress:
                on_progress(completed, total, midi_path, wav_path is not None)
            results.append((midi_path, wav_path))

    successful = [wav for _, wav in results if wav is not None]
    failed = [mid for mid, wav in results if wav is None]
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
    if not midi_paths:
        return [], []
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


def _soft_clip_to_int16(samples: List[float]) -> List[int]:
    """Convert float samples to 16-bit ints with tanh soft-clipping.

    When many synth voices sum together the float output can exceed [-1.0, 1.0].
    A hard clamp pins those samples to the 16-bit ceiling/floor, producing
    audible clicks (crackle). tanh is applied to the *whole* signal so the
    curve is continuous and monotonic: near-linear at low levels (identity at
    the origin, slope 1) and smoothly limiting at high levels, so nothing is
    ever pinned flat to the ceiling.

    Applying tanh only above a threshold (e.g. >1.0) is *wrong*: it creates a
    notch at the crossing (just-under-1.0 -> ~32439, just-over-1.0 -> ~25000),
    which is itself a click.
    """
    return [
        max(-32768, min(32767, int(math.tanh(s) * 32767.0)))
        for s in samples
    ]


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

    # Soft-clip (tanh) + 16-bit stereo WAV. Shared with the sfizz renderer via
    # _write_int16_wav so both backends produce identical output format.
    return _write_int16_wav(samples[:samples_needed], output_path, sample_rate)


def _write_int16_wav(
    interleaved: List[float], output_path: Path, sample_rate: int
) -> bool:
    """Soft-clip a flat interleaved float buffer and write a 16-bit stereo WAV.

    Shared by the tsf and sfizz renderers so both get identical output format
    (16-bit stereo) and the same crackle-preventing soft-clip.
    """
    int16_samples = _soft_clip_to_int16(interleaved)
    if not int16_samples:
        return False
    try:
        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)  # 16-bit (2 bytes)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack(f"<{len(int16_samples)}h", *int16_samples))
    except Exception:
        return False
    return output_path.exists()


def _synth_sfizz_to_wav(
    sfz_path: Path,
    midi_path: Path,
    output_path: Path,
    sample_rate: int = 44100,
    polyphony: int = 256,
) -> bool:
    """Render a MIDI to a 16-bit stereo WAV via the sfizz engine (SFZ bank).

    Mirrors _synth_tsf_to_wav's event-driven approach but drives pysfizz's
    low-level _sfizz.Synth block API. pysfizz renders planar (left, right)
    float32 blocks; we interleave them and reuse _write_int16_wav for the same
    16-bit output + soft-clip as tsf.

    Caveat: pysfizz does not expose program_change/bank selection. SFZ GM banks
    map channels/programs to regions up front, so program_change events are
    dropped (a debug warning is logged once). If a MIDI audibly mis-renders
    because of this, add a programChange binding in modules/pysfizz (the engine
    supports it at sfz::Synth::programChange).
    """
    try:
        import mido as _mido
        from pysfizz import _sfizz
    except Exception:
        return False

    try:
        mid = _mido.MidiFile(str(midi_path))
    except Exception:
        return False

    events: List[Tuple[float, _mido.Message]] = []
    abs_time = 0.0
    warned_program_change = False
    for msg in mid:
        abs_time += msg.time
        if msg.type in (
            "note_on",
            "note_off",
            "control_change",
            "pitchwheel",
        ):
            events.append((abs_time, msg))
        elif msg.type == "program_change" and not warned_program_change:
            logger_sfizz.debug(
                "sfizz: dropping program_change events (unsupported by pysfizz)"
            )
            warned_program_change = True

    total_seconds = max(1.0, mid.length + 2.0)
    frames_needed = int(total_seconds * sample_rate)

    try:
        synth = _sfizz.Synth(sample_rate, _SFIZZ_BLOCK_FRAMES)
        synth.enable_freewheeling()
        synth.set_num_voices(max(1, min(polyphony, 512)))
        if not synth.load_sfz_file(str(sfz_path)):
            return False

        interleaved: List[float] = []
        event_index = 0
        n_events = len(events)
        rendered = 0

        while rendered < frames_needed:
            block_start = rendered
            block_end = rendered + _SFIZZ_BLOCK_FRAMES
            # Dispatch every event whose sample time falls within this block,
            # computing the per-event sample delay relative to block_start.
            while event_index < n_events:
                msg_time, msg = events[event_index]
                event_frame = int(msg_time * sample_rate)
                if event_frame >= block_end:
                    break
                delay = max(0, min(_SFIZZ_BLOCK_FRAMES, event_frame - block_start))
                if msg.type == "note_on" and msg.velocity > 0:
                    synth.note_on(delay, msg.note, msg.velocity)
                elif msg.type in ("note_off", "note_on"):
                    synth.note_off(delay, msg.note, 0)
                elif msg.type == "control_change":
                    synth.cc(delay, msg.control, msg.value)
                elif msg.type == "pitchwheel":
                    # mido pitch is -8192..8191; sfizz expects the same range.
                    synth.pitch_wheel(delay, msg.pitch)
                event_index += 1

            left, right = synth.render_block()
            for i in range(len(left)):
                interleaved.append(float(left[i]))
                interleaved.append(float(right[i]))
            rendered = len(interleaved) // 2
    except Exception:
        return False

    channels = 2
    samples_needed = frames_needed * channels
    return _write_int16_wav(interleaved[:samples_needed], output_path, sample_rate)


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
    bitrate: str = MP3_BITRATE,
) -> bool:
    """Run ffmpeg to normalize and encode a WAV file to MP3."""
    encode_cmd = ["ffmpeg", "-y", "-i", str(wav_path)]
    if audio_filter:
        encode_cmd += ["-af", audio_filter]
    encode_cmd += ["-b:a", bitrate, str(mp3_path)]

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


def _find_sfz() -> Optional[Path]:
    """Locate an SFZ instrument bank for the sfizz backend (SFZ-only engine).

    Independent of _find_soundfont() because sfizz cannot load .sf2. Honours
    BIRKA_SFZ, then common SFZ locations.
    """
    env = os.environ.get("BIRKA_SFZ")
    if env and Path(env).exists() and Path(env).suffix.lower() == ".sfz":
        return Path(env)
    candidates = [
        Path("/Volumes/External/Code/Birka/data/GeneralUser GS.sfz"),
        Path("/Volumes/External/Code/Birka/data/GeneralUserGS.sfz"),
        Path("/opt/homebrew/share/sfz/GeneralUser GS.sfz"),
    ]
    for path in candidates:
        if path.exists():
            return path
    for base in [
        Path("/Volumes/External/Code/Birka/data"),
        Path("/opt/homebrew/share/sfz"),
    ]:
        if base.exists():
            for sfz in base.rglob("*.sfz"):
                return sfz
    return None
