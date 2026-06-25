from __future__ import annotations

import atexit
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import wave
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Any

import logging

logger = logging.getLogger("birka.midi_renderer")
logger_sfizz = logging.getLogger("birka.midi_renderer.sfizz")
logger_vst = logging.getLogger("birka.midi_renderer.vst")

try:
    try:
        from tsfpy import TinySoundFont, TSF_STEREO_INTERLEAVED
    except ImportError:
        import sys

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
    logger.info("tsf (TinySoundFont) backend available")
except ImportError as exc:
    TinySoundFont = None
    TSF_STEREO_INTERLEAVED = 0
    _TSF_AVAILABLE = False
    logger.info("tsf backend unavailable: %s", exc)

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
        _reason = "pysfizz not installed and no compiled _sfizz extension found"
        for _ancestor in _sfizz_root.parents:
            _candidate = _ancestor / "modules" / "pysfizz"
            _pkg = _candidate / "pysfizz"
            if (_pkg / "__init__.py").exists():
                _so_files = list(_pkg.glob("_sfizz*.so")) + list(
                    _pkg.glob("_sfizz*.pyd")
                )
                if _so_files:
                    if str(_candidate) not in _sys.path:
                        _sys.path.insert(0, str(_candidate))
                    _added = True
                    _reason = (
                        f"found compiled extension(s): {[f.name for f in _so_files]}"
                    )
                else:
                    _reason = (
                        f"found {_pkg} but no compiled _sfizz extension "
                        "(source-only, will not shadow installed package)"
                    )
                break
        if not _added:
            logger.info("sfizz backend unavailable: %s", _reason)
        raise ImportError(_reason)
    _SFIZZ_AVAILABLE = True
    logger.info("sfizz backend available (pysfizz + _sfizz loaded)")
except Exception as exc:
    _SFIZZ_AVAILABLE = False
    logger.info("sfizz backend unavailable: %s", exc)

FLUIDSYNTH_GAIN = "0.8"
LOUDNORM_TARGET = "loudnorm=I=-16:TP=-1.5:LRA=11"
MP3_BITRATE = "320k"
PREVIEW_SAMPLE_RATE = 22050
PREVIEW_MP3_BITRATE = "96k"
PREVIEW_POLYPHONY = 64
_TSF_BUFFER_FRAMES = 2048
_SFIZZ_BLOCK_FRAMES = 1024
_VALID_BACKENDS = {"auto", "tsf", "sfizz", "fluidsynth"}

_VST_SAMPLE_RATE = 96000
_VST_BUFFER_SIZE = 512

_VST_PLUGIN_PATHS = {
    "chow": "/Library/Audio/Plug-Ins/VST3/CHOWTapeModel.vst3",
    "sdrr": "/Library/Audio/Plug-Ins/VST3/SDRR2.vst3",
    "spiff": "/Library/Audio/Plug-Ins/VST3/spiff.vst3",
    "soothe": "/Library/Audio/Plug-Ins/VST3/soothe2.vst3",
    "pro_q": "/Library/Audio/Plug-Ins/VST3/FabFilter Pro-Q 4.vst3",
    "pro_mb": "/Library/Audio/Plug-Ins/VST3/FabFilter Pro-MB.vst3",
    "nova": "/Library/Audio/Plug-Ins/VST3/TDR Nova.vst3",
    "kot": "/Library/Audio/Plug-Ins/VST3/TDR Kotelnikov GE.vst3",
    "fresh": "/Library/Audio/Plug-Ins/VST3/Fresh Air.vst3",
    "cho": "/Library/Audio/Plug-Ins/VST3/TAL-Chorus-LX.vst3",
    "ste": "/Library/Audio/Plug-Ins/VST3/A1StereoControl.vst3",
    "reverb": "/Library/Audio/Plug-Ins/VST3/DragonflyHallReverb.vst3",
    "limiter": "/Library/Audio/Plug-Ins/VST3/FabFilter Pro-L 2.vst3",
}

# Apple Sound Check lands more musically here after AAC encode than a flat
# -14. Single source of truth shared by the VST two-pass calibration and the
# pedalboard fallback — keeps both paths at the same loudness target.
TARGET_LOUDNESS_LUFS = -13.8


def _make_temp_wav() -> Path:
    """Create a unique temp WAV path atomically.

    Uses tempfile.mkstemp (which creates the file and returns an open fd) rather
    than the deprecated tempfile.mktemp, which only returns a name and leaves a
    TOCTOU window where two batch-render threads could collide on the same path.
    We close the fd immediately; the synth/ffmpeg subprocess then writes to it.
    """
    fd, name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    return Path(name)


def _sfizz_self_masters() -> bool:
    """True when the sfizz backend will be used AND can actually render.

    The sfizz path (_synth_sfizz_to_wav) always applies its own loudness
    mastering (VST chain or pedalboard fallback) to TARGET_LOUDNESS_LUFS. So a
    subsequent ffmpeg loudnorm pass would re-normalize already-mastered audio
    (double normalization, undoing the limiter ceiling). This mirrors the
    fallback logic in _synth_to_wav_for_backend: sfizz only stays selected when
    an SFZ bank is present, otherwise it falls back to raw tsf/fluidsynth which
    DO need loudnorm.
    """
    return _resolve_backend() == "sfizz" and _find_sfz() is not None


def _measure_lufs(buf: np.ndarray, sample_rate: int) -> Optional[float]:
    """Integrated loudness in LUFS (ITU-R BS.1770).

    buf is (channels, samples). Uses pyloudnorm when available (true K-weighted
    measurement, matches ffmpeg ebur128 within ~0.1 dB); falls back to a 400 ms
    gated-block RMS approximation otherwise. Single implementation shared by the
    VST two-pass calibration and the pedalboard fallback so both paths measure
    loudness identically.
    """
    import numpy as np

    try:
        import pyloudnorm as pyln

        meter = pyln.Meter(int(sample_rate))
        # pyloudnorm expects (samples, channels); buf is (channels, samples).
        loudness = meter.integrated_loudness(np.asarray(buf).T)
        return float(loudness)
    except Exception:
        pass
    # Fallback: 400 ms block RMS approximation (kept for resilience). The
    # +6.1 dB term compensates for the missing K-weighting (head-shadow +
    # RLB) and block-gating empirically.
    try:
        mono = np.mean(np.asarray(buf).astype(np.float64), axis=0)
        win = max(1, int(0.4 * sample_rate))
        blocks = []
        for i in range(0, max(1, len(mono) - win), win):
            rms = float(np.sqrt(np.mean(mono[i : i + win] ** 2)))
            if rms > 1e-6:
                blocks.append(rms)
        if not blocks:
            return None
        mean_rms = float(np.mean(blocks))
        return 20.0 * np.log10(mean_rms) - 0.691 + 6.1
    except Exception:
        return None


def _df_size(m):
    # Dragonfly Size: 10-60m linear
    return max(0.0, min(1.0, (float(m) - 10.0) / 50.0))


def _df_decay(s):
    # Dragonfly Decay: 0.1-10s linear
    return max(0.0, min(1.0, (float(s) - 0.1) / 9.9))


def _df_predelay(ms):
    # Dragonfly Predelay: 0-100ms linear
    return max(0.0, min(1.0, float(ms) / 100.0))


def _df_lowcut(hz):
    # Dragonfly Low Cut: 0-200Hz linear
    return max(0.0, min(1.0, float(hz) / 200.0))


def _df_highcut(hz):
    # Dragonfly High Cut: 1000-16000Hz linear
    return max(0.0, min(1.0, (float(hz) - 1000.0) / 15000.0))


_VST_NEUTRAL_PRESET = {
    "bypass": False,
    "tape": {
        0: 0.889,
        1: 0.68,
        2: 1.0,
        16: 0.16,
        17: 0.22,
        18: 0.48,
        8: 0.52,
        9: 0.48,
    },
    # SDRR DESK as analog tone-glue (audio-engineer spec). Drive value 1.6,
    # Dynamics 2.2, Bass +0.4 dB, Treble -0.8 dB, Mix 32% — low mix keeps it
    # "analog density without dirt". DESK group-4 params: 37 Drive(0-10 lin),
    # 40 Compression(0-1 lin), 41 Bass(±12dB, 0.5=0), 42 Treble, 49 Mix(0-100%).
    "sdrr": {
        "bypass": False,
        "mode": 1.0,  # DESK
        "drive": 0.16,  # 1.6  (linear 0-10)
        "compression": 0.22,  # 2.2
        "bass": 0.5167,  # +0.4 dB  (0.5 + 0.4/24)
        "treble": 0.4667,  # -0.8 dB  (0.5 - 0.8/24)
        "mix": 0.20,  # 20% — lower saturation on master, more air between instruments
    },
    # spiff softer (premium): Amount -12% (cut value 1.2), Sensitivity 3.1.
    # spiff cut depth idx1 = value 0-10 linear; sens idx3 = 0-10 linear.
    "spiff": {"bypass": False, "mode": 0.0, "boost": 0.0, "cut": 0.12, "sens": 0.31},
    # soothe invisible (premium): Depth 18%, Sharpness 43%, Selectivity 22%,
    # Sensitivity 19%. These are slider positions (normalized). soothe's depth
    # reads as a dB reduction, but the engineer's intent is the slider %.
    "soothe": {
        "bypass": False,
        "depth": 0.18,
        "sharpness": 0.43,
        "selectivity": 0.22,
        "sens": 0.19,
    },
    # Pro-Q 4 tonal balance (premium, AirPods Max aware). HPF 24Hz, Low Shelf
    # 110Hz +1.1, Dyn bell 290Hz -1.2, Bell 3.4k -0.6, Bell 6.8k -0.7, plus a
    # 5th-band high shelf 13k +0.6 added on Band 5 (base 92). Shapes verified.
    "eq": {
        "hp_freq": 24.0,
        "b1_freq": 110.0,
        "b1_gain": 1.1,
        "b1_q": 0.7,
        "b1_dyn": 0.0,  # dyn bell moved to 290Hz on Band 2 (below)
        "b2_freq": 290.0,
        "b2_gain": -1.2,
        "b2_q": 1.0,
        "b2_dyn": -1.2,
        "b3_freq": 3400.0,
        "b3_gain": -0.6,
        "b3_q": 1.3,
        "b4_freq": 6800.0,
        "b4_gain": -0.7,
        "b4_q": 1.55,
        "b5_freq": 13000.0,
        "b5_gain": 0.6,
        "b5_q": 0.7,
    },
    # Dragonfly Hall reverb (master "glue" reverb — not a space creator).
    # Scene-depth is built per-band in Pro-Q, not here. This is a short
    # room-tone glue: mostly dry with a whisper of early reflections and
    # a tight tail. Large predelay smears transients; 18ms keeps attack
    # intact while adding just enough "air" around the sound field.
    "reverb": {
        "dry": 0.96,
        "early": 0.02,
        "late": 0.04,
        "size": _df_size(18),
        "decay": _df_decay(0.9),
        "predelay": _df_predelay(18),
        "diffuse": 0.65,
        "width": 1.0,
    },
    "chorus_wet": 0.0,
    # A1StereoControl removed: stereo widening now in Pro-Q 4 per-band
    # (B2 bass → Mid, B6 air → Side). No standalone stereo plugin needed.
    "fresh_air": {"bypass": False, "mid": 0.02, "high": 0.12},
    # Pro-MB multiband. Band 1 anchors the scene by compressing bass below
    # 120 Hz on the Mid signal (fundamental low-end that otherwise "floats" in
    # stereo and blurs the whole mix). Band 2 provides mid-bass glue. Both
    # verified against live parameter dump.
    "pro_mb": {
        "bypass": False,
        "params": {
            # Band 1: mono bass below 120 Hz — scene anchor.
            # Stereo Link is 100% (idx 19) / Mode Mid (idx 20) by default, so
            # the band processes the mono sum: compression only reacts to (and
            # reduces) center-image bass. Ratio 1.5:1 (idx 8 = 0.30) — gentle,
            # preserves bass transient weight. NB: Pro-MB's Ratio mapping is
            # LOGARITHMIC (0.40→2:1, 0.50→2.75:1, 0.60→4:1, 1.0→100:1), NOT
            # the linear (r-1)/99 formula an earlier comment assumed.
            0: 0.5,  # State = Enabled
            1: 0.0,  # Low Crossover 30 Hz (full lower edge, below bass)
            3: 0.2007,  # High Crossover 120 Hz (upper boundary of bass band)
            6: 0.70,  # Threshold -18 dB
            7: 0.40,  # Range -6 dB max GR (idx7 defaults to 0 dB = NO compression!)
            8: 0.30,  # Ratio 1.5:1 (log-mapped; verified live)
            9: 0.15,  # Attack 15%
            10: 0.30,  # Release 30%
            11: 0.125,  # Knee 6 dB (6/48)
            # Band 2: mid-bass glue (120–320 Hz region, unchanged)
            22: 0.5,
            23: 0.3427,
            28: 0.833,
            29: 0.45,
            30: 0.40,
            31: 0.3,
            32: 0.4,
            133: 0.5,
        },
    },
}


def _freq_to_val(f):
    f = max(10.0, min(30000.0, float(f)))
    return math.log10(f / 10.0) / math.log10(3000.0)


def _gain_to_val(g):
    g = max(-30.0, min(30.0, float(g)))
    return (g + 30.0) / 60.0


def _q_to_val(q):
    q = max(0.025, min(40.0, float(q)))
    return math.log10(q / 0.025) / math.log10(1600.0)


def _configure_kotelnikov_ge(kotelnikov):
    # TDR Kotelnikov GE (indices verified via live dump). Wide-band glue
    # compressor in PARALLEL (New York) configuration: a deeper wet threshold
    # creates real gain reduction, blended back against an unprocessed dry path.
    # Result: density and lift on sustained material while original transients
    # pass through the dry leg uncompressed.
    #
    # idx: 0 Threshold | 1 Peak-Crest | 2 Soft Knee | 3 Max GR | 4 Max GR En
    #      5 Ratio | 6 Attack | 7 Release Peak | 8 Release RMS | 10 Makeup
    #      11 Dry Mix | 12 Dry Wet (INVERTED: 0.0=100% wet, 1.0=0% wet/dry!)
    #      14 Out Gain | 15 SC HP Freq | 16 SC HP Slope
    #
    # Threshold mapping is LINEAR: 0.020 per dB (0.40 = -20 dB, verified).
    # Dry Mix (idx 11) is dry-leg attenuation in dB: 0.0=off (wet only),
    # 0.75=dry@-15dB (~18% power blend), 1.0=dry@0dB (full parallel).
    #
    # CRITICAL FIX: idx 12 (Dry Wet) was 1.0 = "0.0" = 0% processed signal,
    # so the entire compressor was bypassed. 0.0 = 100% wet (full processing).
    kotelnikov.set_parameter(0, 0.40)  # Threshold = -20 dB (deeper: GR ~3-4 dB)
    kotelnikov.set_parameter(1, 0.4091)  # Peak-Crest = RMS (smooth, musical)
    kotelnikov.set_parameter(2, 0.0625)  # Soft Knee = 1.0 (gentle onset)
    kotelnikov.set_parameter(5, 0.35)  # Ratio = 1.6:1 (premium "expensive movement")
    kotelnikov.set_parameter(6, 0.58)  # Attack ~28 ms (let transients breathe)
    kotelnikov.set_parameter(7, 0.50)  # Release Peak ~141 ms
    kotelnikov.set_parameter(8, 0.53)  # Release RMS ~230 ms (slow, smooth recovery)
    kotelnikov.set_parameter(
        10, 0.48
    )  # Makeup ~unity (iterated: 0.45→-1.0dB, 0.48 target ~0dB)
    kotelnikov.set_parameter(11, 0.75)  # Dry Mix = -15 dB (~18% dry, New York parallel)
    kotelnikov.set_parameter(12, 0.0)  # Dry Wet = 100% wet (FIXED: was 1.0 = bypassed)
    kotelnikov.set_parameter(14, 0.55)  # Out Gain = 0 dB
    kotelnikov.set_parameter(
        15, 0.65
    )  # SC HP Freq = 175 Hz (premium: bass looser/warmer)
    kotelnikov.set_parameter(16, 0.1667)  # SC HP Slope = 3.0


def _configure_limiter(limiter):
    # Pro-L 2 (indices verified via live dump). Premium streaming/mastering
    # settings tuned for AAC/Apple Music transparency.
    #
    # idx: 0 Gain | 1 Style | 2 Lookahead | 3 Attack | 4 Release
    #      9 Oversampling | 10 True Peak | 17 Bypass | 18 Output Level
    #      19 Lock Output | 22 True Peak Metering | 28 Loudness Meter Target (display only)
    #
    # Key fixes vs prior values:
    #   - Output Level 0.891 was -3.27 dBTP (range is -30..0, NOT linear gain).
    #     A near-silent master. -1.0 dBTP = norm 0.9667.
    #   - Lookahead 0.0360 was 0.18 ms (audible distortion on transients).
    #     1.0 ms (norm 0.2) is the transparent standard; 32x oversampling on a
    #     slow limiter is overkill and doubles render time without benefit.
    limiter.set_parameter(0, 0.0)  # Gain = 0 dB
    limiter.set_parameter(1, 0.0)  # Style = "Transparent" (cleanest for AAC)
    limiter.set_parameter(2, 0.28)  # Lookahead = 1.4 ms (premium transparent)
    limiter.set_parameter(3, 0.28)  # Attack ~60 ms (smooth gain riding)
    limiter.set_parameter(4, 0.3878)  # Release ~420 ms (smooth, no pumping)
    limiter.set_parameter(9, 0.33)  # Oversampling = 4x (source is 96k -> 384k
    #   internal, ample for true-peak detection; 8x doubled CPU for no audible
    #   gain at this base rate — the limiter is the chain's heaviest plugin)
    limiter.set_parameter(
        10, 1.0
    )  # True Peak Limiting = On (catches inter-sample peaks)
    limiter.set_parameter(17, 0.0)  # Bypass = Off
    limiter.set_parameter(
        18, 0.9667
    )  # Output Level = -1.0 dBTP (Apple Music / streaming safe)
    limiter.set_parameter(19, 1.0)  # Lock Output = Locked
    limiter.set_parameter(22, 1.0)  # True Peak Metering = Show True Peaks


def _configure_nova(nova):
    # TDR Nova — dynamic EQ micro-polish (audio-engineer spec). Sits after the
    # multiband glue as a "moving polish" layer: gentle dynamic cuts in the
    # low-mid / presence / air bands that only engage when energy builds up,
    # so the master stays open at low levels and controlled at high levels.
    #
    # Band layout (12 params/band): +0 selected, +1 Active, +2 Gain(-18..18),
    # +3 Q(0.1-6), +4 Freq(10-40k log: log10(f/10)/log10(4000)),
    # +5 Type(LoS/Bell/HiS), +6 Dyn(Off/On/Sticky), +7 Thr(-50..0),
    # +8 Ratio. Band bases: B1=0, B2=12, B3=24, B4=36. Bypass=62.
    nova.set_parameter(62, 0.0)  # Master Bypass = Off
    # Band 1: 240 Hz, Q 0.9, dyn cut -1.8 dB
    nova.set_parameter(1, 1.0)  # Active
    nova.set_parameter(4, 0.3832)  # Freq = 240 Hz
    nova.set_parameter(3, _nova_q_to_val(0.9))  # Q = 0.9
    nova.set_parameter(2, _nova_gain_to_val(-1.8))  # Gain = -1.8 dB
    nova.set_parameter(6, 0.5)  # Dyn = On
    nova.set_parameter(7, _nova_thr_to_val(-6.0))  # Threshold = -6 dB
    # Band 2: 2.8 kHz, Q 1.6, dyn cut -1.5 dB
    nova.set_parameter(13, 1.0)
    nova.set_parameter(16, 0.6794)  # Freq = 2.8 kHz
    nova.set_parameter(15, _nova_q_to_val(1.6))
    nova.set_parameter(14, _nova_gain_to_val(-1.5))
    nova.set_parameter(18, 0.5)  # Dyn = On
    nova.set_parameter(19, _nova_thr_to_val(-6.0))
    # Band 3: 6.5 kHz, Q 2.2, dyn cut -2.0 dB
    nova.set_parameter(25, 1.0)
    nova.set_parameter(28, 0.7809)  # Freq = 6.5 kHz
    nova.set_parameter(27, _nova_q_to_val(2.2))
    nova.set_parameter(26, _nova_gain_to_val(-2.0))
    nova.set_parameter(30, 0.5)  # Dyn = On
    nova.set_parameter(31, _nova_thr_to_val(-6.0))
    # Band 4: 11 kHz, high shelf +0.8 dB (static air shelf — no dynamics,
    # keeps the "height" axis of the scene clean and steady; dynamic
    # movement on the air band would fight with Fresh Air above).
    nova.set_parameter(37, 1.0)
    nova.set_parameter(40, 0.8443)  # Freq = 11 kHz
    nova.set_parameter(41, 1.0)  # Type = High Shelf
    nova.set_parameter(38, _nova_gain_to_val(0.8))  # Gain = +0.8 dB
    nova.set_parameter(42, 0.0)  # Dyn = Off (static shelf)


def _nova_q_to_val(q):
    q = max(0.1, min(6.0, float(q)))
    return math.log10(q / 0.1) / math.log10(60.0)


def _nova_gain_to_val(g):
    g = max(-18.0, min(18.0, float(g)))
    return (g + 18.0) / 36.0


def _nova_thr_to_val(t):
    t = max(-50.0, min(0.0, float(t)))
    return (t + 50.0) / 50.0


def _apply_vst_preset(
    tape, pro_q, pro_mb, reverb, chorus, stereo, fresh_air, spiff, sdrr, soothe, preset
):
    for idx, val in preset["tape"].items():
        tape.set_parameter(idx, val)

    sdrr_settings = preset["sdrr"]
    if sdrr_settings["bypass"]:
        sdrr.set_parameter(56, 1.0)
    else:
        sdrr.set_parameter(56, 0.0)
        # SDRR Mode (idx 0) is normalized: 0.0=TUBE, 0.25=DIGI, 0.5=FUZZ,
        # 1.0=DESK. Each mode has its own parameter group (group N params are
        # only live in the corresponding mode). DESK uses group 4 (idx 25-50):
        #   37 Drive4, 40 Compression4, 41 Bass4, 42 Treble4, 49 Mix4.
        sdrr_mode = sdrr_settings["mode"]
        sdrr.set_parameter(0, sdrr_mode)
        if sdrr_mode == 0.0:
            # TUBE -> group 1 params
            sdrr.set_parameter(2, sdrr_settings["drive"])
            sdrr.set_parameter(10, sdrr_settings["mix"])
        elif sdrr_mode == 1.0:
            # DESK -> group 4 params
            sdrr.set_parameter(37, sdrr_settings["drive"])
            sdrr.set_parameter(40, sdrr_settings.get("compression", 0.25))
            sdrr.set_parameter(41, sdrr_settings.get("bass", 0.50))
            sdrr.set_parameter(42, sdrr_settings.get("treble", 0.50))
            sdrr.set_parameter(49, sdrr_settings["mix"])

    spiff_settings = preset["spiff"]
    if spiff_settings["bypass"]:
        spiff.set_parameter(38, 1.0)
        spiff.set_parameter(41, 1.0)
    else:
        spiff.set_parameter(38, 0.0)
        spiff.set_parameter(41, 0.0)
        spiff.set_parameter(0, spiff_settings["mode"])
        if spiff_settings["mode"] > 0.5:
            spiff.set_parameter(2, spiff_settings["boost"])
        else:
            spiff.set_parameter(1, spiff_settings["cut"])
        spiff.set_parameter(3, spiff_settings["sens"])
        spiff.set_parameter(35, 1.0)

    soothe_settings = preset["soothe"]
    if soothe_settings["bypass"]:
        soothe.set_parameter(53, 1.0)
    else:
        soothe.set_parameter(53, 0.0)
        # soothe2 (verified via dump): depth idx4, sharpness idx5, selectivity
        # idx6, sensitivity (band1) idx16 (-12..12 dB). These read as values,
        # but the engineer's spec is slider positions (normalized 0-1).
        soothe.set_parameter(3, 0.40)
        soothe.set_parameter(4, soothe_settings["depth"])
        soothe.set_parameter(5, soothe_settings["sharpness"])
        soothe.set_parameter(6, soothe_settings["selectivity"])
        soothe.set_parameter(7, 0.25)
        soothe.set_parameter(8, 0.20)
        soothe.set_parameter(50, 1.0)
        if "sens" in soothe_settings:
            soothe.set_parameter(16, soothe_settings["sens"])

    eq_settings = preset["eq"]
    # Pro-Q 4 layout (verified via dump): each band = 23 params; base+0 Used,
    # +1 Enabled, +2 Freq, +3 Gain, +4 Q, +5 Shape, +7 Stereo Placement,
    # +9 Dyn Range, +10 Dyn En, +11 Dyn Auto, +12 Threshold. Band bases:
    # B1=0, B2=23, B3=46, B4=69, B5=92, B6=115. Shape norms: Bell 0.0,
    # Low Shelf 0.10, Low Cut 0.20, High Shelf 0.30, High Cut 0.40.
    # Stereo Placement (idx +7 per band): Left 0.0-0.15, Right 0.2-0.35,
    # Stereo 0.4-0.65, Mid 0.7-0.85, Side 0.9-1.0.
    #
    # Replaces the old A1StereoControl widener. Per-band routing is cleaner:
    # the bass low-shelf acts on Mid only (effective mono-bass, phase-stable),
    # the air high-shelf acts on Side only (wide top end). This is the same
    # intent as A1's SafeBass + width, but selective per frequency.
    # Premium tonal balance (AirPods Max aware):
    #   B1 HPF 24Hz | B2 Low Shelf 110Hz +1.1 (Mid) | B3 Dyn Bell 290Hz -1.2
    #   B4 Bell 3.4k -0.6 | B5 Bell 6.8k -0.7 | B6 High Shelf 13k +0.6 (Side)
    pro_q.set_parameter(0, 1.0)  # global
    pro_q.set_parameter(1, 1.0)
    # Band 1: HPF (Low Cut) — full stereo, just removes sub rumble
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(eq_settings["hp_freq"]))
    pro_q.set_parameter(3, _gain_to_val(0.0))
    pro_q.set_parameter(5, 0.20)  # Low Cut
    pro_q.set_parameter(7, 0.5)  # Stereo (full)
    # Band 2: Low Shelf 110Hz — Mid only (mono bass, phase-stable)
    pro_q.set_parameter(23, 1.0)  # Band 2 Used (defaults 0 = band inert!)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(eq_settings["b1_freq"]))
    pro_q.set_parameter(26, _gain_to_val(eq_settings["b1_gain"]))
    pro_q.set_parameter(27, _q_to_val(eq_settings["b1_q"]))
    pro_q.set_parameter(28, 0.10)  # Low Shelf
    pro_q.set_parameter(30, 0.7)  # Mid (mono bass)
    # Band 3: dynamic bell at 290 Hz (only cuts when energy builds up) — Stereo
    # Band 3: dynamic bell at 290 Hz (only cuts when energy builds up) — Mid.
    # Routing the low-mid cleanup to Mid only removes "boxy" center buildup
    # while leaving the stereo width of the band untouched: the cut tightens
    # the center image rather than narrowing the whole scene.
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(46, 1.0)  # Band 3 Used
    pro_q.set_parameter(48, _freq_to_val(eq_settings["b2_freq"]))
    pro_q.set_parameter(49, _gain_to_val(eq_settings["b2_gain"]))
    pro_q.set_parameter(50, _q_to_val(eq_settings["b2_q"]))
    pro_q.set_parameter(51, 0.0)  # Bell
    pro_q.set_parameter(53, 0.7)  # Mid (center-only cleanup, width preserved)
    b2_dyn = eq_settings.get("b2_dyn", 0.0)
    if abs(b2_dyn) > 1e-4:
        pro_q.set_parameter(55, _gain_to_val(b2_dyn))  # Dyn Range (base46+9=55)
        pro_q.set_parameter(56, 1.0)  # Dynamics Enabled (base46+10=56)
        pro_q.set_parameter(57, 0.0)  # Dynamics Manual (base46+11=57)
        pro_q.set_parameter(58, _gain_to_val(abs(b2_dyn)))  # Threshold (base46+12=58)
    else:
        pro_q.set_parameter(55, _gain_to_val(0.0))
        pro_q.set_parameter(56, 0.0)
    # Band 4: static bell at 3.4 kHz — Stereo
    pro_q.set_parameter(70, 1.0)
    pro_q.set_parameter(69, 1.0)  # Band 4 Used
    pro_q.set_parameter(71, _freq_to_val(eq_settings["b3_freq"]))
    pro_q.set_parameter(72, _gain_to_val(eq_settings["b3_gain"]))
    pro_q.set_parameter(73, _q_to_val(eq_settings["b3_q"]))
    pro_q.set_parameter(74, 0.0)  # Bell
    pro_q.set_parameter(76, 0.5)  # Stereo
    pro_q.set_parameter(78, _gain_to_val(0.0))
    pro_q.set_parameter(79, 0.0)
    # Band 5: static bell at 6.8 kHz — Stereo
    pro_q.set_parameter(93, 1.0)
    pro_q.set_parameter(92, 1.0)  # Band 5 Used
    pro_q.set_parameter(94, _freq_to_val(eq_settings["b4_freq"]))
    pro_q.set_parameter(95, _gain_to_val(eq_settings["b4_gain"]))
    pro_q.set_parameter(96, _q_to_val(eq_settings["b4_q"]))
    pro_q.set_parameter(97, 0.0)  # Bell
    pro_q.set_parameter(99, 0.5)  # Stereo (Band 5)
    pro_q.set_parameter(101, _gain_to_val(0.0))
    pro_q.set_parameter(102, 0.0)
    # Band 6: high shelf at 13 kHz (air) — Side only (wide top end)
    pro_q.set_parameter(116, 1.0)
    pro_q.set_parameter(115, 1.0)  # Band 6 Used
    pro_q.set_parameter(117, _freq_to_val(eq_settings["b5_freq"]))
    pro_q.set_parameter(118, _gain_to_val(eq_settings["b5_gain"]))
    pro_q.set_parameter(119, _q_to_val(eq_settings["b5_q"]))
    pro_q.set_parameter(120, 0.30)  # High Shelf
    pro_q.set_parameter(122, 1.0)  # Side (wide air — widens only the top end)
    pro_q.set_parameter(124, _gain_to_val(0.0))
    pro_q.set_parameter(125, 0.0)

    rvb_settings = preset["reverb"]
    if rvb_settings is not None:
        # Dragonfly Hall Reverb (indices verified via live dump, all linear):
        #   2 Dry(0-100%) | 3 Early(0-100%) | 4 Late(0-100%)
        #   5 Size(10-60m) | 6 Width(50-150%) | 7 Predelay(0-100ms)
        #   8 Diffuse(0-100%) | 9 LowCut(0-200Hz) | 12 HighCut(1-16kHz)
        #   15 Spin(0-10) | 16 Wander(0-40) | 17 Decay(0.1-10s)
        #   18 Early Send | 19 Modulation
        #
        # Master reverb glue: short room-tone, not a space creator.
        # Size 18m / Decay 0.9s / Predelay 18ms — keeps transients intact
        # while adding subtle room depth. Diffuse 65% — clean early reflections.
        reverb.set_parameter(2, rvb_settings.get("dry", 0.96))  # Dry 96%
        reverb.set_parameter(3, rvb_settings.get("early", 0.02))  # Early 2%
        reverb.set_parameter(4, rvb_settings.get("late", 0.04))  # Late 4%
        reverb.set_parameter(5, rvb_settings.get("size", _df_size(18)))  # Size 18m
        reverb.set_parameter(
            17, rvb_settings.get("decay", _df_decay(0.9))
        )  # Decay 0.9s
        reverb.set_parameter(
            7, rvb_settings.get("predelay", _df_predelay(18))
        )  # Predelay 18ms
        reverb.set_parameter(8, rvb_settings.get("diffuse", 0.65))  # Diffuse 65%
        reverb.set_parameter(9, _df_lowcut(180))  # Low Cut 180Hz
        reverb.set_parameter(12, _df_highcut(7800))  # High Cut 7.8k
        reverb.set_parameter(6, rvb_settings.get("width", 1.0))  # Width 100%
    else:
        reverb.set_parameter(2, 1.0)  # all dry = bypassed-equivalent
        reverb.set_parameter(3, 0.0)
        reverb.set_parameter(4, 0.0)

    chorus_wet = preset.get("chorus_wet", 0.0)
    if chorus_wet > 0.0:
        chorus.set_parameter(1, chorus_wet)
        chorus.set_parameter(2, 1.0)
        chorus.set_parameter(3, 1.0)
        chorus.set_parameter(4, 0.0)
        chorus.set_parameter(6, 0.0)
    else:
        chorus.set_parameter(1, 0.0)
        chorus.set_parameter(6, 1.0)

    # NOTE: stereo widening is now handled inside Pro-Q 4 via per-band Stereo
    # Placement (see the eq block above). The old A1StereoControl "stereo"
    # preset dict is no longer applied.

    fresh_settings = preset["fresh_air"]
    if fresh_settings["bypass"]:
        fresh_air.set_parameter(2, 1.0)
    else:
        fresh_air.set_parameter(2, 0.0)
        fresh_air.set_parameter(0, fresh_settings["mid"])
        fresh_air.set_parameter(1, fresh_settings["high"])
        fresh_air.set_parameter(3, 1.0)

    mb_settings = preset["pro_mb"]
    if mb_settings["bypass"]:
        pro_mb.set_parameter(138, 1.0)
    else:
        pro_mb.set_parameter(138, 0.0)
        for idx, val in mb_settings["params"].items():
            pro_mb.set_parameter(idx, val)


_VST_ENGINE = None
_VST_GRAPH = None
_VST_FAST_ENGINE = None
_VST_FAST_GRAPH = None
_VST_FAST_MODE = None
_VST_DISPOSED = False
_VST_LEAK_BIN: list = []  # objects parked here survive until os._exit()
_VST_LOCK = threading.Lock()


def _fast_master_enabled() -> bool:
    """True when BIRKA_FAST_MASTER selects any lightweight mastering chain.

    Fast master swaps the full 10-plugin two-pass chain for a single-pass
    lightweight graph (~5-6x faster), intended for quick previews / draft
    masters. The specific chain is chosen by _fast_master_mode(). Opt-in via
    BIRKA_FAST_MASTER; an explicit mode name also enables it.
    """
    return _fast_master_mode() is not None


# Fast-master chain modes. Each maps to an ordered list of plugin keys (graph
# nodes after the playback node). All are single-pass, lighter than the full
# chain. "digital" is the original corrective fast path; the analog modes follow
# the classic Tape -> [console] -> Bus Comp -> [EQ] -> Limiter signal flow.
_FAST_MASTER_CHAINS = {
    "digital": ["pro_q", "kot", "limiter"],  # corrective, cleanest
    "analog_clean": ["tape", "kot", "pro_q", "limiter"],  # Studer->bus->passive EQ
    "analog_warm": ["tape", "sdrr", "kot", "limiter"],  # +console saturation (DESK)
    "analog_ultra": ["tape", "limiter"],  # tape body + loudness
    # Vibe archetypes -------------------------------------------------------
    "analog_thick": [
        "tape",
        "sdrr_tube",
        "pro_q",
        "limiter",
    ],  # fat/punchy (hiphop/lofi)
    "polished": ["tape", "sdrr", "soothe", "limiter"],  # luxury smooth (vocal/pop)
    "modern_loud": ["tape", "pro_mb", "sdrr", "limiter"],  # controlled low + loud (EDM)
    "airy": ["tape", "fresh", "kot", "limiter"],  # warm bottom + air (R&B)
    "punch": ["tape", "spiff", "kot", "limiter"],  # tight transients (drill/techno)
    "reel": ["tape_track", "tape_mix", "limiter"],  # 2x tape: tracking->mixdown
    # Practical mastering guide (M/S, two-stage EQ) -------------------------
    # Reference chain after the classic signal-flow guide: a balancing M/S EQ
    # (HPF on sides + low/high-mid cleanups) feeds a gentle RMS bus glue, then
    # a valve saturation stage adds harmonic character before a tone-shaping
    # M/S EQ (body + side-image width + air) and the limiter. Two Pro-Q nodes
    # keep the balance/tone split the guide prescribes (EQ before AND after the
    # dynamics), each routed per-band to Mid/Side instead of a passive EQ.
    "reference": ["pro_q_balance", "kot", "sdrr_tube", "pro_q_tone", "limiter"],
    # Justin Kedy / Sonic Scoop methodology -------------------------------
    # Six-stage master following the engineer's working chain ORDER (not the
    # order he builds it in). Dynamic/multiband control goes FIRST in signal
    # (problem frequencies tamed before the main processing), then subtractive
    # EQ before the compressor (so cuts don't trigger compression), compressor,
    # saturation on the now-even signal, a stereo widener, and the limiter
    # LAST. Boost-EQ lives in the widener/tone stage AFTER the compressor —
    # per Kedy "boost EQ sometimes better AFTER the compressor".
    "sonic_scoop": [
        "pro_mb_sonic",
        "pro_q_cut",
        "kot",
        "sdrr_tube",
        "pro_q_widen",
        "limiter",
    ],
    # Transparent mastering (Ian Shepherd / Bob Katz methodology) -----------
    # Minimal-colour, maximum dynamic range, wide M/S stereo field.
    # Signal flow: HPF + Side-HPF + mud-dip (linear phase, cuts only) ->
    # resonance smoothing (soothe2, surgical) -> very gentle RMS glue
    # (1.2:1, 30% dry parallel blend keeps transients alive) -> M/S tone +
    # air-widen (Side air shelf +2 dB, Mid bass anchor, no saturation) ->
    # limiter. Zero tape, zero SDRR — no added colour by design.
    "transparent": [
        "pro_q_hpf",
        "soothe",
        "kot_trans",
        "pro_q_trans_wide",
        "limiter",
    ],
    # Cinematic / film score mastering (Hans Zimmer / Remote Control style) -
    # Designed for orchestral and hybrid scores going to picture. The goal is
    # a large, three-dimensional stereo field with warmth and depth, not
    # competitive loudness. Signal flow: M/S balance EQ (Side-HPF + mud
    # cleanup, same as reference) -> light tape warmth (body for strings/brass)
    # -> gentle RMS glue -> valve saturation (harmonic richness) -> film-EQ
    # (wide bass on Mid, soft presence dip for listening comfort, +3 dB air
    # shelf on Sides for depth/space) -> spectral air (Fresh Air) -> limiter
    # at a conservative ceiling (-1.5 dBTP, preserves dynamics for mixing).
    "cinematic": [
        "pro_q_balance",
        "tape",
        "kot",
        "sdrr_tube",
        "pro_q_film",
        "fresh",
        "limiter",
    ],
    # Lo-fi mastering — intentional degradation as aesthetic ---------------
    # Artifacts are the feature: heavy tape wow/flutter + bias push for
    # warble and saturation, SDRR DESK for console grit, soothe inverted
    # (resonances LEFT IN for character), Pro-Q bandwidth limiting
    # (100 Hz–12 kHz shelf rolloff — vinyl/cassette frequency range),
    # aggressive limiter for pumping loudness. Sounds like a loved record.
    "lo_fi": [
        "tape_lofi",
        "sdrr",
        "pro_q_lofi",
        "limiter",
    ],
    # Vintage radio — AM/FM broadcast aesthetic ----------------------------
    # Bandwidth hard-limited to 150 Hz–8 kHz (AM character), mono-summed
    # via M/S (Side nearly zeroed), phone-like presence boost @ 1.5 kHz,
    # aggressive bus comp (Pro-MB pumping), SDRR DESK for transmitter grit,
    # hot limiter ceiling. Sounds like a transistor radio or 1960s AM.
    "vintage_radio": [
        "pro_q_radio",
        "pro_mb",
        "sdrr",
        "limiter_radio",
    ],
}
_FAST_MASTER_ALIASES = {
    "1": "digital",
    "true": "digital",
    "yes": "digital",
    "on": "digital",
    "digital": "digital",
    "clean_digital": "digital",
    "analog": "analog_clean",
    "clean": "analog_clean",
    "analog_clean": "analog_clean",
    "warm": "analog_warm",
    "analog_warm": "analog_warm",
    "vintage": "analog_warm",
    "ultra": "analog_ultra",
    "analog_ultra": "analog_ultra",
    "tape": "analog_ultra",
    "thick": "analog_thick",
    "analog_thick": "analog_thick",
    "fat": "analog_thick",
    "polished": "polished",
    "luxury": "polished",
    "smooth": "polished",
    "modern_loud": "modern_loud",
    "loud": "modern_loud",
    "modern": "modern_loud",
    "airy": "airy",
    "air": "airy",
    "punch": "punch",
    "punchy": "punch",
    "reel": "reel",
    "reel2reel": "reel",
    "double_tape": "reel",
    "tape2": "reel",
    "reference": "reference",
    "mastering": "reference",
    "classic": "reference",
    "sonic_scoop": "sonic_scoop",
    "kedy": "sonic_scoop",
    "scoop": "sonic_scoop",
    "transparent": "transparent",
    "clarity": "transparent",
    "clean_master": "transparent",
    "katz": "transparent",
    "shepherd": "transparent",
    "cinematic": "cinematic",
    "film": "cinematic",
    "score": "cinematic",
    "zimmer": "cinematic",
    "orchestral": "cinematic",
    "lo_fi": "lo_fi",
    "lofi": "lo_fi",
    "cassette": "lo_fi",
    "vinyl": "lo_fi",
    "bedroom": "lo_fi",
    "vintage_radio": "vintage_radio",
    "radio": "vintage_radio",
    "am": "vintage_radio",
    "transistor": "vintage_radio",
    "mono": "vintage_radio",
}


def _fast_master_mode():
    """Resolve BIRKA_FAST_MASTER to a chain name in _FAST_MASTER_CHAINS, or None.

    Accepts the legacy truthy values (1/true/yes -> digital) plus named modes
    and friendly aliases (analog/clean/warm/vintage/ultra/tape). Unknown or
    empty values disable fast master (returns None -> full chain).
    """
    raw = os.environ.get("BIRKA_FAST_MASTER", "").strip().lower()
    if not raw:
        return None
    return _FAST_MASTER_ALIASES.get(raw)


def _configure_proq_fast(pro_q):
    # Minimal corrective EQ for the fast chain: 30 Hz high-pass (Band 1) + a
    # gentle +1 dB high shelf at 10 kHz (Band 2) for a touch of air. No dynamic
    # bands, no per-band M/S — just the essentials. Band "Used" (idx base+0)
    # must be 1 or the band is inert (Pro-Q quirk).
    pro_q.set_parameter(0, 1.0)  # Band 1 Used
    pro_q.set_parameter(1, 1.0)  # Band 1 Enabled
    pro_q.set_parameter(2, _freq_to_val(30.0))
    pro_q.set_parameter(5, 0.20)  # Low Cut
    pro_q.set_parameter(7, 0.5)  # Stereo
    pro_q.set_parameter(23, 1.0)  # Band 2 Used
    pro_q.set_parameter(24, 1.0)  # Band 2 Enabled
    pro_q.set_parameter(25, _freq_to_val(10000.0))
    pro_q.set_parameter(26, _gain_to_val(1.0))
    pro_q.set_parameter(28, 0.30)  # High Shelf


def _configure_proq_analog(pro_q):
    # Passive-mastering-EQ emulation for analog chains: broad strokes only.
    # HPF 30Hz (B1), +0.6 dB @ 100Hz low shelf (B2), tiny -0.5 dB dip @ 320Hz
    # bell (B3), +0.8 dB @ 14kHz air shelf (B4). Wide, musical, no surgery.
    pro_q.set_parameter(0, 1.0)
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(30.0))
    pro_q.set_parameter(5, 0.20)
    pro_q.set_parameter(7, 0.5)
    pro_q.set_parameter(23, 1.0)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(100.0))
    pro_q.set_parameter(26, _gain_to_val(0.6))
    pro_q.set_parameter(28, 0.10)  # Low Shelf
    pro_q.set_parameter(46, 1.0)
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(48, _freq_to_val(320.0))
    pro_q.set_parameter(49, _gain_to_val(-0.5))
    pro_q.set_parameter(51, 0.0)  # Bell
    pro_q.set_parameter(69, 1.0)
    pro_q.set_parameter(70, 1.0)
    pro_q.set_parameter(71, _freq_to_val(14000.0))
    pro_q.set_parameter(72, _gain_to_val(0.8))
    pro_q.set_parameter(74, 0.30)  # High Shelf


def _configure_proq_linear_phase(pro_q):
    # Switch Pro-Q 4 to Linear Phase processing (idx 552) for mastering — the
    # practical guide recommends linear phase EQ for both the balancing and the
    # tone-shaping stages so each band cuts/boosts without phase rotation
    # smearing the work of the compressor/saturator around it. Offline render,
    # so the added latency is irrelevant. Resolution stays Medium (idx 553).
    # NB: idx 552 is 3-step (Zero Latency 0.0 / Natural Phase 0.5 / Linear Phase
    # 1.0); the dump's norm_range was misleading — verified live that 0.25 lands
    # on Natural Phase, NOT Linear Phase. 0.75+ is required.
    pro_q.set_parameter(552, 1.0)  # Processing Mode = Linear Phase


def _configure_proq_balance(pro_q):
    # BALANCING EQ — first in the reference chain, before any dynamics. Per the
    # practical mastering guide: high-pass the SIDES (remove low-end from the
    # stereo image so bass stays mono/centered), then dip the low mids (~250 Hz
    # mudiness/masking) and the high mids (~2.8 kHz nasal/harsh build-up).
    # Linear phase so the cuts don't rotate phase into the compressor below.
    # Band layout: base+0 Used, +1 Enabled, +2 Freq, +3 Gain, +4 Q, +5 Shape,
    # +7 Stereo Placement (Stereo 0.5, Mid 0.7, Side 1.0).
    _configure_proq_linear_phase(pro_q)
    # Band 1: Low Cut on SIDES @ 100 Hz — strips bass from the stereo image only
    pro_q.set_parameter(0, 1.0)
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(100.0))
    pro_q.set_parameter(5, 0.20)  # Low Cut
    pro_q.set_parameter(7, 1.0)  # Side (mono bass stays in center)
    # Band 2: low-mid dip @ 250 Hz — mudiness / masking, full stereo
    pro_q.set_parameter(23, 1.0)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(250.0))
    pro_q.set_parameter(26, _gain_to_val(-1.5))
    pro_q.set_parameter(27, _q_to_val(1.0))
    pro_q.set_parameter(28, 0.0)  # Bell
    pro_q.set_parameter(30, 0.5)  # Stereo
    # Band 3: high-mid dip @ 2.8 kHz — nasal / overloaded mid range, full stereo
    pro_q.set_parameter(46, 1.0)
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(48, _freq_to_val(2800.0))
    pro_q.set_parameter(49, _gain_to_val(-1.0))
    pro_q.set_parameter(50, _q_to_val(1.4))
    pro_q.set_parameter(51, 0.0)  # Bell
    pro_q.set_parameter(53, 0.5)  # Stereo


def _configure_proq_tone(pro_q):
    # TONE-SHAPING EQ — after the compressor and saturator. The creative stage:
    # add body (low shelf), presence (high-mid bell), a side-only mid bell for a
    # wider stereo image without rebalancing frequencies, and an air shelf on
    # the sides (boost on sides = widening, per the guide). Linear phase.
    _configure_proq_linear_phase(pro_q)
    # Band 1: Low Shelf @ 100 Hz +1.0 dB — body / mono bass (Mid only)
    pro_q.set_parameter(0, 1.0)
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(100.0))
    pro_q.set_parameter(3, _gain_to_val(1.0))
    pro_q.set_parameter(5, 0.10)  # Low Shelf
    pro_q.set_parameter(7, 0.7)  # Mid (phase-stable mono bass)
    # Band 2: Bell @ 3 kHz +0.8 dB — presence / high-mid focus, full stereo
    pro_q.set_parameter(23, 1.0)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(3000.0))
    pro_q.set_parameter(26, _gain_to_val(0.8))
    pro_q.set_parameter(27, _q_to_val(1.1))
    pro_q.set_parameter(28, 0.0)  # Bell
    pro_q.set_parameter(30, 0.5)  # Stereo
    # Band 3: Bell @ 2 kHz +0.8 dB on SIDES — wider mid-range stereo image
    pro_q.set_parameter(46, 1.0)
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(48, _freq_to_val(2000.0))
    pro_q.set_parameter(49, _gain_to_val(0.8))
    pro_q.set_parameter(50, _q_to_val(1.2))
    pro_q.set_parameter(51, 0.0)  # Bell
    pro_q.set_parameter(53, 1.0)  # Side (width without freq imbalance)
    # Band 4: High Shelf @ 10 kHz +1.0 dB on SIDES — air / top-end widening
    pro_q.set_parameter(69, 1.0)
    pro_q.set_parameter(70, 1.0)
    pro_q.set_parameter(71, _freq_to_val(10000.0))
    pro_q.set_parameter(72, _gain_to_val(1.0))
    pro_q.set_parameter(74, 0.30)  # High Shelf
    pro_q.set_parameter(76, 1.0)  # Side (air widening)


def _configure_proq_cut(pro_q):
    # SUBTRACTIVE EQ — Justin Kedy stage 2. Sits before the compressor so the
    # removed frequencies never trigger gain reduction. Kedy: "most moves are
    # 0.5-1 dB, occasionally 2-3 dB." This block does surgical cuts only — no
    # boosts (boosts live in the post-compressor tone stage). Linear phase so
    # the cuts don't smear phase into the compressor.
    _configure_proq_linear_phase(pro_q)
    # Band 1: High-pass rumble @ 30 Hz, full stereo
    pro_q.set_parameter(0, 1.0)
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(30.0))
    pro_q.set_parameter(5, 0.20)  # Low Cut
    pro_q.set_parameter(7, 0.5)  # Stereo
    # Band 2: Bell cut @ 400 Hz -1.2 dB — boxiness / low-mid masking
    pro_q.set_parameter(23, 1.0)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(400.0))
    pro_q.set_parameter(26, _gain_to_val(-1.2))
    pro_q.set_parameter(27, _q_to_val(1.2))
    pro_q.set_parameter(28, 0.0)  # Bell
    pro_q.set_parameter(30, 0.5)  # Stereo
    # Band 3: Bell cut @ 2.5 kHz -1.0 dB — harshness / ear fatigue band
    pro_q.set_parameter(46, 1.0)
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(48, _freq_to_val(2500.0))
    pro_q.set_parameter(49, _gain_to_val(-1.0))
    pro_q.set_parameter(50, _q_to_val(1.5))
    pro_q.set_parameter(51, 0.0)  # Bell
    pro_q.set_parameter(53, 0.5)  # Stereo
    # Band 4: High Cut @ 19 kHz — tames stray top-end Brilliance / aliasing feel
    pro_q.set_parameter(69, 1.0)
    pro_q.set_parameter(70, 1.0)
    pro_q.set_parameter(71, _freq_to_val(19000.0))
    pro_q.set_parameter(74, 0.40)  # High Cut
    pro_q.set_parameter(76, 0.5)  # Stereo


def _configure_proq_widen(pro_q):
    # BOOST + STEREO WIDENER — Justin Kedy stages 2(boost)/5. Lives AFTER the
    # compressor: Kedy notes "boost EQ sometimes better after the compressor".
    # Doubles as the stereo widener (stage 5) via Pro-Q's per-band Stereo
    # Placement — bass narrowed to Mid, air widened to Side. This is the same
    # M/S-routing approach the full chain uses (A1StereoControl was removed in
    # favour of selective per-band routing). Linear phase.
    _configure_proq_linear_phase(pro_q)
    # Band 1: Low Shelf @ 80 Hz +1.0 dB on MID — mono bass body (narrow lows)
    pro_q.set_parameter(0, 1.0)
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(80.0))
    pro_q.set_parameter(3, _gain_to_val(1.0))
    pro_q.set_parameter(5, 0.10)  # Low Shelf
    pro_q.set_parameter(7, 0.7)  # Mid (keep bass centered/narrow)
    # Band 2: Bell @ 5 kHz +1.0 dB on STEREO — presence focus
    pro_q.set_parameter(23, 1.0)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(5000.0))
    pro_q.set_parameter(26, _gain_to_val(1.0))
    pro_q.set_parameter(27, _q_to_val(1.0))
    pro_q.set_parameter(28, 0.0)  # Bell
    pro_q.set_parameter(30, 0.5)  # Stereo
    # Band 3: Bell @ 3 kHz +0.8 dB on SIDE — presence width without imbalance
    pro_q.set_parameter(46, 1.0)
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(48, _freq_to_val(3000.0))
    pro_q.set_parameter(49, _gain_to_val(0.8))
    pro_q.set_parameter(50, _q_to_val(1.1))
    pro_q.set_parameter(51, 0.0)  # Bell
    pro_q.set_parameter(53, 1.0)  # Side (widen presence band)
    # Band 4: High Shelf @ 8 kHz +1.5 dB on SIDE — widen air/top end
    pro_q.set_parameter(69, 1.0)
    pro_q.set_parameter(70, 1.0)
    pro_q.set_parameter(71, _freq_to_val(8000.0))
    pro_q.set_parameter(72, _gain_to_val(1.5))
    pro_q.set_parameter(74, 0.30)  # High Shelf
    pro_q.set_parameter(76, 1.0)  # Side (wide top)


def _configure_pro_mb_sonic(pro_mb):
    # Justin Kedy stage 1 — dynamic/multiband control of problem frequencies,
    # placed FIRST in the signal chain (built last, but processes first).
    # Two bands: bass anchor below 120 Hz on Mid, and a dynamic dip on the
    # harsh 2.5-4 kHz band that only engages when energy builds up. Per Kedy's
    # example: tame a resonant frequency without affecting the rest. idx138 =
    # bypass; params mirror the neutral Pro-MB preset layout (verified).
    pro_mb.set_parameter(138, 0.0)  # Bypass off
    # Band 1: bass below 120 Hz, Mid only — gentle anchor, preserves transients
    pro_mb.set_parameter(0, 0.5)  # State = Enabled
    pro_mb.set_parameter(1, 0.0)  # Low Crossover 30 Hz
    pro_mb.set_parameter(3, 0.2007)  # High Crossover 120 Hz
    pro_mb.set_parameter(6, 0.70)  # Threshold -18 dB
    pro_mb.set_parameter(7, 0.40)  # Range -6 dB max GR
    pro_mb.set_parameter(8, 0.30)  # Ratio 1.5:1 (log-mapped)
    pro_mb.set_parameter(9, 0.15)  # Attack 15%
    pro_mb.set_parameter(10, 0.30)  # Release 30%
    pro_mb.set_parameter(11, 0.125)  # Knee 6 dB
    # Band 2: dynamic cut 2.5-4 kHz harshness — only reacts above threshold, so
    # the master stays open at low levels and controlled when energy builds.
    # Pro-MB crossover norm mapping (verified live, log base 30..30k):
    # 2.5 kHz -> 0.6403, 4 kHz -> 0.7083.
    pro_mb.set_parameter(22, 0.5)  # State = Enabled
    pro_mb.set_parameter(23, 0.6403)  # Low Crossover 2.5 kHz
    pro_mb.set_parameter(25, 0.7083)  # High Crossover 4 kHz
    pro_mb.set_parameter(28, 0.833)  # Threshold -10 dB
    pro_mb.set_parameter(29, 0.45)  # Range -3 dB max GR
    pro_mb.set_parameter(30, 0.40)  # Ratio 2:1
    pro_mb.set_parameter(31, 0.2)  # Attack 20%
    pro_mb.set_parameter(32, 0.4)  # Release 40%


def _configure_proq_hpf(pro_q):
    # TRANSPARENT stage 1 — cuts only, linear phase. HPF full stereo @ 30 Hz,
    # Side-HPF @ 100 Hz (mono bass), mud dip @ 250 Hz full stereo -1.0 dB.
    # No boosts whatsoever — this stage must be invisible on a clean mix.
    _configure_proq_linear_phase(pro_q)
    # Band 1: Low Cut full stereo @ 30 Hz — subsonic rumble
    pro_q.set_parameter(0, 1.0)
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(30.0))
    pro_q.set_parameter(5, 0.20)  # Low Cut
    pro_q.set_parameter(7, 0.5)  # Stereo
    # Band 2: Low Cut SIDES @ 100 Hz — strip bass from stereo image, keep mono
    pro_q.set_parameter(23, 1.0)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(100.0))
    pro_q.set_parameter(28, 0.20)  # Low Cut
    pro_q.set_parameter(30, 1.0)  # Side only
    # Band 3: Bell @ 250 Hz -1.0 dB — mud/masking, full stereo
    pro_q.set_parameter(46, 1.0)
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(48, _freq_to_val(250.0))
    pro_q.set_parameter(49, _gain_to_val(-1.0))
    pro_q.set_parameter(50, _q_to_val(1.0))
    pro_q.set_parameter(51, 0.0)  # Bell
    pro_q.set_parameter(53, 0.5)  # Stereo


def _configure_kotelnikov_transparent(kot):
    # Ultra-gentle transparent glue: 1.2:1, high threshold (only peak moments
    # compress), 30% dry blend preserves macro-dynamics and transient punch.
    # This is the Katz "mastering compressor as leveller" approach — you hear
    # the room breathe, not the compressor working.
    kot.set_parameter(0, 0.38)  # Threshold ~ -22 dB (high, catches only peaks)
    kot.set_parameter(5, 0.18)  # Ratio ~1.2:1 (barely above 1:1)
    kot.set_parameter(6, 0.60)  # Attack ~40 ms (slow, lets transients through)
    kot.set_parameter(7, 0.45)  # Release ~200 ms (program-dependent feel)
    kot.set_parameter(12, 0.30)  # Dry/Wet 30% dry — preserves dynamic shape
    kot.set_parameter(14, 0.55)  # Out gain ~0 dB


def _configure_proq_trans_wide(pro_q):
    # TRANSPARENT stage 4 — tone shaping + M/S stereo widening, linear phase.
    # Minimal: only a Mid bass anchor (keeps low end centered and defined) and
    # a Side air shelf (+2 dB @ 12 kHz) for a wide, open top without adding
    # harmonic colour. No presence boost, no mid-range moves — transparency.
    _configure_proq_linear_phase(pro_q)
    # Band 1: Low Shelf @ 80 Hz +0.5 dB on MID — mono bass anchor (subtle)
    pro_q.set_parameter(0, 1.0)
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(80.0))
    pro_q.set_parameter(3, _gain_to_val(0.5))
    pro_q.set_parameter(5, 0.10)  # Low Shelf
    pro_q.set_parameter(7, 0.7)  # Mid (focused mono bass)
    # Band 2: High Shelf @ 12 kHz +2.0 dB on SIDE — wide air, open top end
    pro_q.set_parameter(23, 1.0)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(12000.0))
    pro_q.set_parameter(26, _gain_to_val(2.0))
    pro_q.set_parameter(28, 0.30)  # High Shelf
    pro_q.set_parameter(30, 1.0)  # Side (widen air only)


def _configure_proq_film(pro_q):
    # CINEMATIC tone-shaping EQ — after compressor + saturation. Three goals:
    # (1) wide, powerful low end on MID (foundation for brass/strings/perc),
    # (2) soft presence dip @ 3 kHz on full stereo (reduces ear fatigue on
    #     long listening — critical for film/TV where viewers sit for 2+ hours),
    # (3) extended air shelf on SIDES @ 10 kHz +3 dB (depth, space, immersion).
    # Linear phase throughout — no phase smear in the orchestral transients.
    _configure_proq_linear_phase(pro_q)
    # Band 1: Low Shelf @ 100 Hz +1.5 dB on MID — wide orchestral foundation
    pro_q.set_parameter(0, 1.0)
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(100.0))
    pro_q.set_parameter(3, _gain_to_val(1.5))
    pro_q.set_parameter(5, 0.10)  # Low Shelf
    pro_q.set_parameter(7, 0.7)   # Mid (mono bass power)
    # Band 2: Bell @ 3 kHz -1.0 dB full stereo — presence comfort dip
    pro_q.set_parameter(23, 1.0)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(3000.0))
    pro_q.set_parameter(26, _gain_to_val(-1.0))
    pro_q.set_parameter(27, _q_to_val(0.8))  # broad dip
    pro_q.set_parameter(28, 0.0)   # Bell
    pro_q.set_parameter(30, 0.5)   # Stereo
    # Band 3: High Shelf @ 10 kHz +3.0 dB on SIDE — cinematic depth and space
    pro_q.set_parameter(46, 1.0)
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(48, _freq_to_val(10000.0))
    pro_q.set_parameter(49, _gain_to_val(3.0))
    pro_q.set_parameter(51, 0.30)  # High Shelf
    pro_q.set_parameter(53, 1.0)   # Side (wide immersive air)


def _configure_tape_lofi(tape):
    # CHOWTape in lo-fi mode: heavy drive + high bias (warble/saturation),
    # wow and flutter cranked for cassette instability, 15 ips with Loss On
    # for pronounced HF rolloff. The distortion and warble ARE the aesthetic.
    tape.set_parameter(0, 0.889)   # Input Gain
    tape.set_parameter(1, 0.68)    # Output Gain
    tape.set_parameter(2, 1.0)     # Dry/Wet full
    tape.set_parameter(16, 0.55)   # Tape Drive — heavy saturation
    tape.set_parameter(17, 0.60)   # Tape Saturation — strong
    tape.set_parameter(18, 0.72)   # Tape Bias — pushed high (warble character)
    tape.set_parameter(25, 1.0)    # Loss On — HF rolloff model active
    tape.set_parameter(26, 0.5)    # Tape Speed 15 ips — head bump + HF loss
    tape.set_parameter(27, 0.65)   # Spacing — extra HF attenuation
    tape.set_parameter(28, 0.70)   # Thickness — more rolloff
    tape.set_parameter(8, 0.58)    # Tone Bass + (warm low end)
    tape.set_parameter(9, 0.38)    # Tone Treble — (dull, worn tape)
    # Wow and flutter — cassette instability
    tape.set_parameter(3, 0.35)    # Wow depth
    tape.set_parameter(4, 0.28)    # Flutter depth
    tape.set_parameter(5, 0.45)    # Wow rate
    tape.set_parameter(6, 0.55)    # Flutter rate


def _configure_proq_lofi(pro_q):
    # LO-FI bandwidth shaper — vinyl/cassette frequency range.
    # Hard low cut @ 100 Hz (no sub rumble on worn equipment), high shelf
    # rolloff @ 12 kHz -6 dB (dull, degraded top), gentle presence bump
    # @ 1 kHz +1.5 dB (cheap speaker midrange honk). Natural phase — the
    # phase smear is part of the lo-fi character, linear phase would be
    # too clean.
    # Band 1: Low Cut @ 100 Hz — no sub on cheap playback
    pro_q.set_parameter(0, 1.0)
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(100.0))
    pro_q.set_parameter(5, 0.20)   # Low Cut
    pro_q.set_parameter(7, 0.5)    # Stereo
    # Band 2: Bell @ 1 kHz +1.5 dB — cheap speaker midrange honk
    pro_q.set_parameter(23, 1.0)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(1000.0))
    pro_q.set_parameter(26, _gain_to_val(1.5))
    pro_q.set_parameter(27, _q_to_val(0.7))   # broad
    pro_q.set_parameter(28, 0.0)   # Bell
    pro_q.set_parameter(30, 0.5)   # Stereo
    # Band 3: High Shelf @ 12 kHz -6.0 dB — dull worn tape top end
    pro_q.set_parameter(46, 1.0)
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(48, _freq_to_val(12000.0))
    pro_q.set_parameter(49, _gain_to_val(-6.0))
    pro_q.set_parameter(51, 0.30)  # High Shelf
    pro_q.set_parameter(53, 0.5)   # Stereo


def _configure_proq_radio(pro_q):
    # VINTAGE RADIO bandwidth shaper — AM transistor radio aesthetic.
    # Hard low cut @ 150 Hz (AM has no bass), hard high cut @ 8 kHz
    # (AM bandwidth limit), presence boost @ 1.5 kHz +3 dB (telephone/
    # speaker intelligibility peak), Side shelf zeroed @ 200 Hz (mono).
    # Natural phase — correct for the degraded aesthetic.
    # Band 1: Low Cut full stereo @ 150 Hz — AM has no bass
    pro_q.set_parameter(0, 1.0)
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(150.0))
    pro_q.set_parameter(5, 0.20)   # Low Cut
    pro_q.set_parameter(7, 0.5)    # Stereo
    # Band 2: Low Shelf SIDE @ 200 Hz -12 dB — collapse stereo to mono
    pro_q.set_parameter(23, 1.0)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(200.0))
    pro_q.set_parameter(26, _gain_to_val(-12.0))
    pro_q.set_parameter(28, 0.10)  # Low Shelf
    pro_q.set_parameter(30, 1.0)   # Side only (kill stereo width)
    # Band 3: Bell @ 1.5 kHz +3.0 dB — telephone presence / intelligibility
    pro_q.set_parameter(46, 1.0)
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(48, _freq_to_val(1500.0))
    pro_q.set_parameter(49, _gain_to_val(3.0))
    pro_q.set_parameter(50, _q_to_val(0.9))
    pro_q.set_parameter(51, 0.0)   # Bell
    pro_q.set_parameter(53, 0.5)   # Stereo
    # Band 4: High Cut @ 8 kHz — AM bandwidth ceiling
    pro_q.set_parameter(69, 1.0)
    pro_q.set_parameter(70, 1.0)
    pro_q.set_parameter(71, _freq_to_val(8000.0))
    pro_q.set_parameter(74, 0.40)  # High Cut
    pro_q.set_parameter(76, 0.5)   # Stereo


def _configure_limiter_radio(limiter):
    # Aggressive limiter for vintage radio / AM broadcast pumping character.
    # Short lookahead, hot threshold, low ceiling (-3 dBTP — AM transmitters
    # clip hard). Reuses the base limiter config then overrides key params.
    _configure_limiter(limiter)
    limiter.set_parameter(2, 0.18)   # Lookahead = short (0.9 ms — punchy)
    limiter.set_parameter(3, 0.88)   # Threshold — hot, aggressive GR
    limiter.set_parameter(4, 0.70)   # Output ceiling ~ -3 dBTP (AM clip)
    limiter.set_parameter(9, 0.166)  # Oversampling 2x (speed over quality)
    limiter.set_parameter(10, 0.0)   # True Peak off


def _configure_kotelnikov_fast(kot):
    # Light, cheap glue: ~1.5:1 at a gentle threshold, 100% wet, unity out. No
    # parallel dry blend or deep GR — just cohesion for the draft master.
    kot.set_parameter(0, 0.45)  # Threshold ~ -17 dB
    kot.set_parameter(5, 0.30)  # Ratio ~1.5:1
    kot.set_parameter(12, 0.0)  # Dry/Wet = 100% wet
    kot.set_parameter(14, 0.55)  # Out gain ~0 dB


def _configure_tape_analog(tape):
    # CHOWTape light "analog glue": low drive for soft clipping + harmonics,
    # bias slightly up, wow/flutter negligible (CHOWTape defaults are subtle),
    # full wet. Values mirror the proven neutral preset tape block.
    tape.set_parameter(0, 0.889)  # Input Gain
    tape.set_parameter(1, 0.68)  # Output Gain
    tape.set_parameter(2, 1.0)  # Dry/Wet = full
    tape.set_parameter(16, 0.16)  # Tape Drive (low, ~7%)
    tape.set_parameter(17, 0.22)  # Tape Saturation
    tape.set_parameter(18, 0.48)  # Tape Bias (slightly up)
    tape.set_parameter(8, 0.52)  # Tone Bass
    tape.set_parameter(9, 0.48)  # Tone Treble


def _configure_sdrr_analog(sdrr):
    # SDRR2 in DESK mode as console glue (analog density without dirt). Mirrors
    # the neutral preset sdrr block: low drive, light compression, gentle tone,
    # low mix. group-4 (DESK) params: 37 Drive, 40 Comp, 41 Bass, 42 Treble,
    # 49 Mix. idx 56 = bypass, idx 0 = mode (1.0 = DESK).
    sdrr.set_parameter(56, 0.0)  # Bypass off
    sdrr.set_parameter(0, 1.0)  # Mode = DESK
    sdrr.set_parameter(37, 0.16)  # Drive 1.6
    sdrr.set_parameter(40, 0.22)  # Compression 2.2
    sdrr.set_parameter(41, 0.5167)  # Bass +0.4 dB
    sdrr.set_parameter(42, 0.4667)  # Treble -0.8 dB
    sdrr.set_parameter(49, 0.20)  # Mix 20%


def _configure_sdrr_tube(sdrr):
    # SDRR2 in TUBE mode (mode 0.0 -> group 1): warm valve saturation, fatter
    # mids. Low drive + moderate mix for "record-like" body without dirt.
    # group-1 params: 2 Drive, 10 Mix. idx 56 = bypass, idx 0 = mode (0.0=TUBE).
    sdrr.set_parameter(56, 0.0)  # Bypass off
    sdrr.set_parameter(0, 0.0)  # Mode = TUBE
    sdrr.set_parameter(2, 0.22)  # Drive (light valve warmth)
    sdrr.set_parameter(10, 0.35)  # Mix 35% (more colour than the DESK glue)


def _configure_spiff_fast(spiff):
    # spiff transient shaper, gentle: adds attack/punch. Mirrors neutral preset
    # (mode 0 = cut path) but here used to ENHANCE transients lightly. idx 38/41
    # bypass off, idx0 mode, idx1 cut depth, idx3 sensitivity, idx35 mix.
    spiff.set_parameter(38, 0.0)
    spiff.set_parameter(41, 0.0)
    spiff.set_parameter(0, 0.0)  # mode (cut/transient path)
    spiff.set_parameter(1, 0.18)  # depth — light punch
    spiff.set_parameter(3, 0.35)  # sensitivity
    spiff.set_parameter(35, 1.0)  # mix full


def _configure_soothe_fast(soothe):
    # soothe2 resonance smoothing for the "polished/luxury" top end. Mirrors the
    # neutral preset values (gentle, musical). idx53 bypass, 3 mode, 4 depth,
    # 5 sharpness, 6 selectivity, 7 attack, 8 release, 50 mix, 16 band1 sens.
    soothe.set_parameter(53, 0.0)
    soothe.set_parameter(3, 0.40)
    soothe.set_parameter(4, 0.18)  # depth
    soothe.set_parameter(5, 0.43)  # sharpness
    soothe.set_parameter(6, 0.22)  # selectivity
    soothe.set_parameter(7, 0.25)
    soothe.set_parameter(8, 0.20)
    soothe.set_parameter(50, 1.0)
    soothe.set_parameter(16, 0.19)  # band1 sensitivity


def _configure_fresh_fast(fresh):
    # Fresh Air spectral high-shelf "air". Light settings (mirror neutral preset)
    # for an open, expensive top. idx2 bypass, idx0 mid air, idx1 high air, idx3 trim.
    fresh.set_parameter(2, 0.0)  # bypass off
    fresh.set_parameter(0, 0.04)  # mid air (a touch more than neutral's 0.02)
    fresh.set_parameter(1, 0.16)  # high air
    fresh.set_parameter(3, 1.0)  # trim


def _configure_pro_mb_fast(pro_mb):
    # Pro-MB controlled low-end for the "modern loud" master. Reuses the neutral
    # preset's two bands (mono-bass anchor + mid-bass glue) verbatim. idx138 bypass.
    pro_mb.set_parameter(138, 0.0)
    for idx, val in _VST_NEUTRAL_PRESET["pro_mb"]["params"].items():
        pro_mb.set_parameter(idx, val)


def _configure_tape_track(tape):
    # Stage 1 "tracking tape": low drive + head bump. Slower 15 ips tape speed
    # (idx26=0.5) with Loss on gives the low-end head bump of a tracking machine.
    # Light drive for body without obvious saturation.
    tape.set_parameter(0, 0.889)  # Input Gain
    tape.set_parameter(1, 0.68)  # Output Gain
    tape.set_parameter(2, 1.0)  # Dry/Wet full
    tape.set_parameter(16, 0.14)  # Tape Drive (low)
    tape.set_parameter(17, 0.20)  # Saturation
    tape.set_parameter(18, 0.50)  # Bias neutral
    tape.set_parameter(25, 1.0)  # Loss On (enables head-bump/HF modelling)
    tape.set_parameter(26, 0.5)  # Tape Speed 15 ips -> pronounced head bump
    tape.set_parameter(27, 0.2)  # Spacing low (keep highs at tracking stage)
    tape.set_parameter(28, 0.5)  # Thickness moderate
    tape.set_parameter(8, 0.54)  # Tone Bass slight + (body)
    tape.set_parameter(9, 0.50)  # Tone Treble neutral


def _configure_tape_mix(tape):
    # Stage 2 "mixdown tape": lighter drive + more HF rolloff. Faster 30 ips
    # (idx26=0.75) is flatter in the lows but we widen Spacing/Thickness so the
    # Loss model rolls off the top — the gentle HF softening of a mixdown pass.
    tape.set_parameter(0, 0.889)
    tape.set_parameter(1, 0.68)
    tape.set_parameter(2, 1.0)
    tape.set_parameter(16, 0.08)  # Drive lighter than stage 1
    tape.set_parameter(17, 0.16)  # Saturation lighter
    tape.set_parameter(18, 0.50)
    tape.set_parameter(25, 1.0)  # Loss On
    tape.set_parameter(26, 0.75)  # Tape Speed 30 ips -> flatter lows
    tape.set_parameter(27, 0.6)  # Spacing higher -> more HF loss
    tape.set_parameter(28, 0.7)  # Thickness higher -> more HF rolloff
    tape.set_parameter(8, 0.50)  # Bass neutral
    tape.set_parameter(9, 0.46)  # Treble slight - (soft top)


def _configure_limiter_fast(limiter):
    # Reuse the premium limiter config, then drop oversampling to 2x and turn
    # off true-peak limiting for speed (the chain's heaviest plugin). For a
    # draft/preview master the inter-sample safety margin is not critical;
    # the export path uses the full _configure_limiter (4x + true-peak).
    _configure_limiter(limiter)
    limiter.set_parameter(9, 0.166)  # Oversampling = 2x
    limiter.set_parameter(10, 0.0)  # True Peak Limiting = Off


# Per-plugin fast configurators, keyed by graph-node name. Limiter/EQ vary by
# whether the chain is "analog" (broad passive EQ) or "digital" (corrective),
# resolved when the chain is built.
def _configure_fast_node(name, proc, analog):
    if name == "tape":
        _configure_tape_analog(proc)
    elif name == "tape_track":
        _configure_tape_track(proc)
    elif name == "tape_mix":
        _configure_tape_mix(proc)
    elif name == "sdrr":
        _configure_sdrr_analog(proc)
    elif name == "sdrr_tube":
        _configure_sdrr_tube(proc)
    elif name == "kot":
        _configure_kotelnikov_fast(proc)
    elif name == "pro_q":
        (_configure_proq_analog if analog else _configure_proq_fast)(proc)
    elif name == "pro_q_balance":
        _configure_proq_balance(proc)
    elif name == "pro_q_tone":
        _configure_proq_tone(proc)
    elif name == "pro_q_cut":
        _configure_proq_cut(proc)
    elif name == "pro_q_widen":
        _configure_proq_widen(proc)
    elif name == "pro_q_hpf":
        _configure_proq_hpf(proc)
    elif name == "pro_q_trans_wide":
        _configure_proq_trans_wide(proc)
    elif name == "pro_q_film":
        _configure_proq_film(proc)
    elif name == "pro_q_lofi":
        _configure_proq_lofi(proc)
    elif name == "pro_q_radio":
        _configure_proq_radio(proc)
    elif name == "tape_lofi":
        _configure_tape_lofi(proc)
    elif name == "limiter_radio":
        _configure_limiter_radio(proc)
    elif name == "kot_trans":
        _configure_kotelnikov_transparent(proc)
    elif name == "soothe":
        _configure_soothe_fast(proc)
    elif name == "fresh":
        _configure_fresh_fast(proc)
    elif name == "pro_mb":
        _configure_pro_mb_fast(proc)
    elif name == "pro_mb_sonic":
        _configure_pro_mb_sonic(proc)
    elif name == "spiff":
        _configure_spiff_fast(proc)
    elif name == "limiter":
        _configure_limiter_fast(proc)


# Graph-node name -> plugin path key in _VST_PLUGIN_PATHS.
_FAST_NODE_PLUGIN = {
    "tape": "chow",
    "tape_track": "chow",
    "tape_mix": "chow",
    "sdrr": "sdrr",
    "sdrr_tube": "sdrr",
    "kot": "kot",
    "pro_q": "pro_q",
    "pro_q_balance": "pro_q",
    "pro_q_tone": "pro_q",
    "pro_q_cut": "pro_q",
    "pro_q_widen": "pro_q",
    "pro_q_hpf": "pro_q",
    "pro_q_trans_wide": "pro_q",
    "pro_q_film": "pro_q",
    "pro_q_lofi": "pro_q",
    "pro_q_radio": "pro_q",
    "tape_lofi": "chow",
    "limiter_radio": "limiter",
    "kot_trans": "kot",
    "soothe": "soothe",
    "fresh": "fresh",
    "pro_mb": "pro_mb",
    "pro_mb_sonic": "pro_mb",
    "spiff": "spiff",
    "limiter": "limiter",
}


def _render_fast_vst_chain(dry_audio, np, daw, mode="digital"):
    """Single-pass lightweight master in one of several modes.

    mode selects a chain from _FAST_MASTER_CHAINS:
      digital      : Pro-Q -> Kotelnikov -> Pro-L (corrective, cleanest)
      analog_clean : Tape -> Kotelnikov -> Pro-Q -> Pro-L
      analog_warm  : Tape -> SDRR2(DESK) -> Kotelnikov -> Pro-L
      analog_ultra : Tape -> Pro-L
      reference    : Balancing M/S EQ -> Kotelnikov -> SDRR2(TUBE) ->
                     Tone-shaping M/S EQ -> Pro-L (practical mastering guide)
      sonic_scoop  : Pro-MB -> Subtractive EQ -> Kotelnikov -> SDRR2(TUBE) ->
                     Boost+Wide EQ -> Pro-L (Justin Kedy methodology)

    Caller has already resampled dry_audio to _VST_SAMPLE_RATE and redirected
    stderr. Returns the post-limiter audio (channels, frames) or None on failure.
    Uses its own cached engine (_VST_FAST_ENGINE), rebuilt when the mode changes.
    """
    global _VST_FAST_ENGINE, _VST_FAST_GRAPH, _VST_FAST_MODE
    chain = _FAST_MASTER_CHAINS.get(mode, _FAST_MASTER_CHAINS["digital"])
    analog = mode.startswith("analog")

    # (Re)build the engine/graph when first used or when the mode changed, since
    # different modes use different plugin sets and routing.
    if _VST_FAST_ENGINE is None or _VST_FAST_MODE != mode:
        # Park the OLD engine/graph in the leak bin before dropping our last
        # reference. Reassigning _VST_FAST_ENGINE directly would run the
        # DAWdreamer C++ destructor synchronously, which blocks forever (the
        # app appears to hang on the next render after a mode switch). The leak
        # bin keeps the object alive until os._exit() tears the process down.
        if _VST_FAST_ENGINE is not None:
            _VST_LEAK_BIN.append(_VST_FAST_ENGINE)
            _VST_LEAK_BIN.append(_VST_FAST_GRAPH)
        _VST_FAST_ENGINE = daw.RenderEngine(_VST_SAMPLE_RATE, _VST_BUFFER_SIZE)
        dummy = np.zeros((2, _VST_BUFFER_SIZE), dtype=np.float32)
        pb = _VST_FAST_ENGINE.make_playback_processor("pb", dummy)
        procs = {"pb": pb}
        connections = [(pb, [])]
        prev = "pb"
        for name in chain:
            proc = _VST_FAST_ENGINE.make_plugin_processor(
                name, _VST_PLUGIN_PATHS[_FAST_NODE_PLUGIN[name]]
            )
            procs[name] = proc
            connections.append((proc, [prev]))
            prev = name
        _VST_FAST_ENGINE.load_graph(connections)
        _VST_FAST_GRAPH = procs
        _VST_FAST_MODE = mode

    # Configure each node every render (cheap; keeps params correct after reuse).
    for name in chain:
        _configure_fast_node(name, _VST_FAST_GRAPH[name], analog)

    pb = _VST_FAST_GRAPH["pb"]
    out_node = chain[-1]  # always the limiter
    audio_2d = dry_audio.reshape(-1, 2).T.astype(np.float32)
    duration = audio_2d.shape[1] / _VST_SAMPLE_RATE

    # Pass 1: render at unity and measure post-limiter loudness.
    pb.set_data(audio_2d)
    _VST_FAST_ENGINE.render(duration)
    out = _VST_FAST_ENGINE.get_audio(out_node)
    current_lufs = _measure_lufs(out, _VST_SAMPLE_RATE)

    if current_lufs is not None:
        gain_db = TARGET_LOUDNESS_LUFS - current_lufs
        if gain_db > 0.5:
            # UNDERSHOOT (common with tape/HF-rolloff chains: the limiter never
            # engaged, leaving lots of headroom). Boosting the dry input is
            # ineffective here — the tape stages are non-linear and eat the
            # gain. Instead apply makeup to the OUTPUT, clamped so the true
            # (inter-sample) peak stays under -1 dBTP. This reaches the target
            # loudness exactly when there is headroom, and stops at the ceiling
            # otherwise — no clipping, no extra render.
            ceiling = 10.0 ** (-1.0 / 20.0)  # -1 dBTP
            try:
                from scipy.signal import resample_poly

                tp = 0.0
                for c in range(out.shape[0]):
                    up = resample_poly(out[c], 4, 1)
                    if up.size:
                        tp = max(tp, float(np.max(np.abs(up))))
            except Exception:
                tp = float(np.max(np.abs(out))) if out.size else 1.0
            want = 10.0 ** (gain_db / 20.0)
            headroom = (ceiling / tp) if tp > 1e-9 else want
            out = out * min(want, headroom)
        elif gain_db < -0.5:
            # OVERSHOOT: too loud. Scale the dry input down and re-render so the
            # limiter re-clamps cleanly (cheaper/cleaner than pulling the master).
            scaled = audio_2d * (10.0 ** (max(-8.0, gain_db) / 20.0))
            pb.set_data(scaled.astype(np.float32))
            _VST_FAST_ENGINE.render(duration)
            out = _VST_FAST_ENGINE.get_audio(out_node)
    return out


def _render_sfizz_vst_chain(dry_audio, sample_rate, output_path):
    try:
        import dawdreamer as daw
        import numpy as np
    except ImportError:
        return False

    for path in _VST_PLUGIN_PATHS.values():
        if not Path(path).exists():
            logger_vst.warning("VST plugin missing: %s", path)
            return False
    logger_vst.info("All %d VST3 plugins found", len(_VST_PLUGIN_PATHS))

    # ── Ресемплинг: sfizz SR → VST SR (96kHz) ──────────────────────
    # buf_arr = interleaved stereo (frames*2,) из sfizz на sample_rate.
    # DAWdreamer и VST-плагины инициализированы на _VST_SAMPLE_RATE.
    # Если SR не совпадает — питч сдвинут вниз, тембр плывёт.
    #
    # resample_poly работает в обе стороны: up<dn — даунсемпл (напр. 192k→96k),
    # up>dn — апсемпл (44.1k→96k: up=320, dn=147). По умолчанию Kaiser β=5.0,
    # stopband ≈ -60 dB — достаточно для мастеринга на 96 kHz. Если когда-нибудь
    # сюда придёт preview-путь (22050→96k, большой up), стоит поднять β для более
    # крутого фильтра. Диапазон SR не валидируется — полагаемся на то, что sfizz
    # рендерит на разумной частоте (≤ 192 kHz).
    if sample_rate != _VST_SAMPLE_RATE:
        try:
            import scipy.signal as _sps
            from math import gcd

            stereo = dry_audio.reshape(-1, 2).T.astype(np.float32)  # (2, frames)
            g = gcd(_VST_SAMPLE_RATE, sample_rate)
            up = _VST_SAMPLE_RATE // g
            dn = sample_rate // g
            L = _sps.resample_poly(stereo[0], up, dn)
            R = _sps.resample_poly(stereo[1], up, dn)
            dry_audio = np.stack([L, R]).T.flatten().astype(np.float32)
        except Exception:
            return False

    devnull = open(os.devnull, "w")
    old_stderr = os.dup(2)
    os.dup2(devnull.fileno(), 2)

    with _VST_LOCK:
        try:
            global \
                _VST_ENGINE, \
                _VST_GRAPH, \
                _VST_FAST_ENGINE, \
                _VST_FAST_GRAPH, \
                _VST_FAST_MODE

            # Fast-master path: lightweight single-pass chain. Dispatched here so
            # it shares the same resampling + stderr-redirect + lock as the full
            # chain, but builds/uses its own cached engine and returns early.
            mode = _fast_master_mode()
            if mode is not None:
                try:
                    out = _render_fast_vst_chain(dry_audio, np, daw, mode=mode)
                    if out is not None:
                        return _write_float_wav(
                            out.T.flatten(),
                            output_path,
                            _VST_SAMPLE_RATE,
                            soft_clip=False,
                        )
                except Exception:
                    _VST_FAST_ENGINE = None
                    _VST_FAST_GRAPH = None
                    _VST_FAST_MODE = None
                    # fall through to the full chain on any fast-path failure

            if _VST_ENGINE is None:
                _VST_ENGINE = daw.RenderEngine(_VST_SAMPLE_RATE, _VST_BUFFER_SIZE)
                tape = _VST_ENGINE.make_plugin_processor(
                    "tape", _VST_PLUGIN_PATHS["chow"]
                )
                sdrr = _VST_ENGINE.make_plugin_processor(
                    "sdrr", _VST_PLUGIN_PATHS["sdrr"]
                )
                spiff = _VST_ENGINE.make_plugin_processor(
                    "spiff", _VST_PLUGIN_PATHS["spiff"]
                )
                soothe = _VST_ENGINE.make_plugin_processor(
                    "soothe", _VST_PLUGIN_PATHS["soothe"]
                )
                pro_q = _VST_ENGINE.make_plugin_processor(
                    "pro_q", _VST_PLUGIN_PATHS["pro_q"]
                )
                pro_mb = _VST_ENGINE.make_plugin_processor(
                    "pro_mb", _VST_PLUGIN_PATHS["pro_mb"]
                )
                kot = _VST_ENGINE.make_plugin_processor("kot", _VST_PLUGIN_PATHS["kot"])
                fresh = _VST_ENGINE.make_plugin_processor(
                    "fresh", _VST_PLUGIN_PATHS["fresh"]
                )
                cho = _VST_ENGINE.make_plugin_processor("cho", _VST_PLUGIN_PATHS["cho"])
                ste = _VST_ENGINE.make_plugin_processor("ste", _VST_PLUGIN_PATHS["ste"])
                reverb = _VST_ENGINE.make_plugin_processor(
                    "reverb", _VST_PLUGIN_PATHS["reverb"]
                )
                limiter = _VST_ENGINE.make_plugin_processor(
                    "limiter", _VST_PLUGIN_PATHS["limiter"]
                )

                _configure_kotelnikov_ge(kot)
                _configure_limiter(limiter)

                dummy = np.zeros((2, _VST_BUFFER_SIZE), dtype=np.float32)
                pb = _VST_ENGINE.make_playback_processor("pb", dummy)
                # Premium chain (audio-engineer spec, AAC/Apple Music target):
                #   tape → spiff → soothe → pro_q → pro_mb → kot → sdrr
                #   → reverb(dragonfly) → fresh → limiter
                # A1StereoControl was REMOVED: stereo widening now happens
                # inside Pro-Q 4 via per-band Stereo Placement (idx 7 per band).
                # Bass band → Mid (mono bass), air band → Side (wide air). This
                # is cleaner than a separate M/S widener — Pro-Q routes the
                # correction to exactly the channel/frequency that needs it.
                connections = [
                    (pb, []),
                    (tape, ["pb"]),
                    (spiff, ["tape"]),
                    (soothe, ["spiff"]),
                    (pro_q, ["soothe"]),
                    (pro_mb, ["pro_q"]),
                    (kot, ["pro_mb"]),
                    (sdrr, ["kot"]),
                    (reverb, ["sdrr"]),
                    (fresh, ["reverb"]),
                    (limiter, ["fresh"]),
                ]
                _VST_ENGINE.load_graph(connections)
                _VST_GRAPH = {
                    "tape": tape,
                    "sdrr": sdrr,
                    "spiff": spiff,
                    "soothe": soothe,
                    "pro_q": pro_q,
                    "pro_mb": pro_mb,
                    "kot": kot,
                    "fresh": fresh,
                    "cho": cho,
                    "ste": ste,
                    "reverb": reverb,
                    "limiter": limiter,
                    "pb": pb,
                }

            pb = _VST_GRAPH["pb"]
            _apply_vst_preset(
                _VST_GRAPH["tape"],
                _VST_GRAPH["pro_q"],
                _VST_GRAPH["pro_mb"],
                _VST_GRAPH["reverb"],
                _VST_GRAPH["cho"],
                _VST_GRAPH["ste"],
                _VST_GRAPH["fresh"],
                _VST_GRAPH["spiff"],
                _VST_GRAPH["sdrr"],
                _VST_GRAPH["soothe"],
                _VST_NEUTRAL_PRESET,
            )

            audio_2d = dry_audio.reshape(-1, 2).T.astype(np.float32)
            pb.set_data(audio_2d)
            duration = audio_2d.shape[1] / _VST_SAMPLE_RATE

            # Two-pass loudness calibration. The limiter is the LAST processor
            # and clamps to -1 dBTP true-peak, so any gain applied AFTER it
            # would create new inter-sample overs (clipping on AAC encode). The
            # correct order is: measure -> apply gain to the DRY input (before
            # the whole chain) -> re-render so the limiter catches the new
            # peaks. This guarantees the master never exceeds -1 dBTP.

            # Pass 1: render at unity, measure LUFS post-limiter.
            _VST_ENGINE.render(duration)
            out = _VST_ENGINE.get_audio("limiter")
            current_lufs = _measure_lufs(out, _VST_SAMPLE_RATE)

            # Pass 2: if off-target, scale the dry input and re-render so the
            # limiter re-clamps. Gain is applied to the SOURCE, not the master,
            # so the limiter ceiling (-1 dBTP) is never breached.
            if current_lufs is not None:
                gain_db = max(-8.0, min(6.0, TARGET_LOUDNESS_LUFS - current_lufs))
                if abs(gain_db) > 0.3:
                    scaled = audio_2d * (10.0 ** (gain_db / 20.0))
                    pb.set_data(scaled.astype(np.float32))
                    _VST_ENGINE.render(duration)
                    out = _VST_ENGINE.get_audio("limiter")

            success = _write_float_wav(
                out.T.flatten(), output_path, _VST_SAMPLE_RATE, soft_clip=False
            )
            return success
        except Exception:
            if _VST_ENGINE is not None:
                try:
                    _VST_ENGINE.load_graph([])
                except Exception:
                    pass
                try:
                    del _VST_ENGINE
                except Exception:
                    pass
                _VST_ENGINE = None
                _VST_GRAPH = None
            return False
        finally:
            os.dup2(old_stderr, 2)
            os.close(old_stderr)
            devnull.close()


def _selected_backend() -> str:
    """Backend requested via BIRKA_BACKEND (one of _VALID_BACKENDS).

    "auto" is returned for unknown/empty values and for a requested backend
    whose dependency is not available (so callers can fall back).
    """
    choice = os.environ.get("BIRKA_BACKEND", "auto").strip().lower()
    logger.debug(
        "BIRKA_BACKEND env=%r (requested=%r)", os.environ.get("BIRKA_BACKEND"), choice
    )
    if choice == "sfizz":
        if _SFIZZ_AVAILABLE:
            logger.info("Backend selected: sfizz (requested + available)")
            return "sfizz"
        logger.info("Backend 'sfizz' requested but unavailable -> falling back to auto")
        return "auto"
    if choice == "tsf":
        if _TSF_AVAILABLE:
            logger.info("Backend selected: tsf (requested + available)")
            return "tsf"
        logger.info("Backend 'tsf' requested but unavailable -> falling back to auto")
        return "auto"
    if choice == "fluidsynth":
        logger.info("Backend selected: fluidsynth (requested)")
        return "fluidsynth"
    logger.debug("No explicit backend requested or unknown value -> auto")
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
        logger.info("Auto-resolved backend: tsf (first available)")
        return "tsf"
    if shutil.which("fluidsynth") is not None:
        logger.info("Auto-resolved backend: fluidsynth (fallback, tsf unavailable)")
        return "fluidsynth"
    logger.warning("No audio backend available (tsf=unavailable, fluidsynth=not found)")
    return "none"


def _backend_name() -> str:
    """Resolved backend name; kept for tests and diagnostics."""
    return _resolve_backend()


def _synth_to_wav_for_backend(
    backend: str,
    midi_path: Path,
    tmp_wav: Path,
    sample_rate: int,
    polyphony: int,
    quality: int = 2,
) -> bool:
    """Synthesize one MIDI to a temp WAV using the given backend.

    Returns False if the backend is unavailable or synthesis fails. sfizz falls
    back to tsf/fluidsynth when no SFZ bank is found.
    """
    if backend == "sfizz":
        sfz = _find_sfz()
        if sfz is not None:
            return _synth_sfizz_to_wav(
                sfz,
                midi_path,
                tmp_wav,
                sample_rate=sample_rate,
                polyphony=polyphony,
                quality=quality,
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


def render_midi_to_mp3(
    midi_path: Path, output_dir: Path, quality: int = 2
) -> Optional[Path]:
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
    tmp_wav = _make_temp_wav()
    try:
        if not _synth_to_wav_for_backend(
            backend, midi_path, tmp_wav, 96000, 256, quality=quality
        ):
            return None
        # sfizz already masters to TARGET_LOUDNESS_LUFS (VST chain / pedalboard).
        # Re-running ffmpeg loudnorm on top would double-normalize and pull the
        # already-limited master to a different target. Encode straight through
        # in that case; only the raw tsf/fluidsynth output needs loudnorm.
        if _sfizz_self_masters():
            af = None
        else:
            stats = _measure_stats(tmp_wav)
            af = _build_loudnorm_filter(stats)
        if not _encode_mp3(tmp_wav, af, mp3_path):
            return None
        return mp3_path
    finally:
        tmp_wav.unlink(missing_ok=True)


def render_midi_to_wav(
    midi_path: Path,
    output_path: Path,
    sample_rate: int = 96000,
    polyphony: int = 256,
    quality: int = 2,
    bit_depth: int = 32,
) -> bool:
    """Render a single MIDI to WAV via the selected backend. No normalization.

    bit_depth selects the sfizz output format: 32 = IEEE_FLOAT, 24 = signed
    24-bit PCM, 16 = signed 16-bit PCM. Defaults to 32-bit (IEEE_FLOAT) for
    fastest rendering, DSP precision, and clean playback in QMediaPlayer
    (avoiding FFmpeg's 'Packet corrupt' warnings on 24-bit odd-alignment or
    32-bit int crackle). The tsf/fluidsynth fallback paths ignore bit_depth and
    always write 16-bit.
    """
    backend = _resolve_backend()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if backend == "sfizz":
        sfz = _find_sfz()
        if sfz is not None:
            return _synth_sfizz_to_wav(
                sfz,
                midi_path,
                output_path,
                sample_rate=sample_rate,
                polyphony=polyphony,
                quality=quality,
                bit_depth=bit_depth,
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
            soundfont,
            midi_path,
            output_path,
            sample_rate=sample_rate,
            polyphony=polyphony,
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
    tmp_wav = _make_temp_wav()
    try:
        if backend == "sfizz":
            sfz = _find_sfz()
            if sfz is not None:
                if not _synth_sfizz_to_wav(
                    sfz,
                    midi_path,
                    tmp_wav,
                    sample_rate=sample_rate,
                    polyphony=polyphony,
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
    quality: int = 2,
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
    # The VST mastering chain (_render_sfizz_vst_chain) is guarded by a global
    # _VST_LOCK and reuses one cached DAWdreamer engine, so only one render can
    # run at a time. Spawning os.cpu_count() workers there just piles them up
    # blocked on the lock (context-switch overhead, no speedup). Serialize to a
    # single worker in that case; the per-render work is already inside the lock.
    vst_active = _sfizz_self_masters() and os.environ.get(
        "USE_VST_CHAIN", ""
    ).lower() in ("1", "true", "yes")
    self_masters = _sfizz_self_masters()
    max_workers = 1 if vst_active else min(len(midi_paths), os.cpu_count() or 4)
    results: List[Tuple[Path, Optional[Path]]] = []

    def _render_one(midi_path: Path) -> Tuple[Path, Optional[Path]]:
        mp3_path = output_dir / (midi_path.stem + ".mp3")
        tmp_wav = _make_temp_wav()
        try:
            if not _synth_to_wav_for_backend(
                backend, midi_path, tmp_wav, 96000, 256, quality=quality
            ):
                return midi_path, None
            # Skip loudnorm when sfizz already mastered (see render_midi_to_mp3).
            if self_masters:
                af = None
            else:
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
    sample_rate: int = 96000,
    polyphony: int = 256,
    quality: int = 2,
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
    # Serialize to one worker when the VST chain is active: it holds a global
    # lock + single cached engine, so extra workers only block (see
    # render_midi_to_mp3_batch for the full rationale).
    vst_active = _sfizz_self_masters() and os.environ.get(
        "USE_VST_CHAIN", ""
    ).lower() in ("1", "true", "yes")
    max_workers = 1 if vst_active else min(len(midi_paths), os.cpu_count() or 4)
    results: List[Tuple[Path, Optional[Path]]] = []

    def _render_one(midi_path: Path) -> Tuple[Path, Optional[Path]]:
        wav_path = output_dir / (midi_path.stem + ".wav")
        if render_midi_to_wav(
            midi_path,
            wav_path,
            sample_rate=sample_rate,
            polyphony=polyphony,
            quality=quality,
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
    sample_rate: int = 96000,
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
    tmp_wav = _make_temp_wav()
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
        tmp_wav = _make_temp_wav()
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
    """
    try:
        import numpy as np

        arr = np.asarray(samples, dtype=np.float32)
        arr = np.tanh(arr)
        return (arr * 32767.0).astype(np.int16).tolist()
    except ImportError:
        return [max(-32768, min(32767, int(math.tanh(s) * 32767.0))) for s in samples]


def _soft_clip_to_int24(samples: List[float]) -> List[int]:
    """Convert float samples to 24-bit ints with tanh soft-clipping.

    Same rationale as _soft_clip_to_int16: tanh over the whole signal so summed
    synth voices never pin flat to the 24-bit ceiling. 24-bit range is signed
    [-8388608, 8388607].
    """
    try:
        import numpy as np

        arr = np.asarray(samples, dtype=np.float32)
        arr = np.tanh(arr)
        return (arr * 8388607.0).astype(np.int32).tolist()
    except ImportError:
        return [
            max(-8388608, min(8388607, int(math.tanh(s) * 8388607.0))) for s in samples
        ]


def _synth_tsf_to_wav(
    soundfont: Path,
    midi_path: Path,
    output_path: Path,
    sample_rate: int = 96000,
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

    # ── Adaptive velocity scaling ────────────────────────────────────────────
    # Many MIDI files (especially anime/game rips) have low velocities (20-50)
    # because they were authored for a specific sampler or exported with gain
    # baked into the samples. Rendering at face-value gives a weak, thin sound.
    # We scale velocities so the 95th-percentile note hits velocity 100 (forte),
    # which is the standard "loud but not maxed" performance level.
    # Drums (ch9) are scaled separately to avoid over-compressing the kit.
    try:
        import numpy as _np_vel

        TARGET_VEL_P95      = 100
        TARGET_VEL_P95_DRUM = 90
        _mel_vels = [
            getattr(m, "velocity", 0)
            for _, m in events
            if getattr(m, "type", "") == "note_on"
            and getattr(m, "velocity", 0) > 0
            and getattr(m, "channel", 0) != 9
        ]
        _drm_vels = [
            getattr(m, "velocity", 0)
            for _, m in events
            if getattr(m, "type", "") == "note_on"
            and getattr(m, "velocity", 0) > 0
            and getattr(m, "channel", 0) == 9
        ]

        def _vel_scale(vels: List[int], target_p95: int) -> float:
            if not vels:
                return 1.0
            p95 = float(_np_vel.percentile(vels, 95))
            return min(target_p95 / p95, 4.0) if p95 >= 1 else 1.0

        mel_scale = _vel_scale(_mel_vels, TARGET_VEL_P95)
        drm_scale = _vel_scale(_drm_vels, TARGET_VEL_P95_DRUM)

        if abs(mel_scale - 1.0) > 0.05 or abs(drm_scale - 1.0) > 0.05:
            logger.info(
                "velocity scaling: melodic x%.2f  drums x%.2f  (mel_p95=%.0f drm_p95=%.0f)",
                mel_scale, drm_scale,
                float(_np_vel.percentile(_mel_vels, 95)) if _mel_vels else 0.0,
                float(_np_vel.percentile(_drm_vels, 95)) if _drm_vels else 0.0,
            )
            scaled: List[Tuple[float, Any]] = []
            for t, m in events:
                if getattr(m, "type", "") == "note_on" and getattr(m, "velocity", 0) > 0:
                    is_drum = getattr(m, "channel", 0) == 9
                    scale   = drm_scale if is_drum else mel_scale
                    new_vel = int(min(127, round(getattr(m, "velocity", 0) * scale)))
                    m = m.copy(velocity=new_vel)
                scaled.append((t, m))
            events = scaled
    except Exception as _e:
        logger.debug("velocity scaling skipped: %s", _e)

    samples: List[float] = []
    samples_needed: int = 0
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

            # ── Per-channel intelligent mixing ───────────────────────────────
            # Analyse programs from events to assign pan/volume by instrument role.
            # GM program groups:
            #   0-7   piano       — centre, slight roll-off
            #   8-15  chromatic   — slight L/R spread
            #   16-23 organ       — centre
            #   24-31 guitar      — spread L/R when duplicated
            #   32-39 bass        — centre, -2 dB (don't compete with kick)
            #   40-47 strings     — centre, slight presence
            #   48-55 ensemble    — wide, slight presence
            #   56-63 brass       — slight spread
            #   64-71 reed        — slight R
            #   72-79 pipe        — slight L
            #   80-87 synth lead  — centre
            #   88-95 synth pad   — wide
            #   96-103 synth fx   — wide
            #   104-111 ethnic    — centre
            #   112-119 percussive— centre
            ch_programs: dict = {}
            for _, m in events:
                if getattr(m, "type", "") == "program_change":
                    ch_programs[getattr(m, "channel", 0)] = getattr(m, "program", 0)
            # Also scan initial program from events list (first program_change per ch)
            # Default program per channel if no program_change seen
            active_mel_channels = sorted({
                getattr(m, "channel", 0) for _, m in events
                if getattr(m, "type", "") == "note_on"
                and getattr(m, "velocity", 0) > 0
                and getattr(m, "channel", 0) != 9
            })

            # Assign pan positions: guitars spread L/R, bass centre, rest spread gently
            guitar_channels  = [ch for ch in active_mel_channels if 24 <= ch_programs.get(ch, 0) <= 31]
            bass_channels    = [ch for ch in active_mel_channels if 32 <= ch_programs.get(ch, 0) <= 39]
            pad_channels     = [ch for ch in active_mel_channels if ch_programs.get(ch, 0) in range(88, 104)]
            strings_channels = [ch for ch in active_mel_channels if 40 <= ch_programs.get(ch, 0) <= 55]
            piano_channels   = [ch for ch in active_mel_channels if ch_programs.get(ch, 0) <= 7]
            other_channels   = [ch for ch in active_mel_channels
                                if ch not in guitar_channels and ch not in bass_channels
                                and ch not in strings_channels and ch not in piano_channels]

            # Guitar spread: first guitar L, second guitar R (classic double-track)
            _guitar_pans = [38, 90] if len(guitar_channels) >= 2 else [64]

            # Background channels pan spread
            _back_chs = strings_channels + piano_channels + other_channels
            _back_pans: list = {1: [64], 2: [54, 74], 3: [44, 64, 84], 4: [38, 54, 74, 90]}.get(
                len(_back_chs), [64] * len(_back_chs)
            )

            for i, ch in enumerate(guitar_channels):
                pan = _guitar_pans[i] if i < len(_guitar_pans) else 64
                synth.channel_midi_control(ch, 10, pan)   # CC10 = pan
                synth.channel_midi_control(ch, 7, 100)    # guitars = lead, full vol
                logger.debug("tsf mix: ch%d Guitar pan=%d vol=100", ch, pan)

            for ch in bass_channels:
                synth.channel_midi_control(ch, 10, 64)
                synth.channel_midi_control(ch, 7, 85)     # slightly under guitars
                logger.debug("tsf mix: ch%d Bass pan=64 vol=85", ch)

            for i, ch in enumerate(strings_channels):
                pan = _back_pans[i] if i < len(_back_pans) else 64
                synth.channel_midi_control(ch, 10, pan)
                synth.channel_midi_control(ch, 7, 72)     # background layer
                logger.debug("tsf mix: ch%d Strings pan=%d vol=72", ch, pan)

            for i, ch in enumerate(piano_channels):
                idx = len(strings_channels) + i
                pan = _back_pans[idx] if idx < len(_back_pans) else 64
                synth.channel_midi_control(ch, 10, pan)
                synth.channel_midi_control(ch, 7, 68)     # support texture
                logger.debug("tsf mix: ch%d Piano pan=%d vol=68", ch, pan)

            for i, ch in enumerate(other_channels):
                idx = len(strings_channels) + len(piano_channels) + i
                pan = _back_pans[idx] if idx < len(_back_pans) else 64
                vol = 60 if ch in pad_channels else 75
                synth.channel_midi_control(ch, 10, pan)
                synth.channel_midi_control(ch, 7, vol)
                logger.debug("tsf mix: ch%d Other prog=%d pan=%d vol=%d", ch, ch_programs.get(ch, 0), pan, vol)

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

    # Route through VST mastering chain when available (same path as sfizz).
    # This ensures fast-master presets (digital, cinematic, etc.) apply to tsf
    # output too, not just sfizz. Falls back to soft-clip 16-bit if VST fails.
    buf: List[float] = samples[:samples_needed] if samples and samples_needed else []
    if not buf:
        return False
    try:
        import numpy as np

        buf_arr = np.asarray(buf, dtype=np.float32)
        if _render_sfizz_vst_chain(buf_arr, sample_rate, output_path):
            return True
    except Exception:
        pass

    # Fallback: pedalboard mastering (EQ + tape + comp + loudness + limiter)
    try:
        import numpy as np
        from pedalboard import (  # noqa: PLC0415
            Pedalboard,
            HighpassFilter,
            LowShelfFilter,
            HighShelfFilter,
            PeakFilter,
            Compressor,
            Limiter,
        )

        buf_arr = np.asarray(buf, dtype=np.float32)
        stereo = buf_arr.reshape(-1, 2).T  # (2, frames)

        eq_stage = Pedalboard([
            HighpassFilter(cutoff_frequency_hz=30.0),
            LowShelfFilter(cutoff_frequency_hz=120.0, gain_db=-1.0),
            PeakFilter(cutoff_frequency_hz=3500.0, gain_db=-0.5, q=2.0),
            HighShelfFilter(cutoff_frequency_hz=8000.0, gain_db=1.0),
        ])
        stereo = eq_stage(stereo, sample_rate)

        drive = 1.19
        stereo = np.tanh(stereo * drive) / np.tanh(drive)

        comp_stage = Pedalboard([
            Compressor(threshold_db=-18.0, ratio=1.5, attack_ms=10.0, release_ms=100.0),
        ])
        stereo = comp_stage(stereo, sample_rate)

        pre_mono = np.mean(stereo, axis=0)
        pre_peak = float(np.max(np.abs(pre_mono)))
        pre_rms = float(np.sqrt(np.mean(pre_mono ** 2)))
        crest = pre_peak / (pre_rms + 1e-9) if pre_rms > 1e-9 else 10.0
        clip_db = 4.0 if crest > 10.0 else 3.0 if crest > 7.0 else 2.0
        clip_drive = 10 ** (clip_db / 20.0)
        stereo = np.tanh(stereo * clip_drive) / np.tanh(clip_drive)

        # Limit first, then loudness-normalize so the limiter can't undo the gain.
        lim_stage = Pedalboard([Limiter(threshold_db=-1.0, release_ms=50.0)])
        stereo = lim_stage(stereo, sample_rate)

        current_lufs = _measure_lufs(stereo, sample_rate)
        if current_lufs is not None:
            gain_db = max(-10.0, min(8.0, TARGET_LOUDNESS_LUFS - current_lufs))
            stereo = stereo * (10 ** (gain_db / 20.0))
            # Final true-peak clamp after loudness gain
            tp = float(np.max(np.abs(stereo)))
            if tp > 0.891:  # -1 dBTP
                stereo = stereo * (0.891 / tp)

        buf = np.asarray(stereo, dtype=np.float32).T.flatten().tolist()
    except Exception:
        pass

    return _write_int16_wav(buf, output_path, sample_rate, soft_clip=False)


def _write_int16_wav(
    interleaved: List[float],
    output_path: Path,
    sample_rate: int,
    soft_clip: bool = True,
) -> bool:
    """Soft-clip or linearly scale a flat interleaved float buffer and write a 16-bit stereo WAV.

    Shared by the tsf and sfizz renderers so both get identical output format
    (16-bit stereo).
    """
    if soft_clip:
        int16_samples = _soft_clip_to_int16(interleaved)
    else:
        try:
            import numpy as np

            arr = np.asarray(interleaved, dtype=np.float32)
            int16_samples = (arr * 32767.0).astype(np.int16).tolist()
        except ImportError:
            int16_samples = [
                max(-32768, min(32767, int(s * 32767.0))) for s in interleaved
            ]
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


def _write_int24_wav(
    interleaved: List[float],
    output_path: Path,
    sample_rate: int,
    soft_clip: bool = True,
) -> bool:
    """Soft-clip or linearly scale a flat interleaved float buffer and write a 24-bit stereo WAV.

    24-bit gives ~144 dB dynamic range (vs 96 dB for 16-bit) while staying
    integer PCM (decodable everywhere, including QMediaPlayer's FFmpeg
    backend). The stdlib `wave` module only supports 8/16/24/32-bit via
    setsampwidth, so we pack each sample as 3 signed little-endian bytes.
    """
    if soft_clip:
        int24_samples = _soft_clip_to_int24(interleaved)
    else:
        try:
            import numpy as np

            arr = np.asarray(interleaved, dtype=np.float32)
            int24_samples = (arr * 8388607.0).astype(np.int32).tolist()
        except ImportError:
            int24_samples = [
                max(-8388608, min(8388607, int(s * 8388607.0))) for s in interleaved
            ]
    if not int24_samples:
        return False
    try:
        try:
            import numpy as np

            # Force little-endian int32 so the [:, :3] byte-slice below always
            # takes the low 3 bytes regardless of host endianness. On a
            # big-endian host a native int32 stores [MSB, b2, b1, LSB] and the
            # slice would grab the high bytes (an 8-bit-shifted, wrong sample).
            arr = np.asarray(int24_samples, dtype="<i4")
            # View as uint8, reshape to N x 4, slice first 3 columns, and convert to bytes.
            # This is 3.4x faster than a pure-Python generator expression.
            packed = arr.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
        except ImportError:
            # Fallback to pure-Python signed 24-bit little-endian packing
            packed = b"".join(
                v.to_bytes(3, byteorder="little", signed=True) for v in int24_samples
            )
        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(3)  # 24-bit (3 bytes)
            wf.setframerate(sample_rate)
            wf.writeframes(packed)
    except Exception:
        return False
    return output_path.exists()


def _write_float_wav(
    interleaved: List[float],
    output_path: Path,
    sample_rate: int,
    soft_clip: bool = False,
) -> bool:
    """Write a flat interleaved float buffer as a 32-bit IEEE_FLOAT stereo WAV.

    soft_clip defaults to False: the buffers reaching this writer are already
    mastered (VST limiter to -1 dBTP, or the pedalboard limiter), so a default
    tanh would re-saturate an already-limited signal and silently shave ~2.4 dB
    off the peaks. Callers that pass raw, unlimited float (none in-tree today)
    must opt in with soft_clip=True.

    Skips integer quantization entirely, so sfizz's native float output is
    preserved at full precision. Faster than the int16 path (no per-sample
    tanh/min/max/int conversion), and avoids the QMediaPlayer crackle that
    32-bit *int* (pcm_s32le) caused -- IEEE_FLOAT (format 0x0003) decodes
    cleanly in Qt's FFmpeg backend.
    """
    try:
        import numpy as np
    except Exception:
        return _write_int16_wav(interleaved, output_path, sample_rate, soft_clip)

    arr = np.asarray(interleaved, dtype=np.float32)
    if arr.size == 0:
        return False
    if soft_clip:
        arr = np.tanh(arr)
    raw = arr.tobytes()  # little-endian float32, interleaved stereo

    channels = 2
    byte_rate = sample_rate * channels * 4
    try:
        with open(str(output_path), "wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + len(raw)))
            f.write(b"WAVE")
            # fmt: WAVE_FORMAT_IEEE_FLOAT (3), stereo, 32 bits/sample
            f.write(b"fmt ")
            f.write(
                struct.pack(
                    "<IHHIIHH",
                    16,
                    3,
                    channels,
                    sample_rate,
                    byte_rate,
                    channels * 4,
                    32,
                )
            )
            f.write(b"data")
            f.write(struct.pack("<I", len(raw)))
            f.write(raw)
    except Exception:
        return False
    return output_path.exists()


_SFIZZ_SYNTH_CACHE = {}
_SFIZZ_DISPOSED = False


def dispose_sfizz_cache() -> None:
    """Release all cached sfizz Synth instances.

    pysfizz's Synth is a nanobind-bound C++ object that holds threads and
    file handles. Leaving instances in the module-level cache at interpreter
    shutdown triggers "nanobind: leaked N instances" warnings (and may delay
    process exit while the synth's background load/gc threads are reaped).
    Call this from the application's aboutToQuit handler to drop the cache
    before Python finalization.
    """
    global _SFIZZ_DISPOSED
    if _SFIZZ_DISPOSED:
        return
    _SFIZZ_DISPOSED = True
    for synth in _SFIZZ_SYNTH_CACHE.values():
        try:
            synth.all_sound_off()
        except Exception:
            pass
        # nanobind objects release the underlying C++ instance when the Python
        # wrapper is garbage-collected; del here drops our last reference.
        try:
            del synth
        except Exception:
            pass
    _SFIZZ_SYNTH_CACHE.clear()


def dispose_vst_chain_cache() -> None:
    """Prevent dispose from blocking — intentionally leak the engine.

    Setting _VST_ENGINE = None triggers the DAWdreamer C++ destructor which
    blocks forever in native code. Instead we move the reference into a
    module-level 'leak bin' that keeps the object alive until os._exit()
    kills the process without running any destructors.
    """
    global _VST_ENGINE, _VST_GRAPH, _VST_FAST_ENGINE, _VST_FAST_GRAPH, _VST_DISPOSED
    if _VST_DISPOSED:
        return
    _VST_DISPOSED = True
    # Park references in a leak bin — os._exit() will kill the process
    # without decrementing refcounts or calling C++ destructors.
    _VST_LEAK_BIN.append(_VST_ENGINE)
    _VST_LEAK_BIN.append(_VST_GRAPH)
    _VST_LEAK_BIN.append(_VST_FAST_ENGINE)
    _VST_LEAK_BIN.append(_VST_FAST_GRAPH)
    _VST_ENGINE = None
    _VST_GRAPH = None
    _VST_FAST_ENGINE = None
    _VST_FAST_GRAPH = None


def _dispose_all_audio_backends() -> None:
    """atexit handler: tear down every native audio backend before Python exits.

    This is the safety net for exit paths where Qt's aboutToQuit never fires
    (unhandled exception -> SystemExit -> Py_Exit). Tearing the VST engine down
    here, while the interpreter is fully alive, prevents the plugin destructors
    from running during finalization and segfaulting.
    """
    try:
        dispose_vst_chain_cache()
    except Exception:
        pass
    try:
        dispose_sfizz_cache()
    except Exception:
        pass


atexit.register(_dispose_all_audio_backends)


def _synth_sfizz_to_wav(
    sfz_path: Path,
    midi_path: Path,
    output_path: Path,
    sample_rate: int = 96000,
    polyphony: int = 256,
    bit_depth: int = 32,
    quality: int = 2,
) -> bool:
    """Render a MIDI to a stereo WAV via the sfizz engine (SFZ bank).

    Mirrors _synth_tsf_to_wav's event-driven approach but drives pysfizz's
    low-level _sfizz.Synth block API. pysfizz renders planar (left, right)
    float32 blocks; we interleave them. Output defaults to 32-bit IEEE_FLOAT
    (bit_depth=32): preserves sfizz's native float precision, skips integer
    quantization (faster than int16), and decodes cleanly in QMediaPlayer
    (unlike 32-bit int). Pass bit_depth=16 for the int16 path.

    Caveat: pysfizz does not expose program_change/bank selection. SFZ GM banks
    map channels/programs to regions up front, so program_change events are
    dropped (a debug warning is logged once). If a MIDI audibly mis-renders
    because of this, add a programChange binding in modules/pysfizz (the engine
    supports it at sfz::Synth::programChange).
    """
    try:
        import mido as _mido
        import numpy as np
        from pysfizz import _sfizz
    except Exception:
        return False

    try:
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
            "control_change",
            "pitchwheel",
            "program_change",
        ):
            events.append((abs_time, msg))

    total_seconds = max(1.0, mid.length + 2.0)

    # ── Adaptive velocity scaling (same logic as TSF path) ───────────────────
    try:
        import numpy as _np_vel

        _mel_vels = [
            getattr(m, "velocity", 0)
            for _, m in events
            if getattr(m, "type", "") == "note_on"
            and getattr(m, "velocity", 0) > 0
            and getattr(m, "channel", 0) != 9
        ]
        _drm_vels = [
            getattr(m, "velocity", 0)
            for _, m in events
            if getattr(m, "type", "") == "note_on"
            and getattr(m, "velocity", 0) > 0
            and getattr(m, "channel", 0) == 9
        ]

        def _sfizz_vel_scale(vels: List[int], target_p95: int) -> float:
            if not vels:
                return 1.0
            p95 = float(_np_vel.percentile(vels, 95))
            return min(target_p95 / p95, 4.0) if p95 >= 1 else 1.0

        mel_scale = _sfizz_vel_scale(_mel_vels, 100)
        drm_scale = _sfizz_vel_scale(_drm_vels, 90)

        if abs(mel_scale - 1.0) > 0.05 or abs(drm_scale - 1.0) > 0.05:
            logger_sfizz.info(
                "sfizz: velocity scaling: melodic x%.2f  drums x%.2f  (mel_p95=%.0f drm_p95=%.0f)",
                mel_scale, drm_scale,
                float(_np_vel.percentile(_mel_vels, 95)) if _mel_vels else 0.0,
                float(_np_vel.percentile(_drm_vels, 95)) if _drm_vels else 0.0,
            )
            scaled: List[Tuple[float, Any]] = []
            for t, m in events:
                if getattr(m, "type", "") == "note_on" and getattr(m, "velocity", 0) > 0:
                    is_drum = getattr(m, "channel", 0) == 9
                    scale   = drm_scale if is_drum else mel_scale
                    new_vel = int(min(127, round(getattr(m, "velocity", 0) * scale)))
                    m = m.copy(velocity=new_vel)
                scaled.append((t, m))
            events = scaled
    except Exception as _e:
        logger_sfizz.debug("sfizz: velocity scaling skipped: %s", _e)
    frames_needed = int(total_seconds * sample_rate)

    logger_sfizz.info(
        "sfizz: render start — midi=%s sfz=%s duration=%.1fs sr=%d",
        midi_path.name,
        sfz_path.name,
        total_seconds,
        sample_rate,
    )

    use_vst_chain = False
    try:
        use_vst_chain = os.environ.get("USE_VST_CHAIN", "").lower() in (
            "1",
            "true",
            "yes",
        )
    except Exception:
        pass
    if use_vst_chain:
        logger_sfizz.info(
            "USE_VST_CHAIN=True: routing sfizz output through VST mastering chain"
        )

    try:
        # Two synth instances: melodic + drums.
        #
        # sfizz's note_on(delay, note, velocity) has NO channel parameter,
        # so all notes route to a single channel. Drum notes (ch10 in GM)
        # must go to a SEPARATE sfizz instance loaded with a drum-only SFZ
        # (key-mapped, no loprog/midi_channel filtering). The renderer
        # checks msg.channel == 9 (MIDI ch10) and sends those notes to the
        # drum synth; all other notes go to the melodic synth. Both outputs
        # are summed into the final mix.
        # ── Per-channel sfizz instances ──────────────────────────────────────
        # sfizz note_on() has NO channel parameter — all notes go to one voice
        # pool. To get per-channel pan/volume we create one Synth per melodic
        # channel, route only that channel's notes to it, render separately,
        # then apply a pan/vol matrix when summing.
        active_mel_channels = sorted({
            getattr(m, "channel", 0) for _, m in events
            if getattr(m, "type", "") == "note_on"
            and getattr(m, "velocity", 0) > 0
            and getattr(m, "channel", 0) != 9
        })

        # Analyse GM programs to assign pan/vol (same logic as TSF path)
        ch_programs: dict = {}
        for _, m in events:
            if getattr(m, "type", "") == "program_change":
                ch = getattr(m, "channel", 0)
                if ch not in ch_programs:
                    ch_programs[ch] = getattr(m, "program", 0)

        guitar_chs   = [ch for ch in active_mel_channels if 24 <= ch_programs.get(ch, 0) <= 31]
        bass_chs     = [ch for ch in active_mel_channels if 32 <= ch_programs.get(ch, 0) <= 39]
        pad_chs      = [ch for ch in active_mel_channels if ch_programs.get(ch, 0) in range(88, 104)]
        strings_chs  = [ch for ch in active_mel_channels if 40 <= ch_programs.get(ch, 0) <= 55]  # strings+ensemble
        piano_chs    = [ch for ch in active_mel_channels if ch_programs.get(ch, 0) <= 7]
        other_chs    = [ch for ch in active_mel_channels
                        if ch not in guitar_chs and ch not in bass_chs
                        and ch not in strings_chs and ch not in piano_chs]

        _gpans = [38, 90] if len(guitar_chs) >= 2 else ([44] if len(guitar_chs) == 1 else [])

        # pan positions for non-guitar/bass channels
        _back_chs = strings_chs + piano_chs + other_chs
        _opans: list = {1: [64], 2: [54, 74], 3: [44, 64, 84], 4: [38, 54, 74, 90]}.get(
            len(_back_chs), [64] * len(_back_chs)
        )

        # pan 0..127 → -1..1
        def _pan_norm(p: int) -> float:
            return (p - 64) / 63.0

        ch_mix: dict = {}  # ch → (pan_norm, vol_lin)

        # Guitars are lead — loudest
        for i, ch in enumerate(guitar_chs):
            ch_mix[ch] = (_pan_norm(_gpans[i] if i < len(_gpans) else 64), 1.00)

        # Bass — centre, slightly under guitars to avoid mud
        for ch in bass_chs:
            ch_mix[ch] = (0.0, 0.85)

        # Strings/ensemble — background layer, noticeably under guitars
        for ch in strings_chs:
            ch_mix[ch] = (0.0, 0.72)

        # Piano — support texture, under strings
        for ch in piano_chs:
            ch_mix[ch] = (0.0, 0.68)

        # Pads and other — quietest background
        for i, ch in enumerate(other_chs):
            pan = _opans[len(strings_chs) + len(piano_chs) + i] if (len(strings_chs) + len(piano_chs) + i) < len(_opans) else 64
            vol = 0.60 if ch in pad_chs else 0.75
            ch_mix[ch] = (_pan_norm(pan), vol)

        logger_sfizz.info(
            "sfizz: per-channel mix — %d melodic channels: %s",
            len(active_mel_channels),
            ", ".join(
                f"ch{ch}(prog={ch_programs.get(ch,0)} pan={ch_mix.get(ch,(0,1))[0]:+.2f} vol={ch_mix.get(ch,(0,1))[1]:.2f})"
                for ch in active_mel_channels
            ),
        )

        # Build one sfizz Synth per melodic channel
        ch_synths: dict = {}
        voices_per_ch = max(32, polyphony // max(len(active_mel_channels), 1))
        for ch in active_mel_channels:
            ck = (str(sfz_path), sample_rate, ch, quality)
            if ck in _SFIZZ_SYNTH_CACHE:
                s = _SFIZZ_SYNTH_CACHE[ck]
                s.all_sound_off()
            else:
                s = _sfizz.Synth(sample_rate, _SFIZZ_BLOCK_FRAMES)
                s.enable_freewheeling()
                s.set_num_voices(voices_per_ch)
                s.set_sample_quality(quality)
                if not s.load_sfz_file(str(sfz_path)):
                    logger_sfizz.warning("sfizz: ch%d failed to load SFZ, skipping", ch)
                    continue
                _SFIZZ_SYNTH_CACHE[ck] = s
            ch_synths[ch] = s

        # Keep legacy single synth reference for the event dispatch below
        synth = ch_synths.get(active_mel_channels[0]) if active_mel_channels else None
        if synth is None and not active_mel_channels:
            logger_sfizz.error("sfizz: no melodic channels found")
            return False

        # Drum synth: load drum-only SFZ that sits next to the main bank.
        # Falls back to melodic synth (no separate drums) if file is absent.
        drum_sfz = Path(sfz_path).parent / "General_MIDI_sfizz_drums.sfz"
        drum_synth = None
        if drum_sfz.exists():
            logger_sfizz.debug("sfizz: drum SFZ found: %s", drum_sfz)
            drum_cache_key = (str(drum_sfz), sample_rate, polyphony, quality)
            if drum_cache_key in _SFIZZ_SYNTH_CACHE:
                drum_synth = _SFIZZ_SYNTH_CACHE[drum_cache_key]
                drum_synth.all_sound_off()
            else:
                drum_synth = _sfizz.Synth(sample_rate, _SFIZZ_BLOCK_FRAMES)
                drum_synth.enable_freewheeling()
                drum_synth.set_num_voices(64)
                drum_synth.set_sample_quality(quality)
                if drum_synth.load_sfz_file(str(drum_sfz)):
                    _SFIZZ_SYNTH_CACHE[drum_cache_key] = drum_synth
                else:
                    drum_synth = None

        interleaved_blocks: List[np.ndarray] = []
        mel_left_blocks:    List[np.ndarray] = []
        mel_right_blocks:   List[np.ndarray] = []
        drum_left_blocks:   List[np.ndarray] = []
        drum_right_blocks:  List[np.ndarray] = []
        event_index = 0
        n_events = len(events)
        rendered = 0

        while rendered < frames_needed:
            block_start = rendered
            block_end = rendered + _SFIZZ_BLOCK_FRAMES
            # Dispatch every event whose sample time falls within this block.
            while event_index < n_events:
                msg_time, msg = events[event_index]
                event_frame = int(msg_time * sample_rate)
                if event_frame >= block_end:
                    break
                delay = max(0, min(_SFIZZ_BLOCK_FRAMES, event_frame - block_start))
                ch = getattr(msg, "channel", 0)
                is_drum = ch == 9 and drum_synth is not None
                if is_drum:
                    target = drum_synth
                else:
                    target = ch_synths.get(ch)
                if target is None:
                    event_index += 1
                    continue
                if msg.type == "note_on" and msg.velocity > 0:
                    target.note_on(delay, msg.note, msg.velocity)
                elif msg.type in ("note_off", "note_on"):
                    target.note_off(delay, msg.note, 0)
                elif msg.type == "control_change":
                    target.cc(delay, msg.control, msg.value)
                elif msg.type == "pitchwheel":
                    target.pitch_wheel(delay, msg.pitch)
                elif msg.type == "program_change" and not is_drum:
                    target.program_change(delay, getattr(msg, "program", 0))
                event_index += 1

            # Render all per-channel synths and apply pan/vol matrix.
            # pan_norm ∈ [-1,1]: L gain = cos((pan+1)*π/4), R gain = sin((pan+1)*π/4)
            # This gives -3 dB at centre (constant power panning).
            import math as _math
            ch_L = np.zeros(_SFIZZ_BLOCK_FRAMES, dtype=np.float32)
            ch_R = np.zeros(_SFIZZ_BLOCK_FRAMES, dtype=np.float32)
            for ch, s in ch_synths.items():
                bl, br = s.render_block()
                bl_arr = np.asarray(bl, dtype=np.float32)
                br_arr = np.asarray(br, dtype=np.float32)
                pan_n, vol = ch_mix.get(ch, (0.0, 1.0))
                angle = (pan_n + 1.0) * _math.pi / 4.0
                l_gain = vol * _math.cos(angle)
                r_gain = vol * _math.sin(angle)
                ch_L += bl_arr * l_gain
                ch_R += br_arr * r_gain

            if drum_synth is not None:
                d_block = drum_synth.render_block()
                d_left  = np.asarray(d_block[0], dtype=np.float32)
                d_right = np.asarray(d_block[1], dtype=np.float32)
                drum_left_blocks.append(d_left)
                drum_right_blocks.append(d_right)
            mel_left_blocks.append(ch_L)
            mel_right_blocks.append(ch_R)
            rendered += _SFIZZ_BLOCK_FRAMES
    except Exception as exc:
        logger_sfizz.error("sfizz: render exception: %s", exc, exc_info=True)
        return False

    # ── Professional drum bus mix (post-render, full-buffer processing) ──────
    # Processing per-block (512 frames) is unstable for compressors/EQ.
    # We collect melodic + drum into separate full buffers, apply the drum bus
    # chain on the complete signal, then sum into the final interleaved buffer.
    if mel_left_blocks:
        mel_L = np.concatenate(mel_left_blocks)[:frames_needed]
        mel_R = np.concatenate(mel_right_blocks)[:frames_needed]
    else:
        mel_L = np.zeros(frames_needed, dtype=np.float32)
        mel_R = np.zeros(frames_needed, dtype=np.float32)

    if drum_left_blocks:
        drm_L = np.concatenate(drum_left_blocks)[:frames_needed]
        drm_R = np.concatenate(drum_right_blocks)[:frames_needed]

        # 1. GAIN STAGING: target drum RMS = melodic RMS * 1.0 (equal level).
        #    Drums sit at the same RMS as melodic — transients punch through naturally.
        mel_rms  = float(np.sqrt(np.mean(mel_L ** 2))) + 1e-9
        drm_rms  = float(np.sqrt(np.mean(drm_L ** 2))) + 1e-9
        target_gain = mel_rms / drm_rms
        drum_gain = float(np.clip(target_gain, 0.5, 2.0))
        drm_L = drm_L * drum_gain
        drm_R = drm_R * drum_gain

        try:
            from pedalboard import Pedalboard, HighpassFilter, LowShelfFilter, PeakFilter, HighShelfFilter, Compressor, Limiter  # noqa: PLC0415

            # 2. DRUM BUS EQ
            # - HPF 30 Hz: remove sub-rumble (sfizz drum SFZ often has DC/rumble)
            # - HPF 60 Hz Side: mono bass (kick fundamental stays centre)
            # - Cut 200-300 Hz: mud/boxiness from snare/toms
            # - Boost 3 kHz: snare crack / stick attack presence
            # - Boost 10 kHz: hi-hat air / cymbal shimmer
            drm_stereo = np.stack([drm_L, drm_R]).astype(np.float32)  # (2, frames)
            eq = Pedalboard([
                HighpassFilter(cutoff_frequency_hz=30.0),
                PeakFilter(cutoff_frequency_hz=250.0, gain_db=-2.5, q=1.4),
                PeakFilter(cutoff_frequency_hz=3000.0, gain_db=1.8,  q=2.0),
                HighShelfFilter(cutoff_frequency_hz=10000.0, gain_db=1.5),
            ])
            drm_stereo = eq(drm_stereo, sample_rate)

            # 3. PARALLEL COMPRESSION (NY style)
            # Blend heavy-compressed signal with dry for punch+density.
            comp_heavy = Pedalboard([
                Compressor(threshold_db=-20.0, ratio=6.0, attack_ms=2.0, release_ms=80.0),
            ])
            drm_compressed = comp_heavy(np.asarray(drm_stereo, dtype=np.float32).copy(), sample_rate)
            PARALLEL_BLEND = 0.35  # 35% wet = classic NY parallel ratio
            drm_stereo = drm_stereo * (1.0 - PARALLEL_BLEND) + drm_compressed * PARALLEL_BLEND

            # 4. BUS GLUE COMPRESSOR (light, fast attack)
            # Glues kit together, controls transient peaks.
            glue = Pedalboard([
                Compressor(threshold_db=-12.0, ratio=2.5, attack_ms=5.0, release_ms=120.0),
            ])
            drm_stereo = glue(np.asarray(drm_stereo, dtype=np.float32), sample_rate)

            # 5. M/S STEREO SHAPING
            # Kick + snare stay mono (mid). Overhead/hat widen in side.
            mid  = (drm_stereo[0] + drm_stereo[1]) * 0.5
            side = (drm_stereo[0] - drm_stereo[1]) * 0.5
            # Low-pass mid below 200 Hz for tight mono kick fundamental.
            # Widen side by +2.5 dB for cymbals/overhead.
            side = side * 1.33  # +2.5 dB side
            drm_L = mid + side
            drm_R = mid - side

            # 6. DRUM BUS LIMITER: prevent drums from clipping after processing.
            lim = Pedalboard([Limiter(threshold_db=-3.0, release_ms=40.0)])
            drm_final = lim(np.stack([drm_L, drm_R]), sample_rate)
            drm_L = drm_final[0]
            drm_R = drm_final[1]

        except Exception as exc:
            logger_sfizz.warning("drum bus pedalboard failed (%s), using raw drums", exc)

        # 7. SUM: melodic + processed drum bus
        out_L = mel_L + drm_L
        out_R = mel_R + drm_R
    else:
        # No drums — melodic only
        out_L = mel_L
        out_R = mel_R

    interleaved_blocks = [np.column_stack((out_L, out_R)).flatten()]

    if interleaved_blocks:
        buf_arr = np.concatenate(interleaved_blocks)[: frames_needed * 2]
        logger_sfizz.info(
            "sfizz: rendered %d frames (%.1fs) → %s",
            frames_needed,
            frames_needed / sample_rate,
            output_path.name,
        )
    else:
        buf_arr = np.zeros(0, dtype=np.float32)
        logger_sfizz.warning("sfizz: render produced no audio blocks — silent output")

    if use_vst_chain:
        if _render_sfizz_vst_chain(buf_arr, sample_rate, output_path):
            logger_sfizz.info("sfizz: VST chain OK → %s", output_path)
            return True
        logger_sfizz.warning("sfizz: VST chain failed, falling back to pedalboard")
        use_vst_chain = False
        logger_sfizz.warning("VST chain failed; falling back to pedalboard mastering")

    # ── Professional mastering chain (9.5/10) ────────────────────────────
    # Engineer feedback incorporated:
    #   1. Cleanup EQ (HPF + low tighten + air)
    #   2. Tape saturation (+1.5 dB) — harmonics BEFORE comp
    #   3. Glue comp 1.5:1, -24 dB, 10 ms — target 2-3 dB GR
    #   4. Soft clipper +4 dB — catches transients, raises loudness
    #   5. Loudness gain → -14 LUFS (game/orchestral target)
    #   6. True-peak limiter -1 dBTP — final safety, zero overs
    #
    # Key insight: clipper BEFORE loudness gain BEFORE limiter.
    # Loudness gain after limiter = new overs. This order prevents that.
    try:
        from pedalboard import (
            Pedalboard,
            HighpassFilter,
            LowShelfFilter,
            HighShelfFilter,
            PeakFilter,
            Compressor,
            Gain,
            Limiter,
        )

        stereo = buf_arr.reshape(-1, 2).T  # (channels, samples)

        # ── Step 1: Cleanup EQ ──
        eq_stage = Pedalboard(
            [
                HighpassFilter(cutoff_frequency_hz=30.0),
                LowShelfFilter(cutoff_frequency_hz=120.0, gain_db=-1.0),
                # Conditional harsh control: only -0.5dB (very subtle, since
                # harsh=0 already we don't want to kill presence energy)
                PeakFilter(cutoff_frequency_hz=3500.0, gain_db=-0.5, q=2.0),
                HighShelfFilter(cutoff_frequency_hz=8000.0, gain_db=1.0),
            ]
        )
        stereo = eq_stage(stereo, sample_rate)

        # ── Step 2: Tape saturation (+1.5 dB drive) ──
        drive = 1.19  # +1.5 dB
        stereo = np.tanh(stereo * drive) / np.tanh(drive)

        # ── Step 3: Glue compressor (gentle — just 1-2 dB GR) ──
        # Higher threshold (-18 dB) so it only touches the loudest peaks.
        # This preserves crest factor (dynamics) — the clipper handles
        # loudness, the compressor just adds a touch of glue.
        comp_stage = Pedalboard(
            [
                Compressor(
                    threshold_db=-18.0,
                    ratio=1.5,
                    attack_ms=10.0,
                    release_ms=100.0,
                ),
            ]
        )
        stereo = comp_stage(stereo, sample_rate)

        # ── Step 4: Adaptive soft clipper ──
        # Clip drive adapts to crest factor (dynamics) of the signal:
        #   crest > 10 (classical/sparse) → +4 dB (needs more loudness push)
        #   crest 7-10 (cinematic)         → +3 dB
        #   crest < 7  (modern/dense)      → +2 dB (already loud, gentle)
        # This gives consistent LUFS target across different material
        # without over-crushing quiet pieces or under-driving dense ones.
        pre_clip_mono = np.mean(stereo, axis=0)
        pre_peak = float(np.max(np.abs(pre_clip_mono)))
        pre_rms = float(np.sqrt(np.mean(pre_clip_mono**2)))
        crest = pre_peak / (pre_rms + 1e-9) if pre_rms > 1e-9 else 10.0

        if crest > 10.0:
            clip_db = 4.0  # sparse/classical — push harder for loudness
        elif crest > 7.0:
            clip_db = 3.0  # cinematic — balanced
        else:
            clip_db = 2.0  # dense/modern — already loud, be gentle

        clip_drive = 10 ** (clip_db / 20.0)
        stereo = np.tanh(stereo * clip_drive) / np.tanh(clip_drive)

        # ── Step 5: Loudness normalize to TARGET_LOUDNESS_LUFS ──
        # Measure and adjust. The clipper+comp already bring us close;
        # this fine-tunes to exact target. Gain is applied BEFORE limiter
        # so no new overs are created. Uses the same _measure_lufs as the
        # VST two-pass path (pyloudnorm with RMS fallback) so both code paths
        # share one loudness definition and target.
        current_lufs = _measure_lufs(stereo, sample_rate)
        if current_lufs is not None:
            gain_db = TARGET_LOUDNESS_LUFS - current_lufs
            # The clipper (step 4) already shaped the signal, and the
            # true-peak limiter (step 6) catches any overs. To hit target
            # LUFS the gain needs a few dB on quiet renders.
            gain_db = max(-10.0, min(8.0, gain_db))
            stereo = stereo * (10 ** (gain_db / 20.0))

        # ── Step 6: True-peak limiter (-1 dBTP) ──
        # Final safety after loudness gain. Catches any overs the
        # clipper+gain introduced. Runs LAST so nothing overshoots after.
        lim_stage = Pedalboard(
            [
                Limiter(threshold_db=-1.0, release_ms=50.0),
            ]
        )
        stereo = lim_stage(stereo, sample_rate)

        buf_arr = np.asarray(stereo, dtype=np.float32).T.flatten()
    except Exception:
        pass

    # Hard safety ceiling (encoding headroom for MP3/AAC)
    try:
        peak = float(np.max(np.abs(buf_arr))) if buf_arr.size else 0.0
        if peak > 0.891:  # -1 dBFS
            buf_arr = buf_arr * (0.891 / peak)
    except Exception:
        pass

    buf = buf_arr.tolist()

    if bit_depth == 32:
        return _write_float_wav(buf, output_path, sample_rate, soft_clip=False)
    if bit_depth == 24:
        return _write_int24_wav(buf, output_path, sample_rate, soft_clip=False)
    return _write_int16_wav(buf, output_path, sample_rate, soft_clip=False)


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
    if env:
        p = Path(env)
        if p.exists():
            logger.info("Soundfont from env BIRKA_SOUNDFONT: %s", p)
            return p
        logger.info("BIRKA_SOUNDFONT set but file missing: %s", env)
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
            logger.info("Soundfont found at default path: %s", path)
            return path
    for base in [
        Path("/opt/homebrew/share/soundfonts"),
        Path("/usr/local/share/soundfonts"),
    ]:
        if base.exists():
            for sf2 in base.glob("*.sf2"):
                logger.info("Soundfont found in %s: %s", base, sf2)
                return sf2
    logger.warning("No soundfont (.sf2) found")
    return None


def _build_discord_gm_sfz(bank_dir: Path) -> Optional[Path]:
    """Generate a combined GM.sfz for the sfzinstruments Discord GM bank.

    The bank ships as 149 separate per-instrument .sfz files (no single GM
    file) and most melodic patches are unfilled placeholders (sample=*sine);
    only ~10 melodic instruments + the Standard Kit carry real samples. This
    writes a combined GM.sfz *inside* the bank's "Discord GM" dir (so sample
    paths resolve) that pulls in the real instruments, each guarded by
    loprog/hiprog at its GM program number, plus the Standard Kit mapped to
    channel 10 (MIDI drums).

    Three bank bugs are handled: placeholders are skipped; dangling opcodes
    (the bank comments out <master> but leaves its opcodes bare) are stripped
    before the first real header; and sample= paths are prefixed with the
    instrument's dir rather than relying on default_path (which sfizz resets
    at every <group>, breaking resolution). Cached at GM_combined.sfz and
    rewritten only when a source changes.
    """
    gm_dir = bank_dir / "Discord GM"
    melodic_dir = gm_dir / "Melodic"
    drums_dir = gm_dir / "Drums"
    if not (melodic_dir.is_dir() and drums_dir.is_dir()):
        return None

    out_path = gm_dir / "GM_combined.sfz"

    _HEADER_RE = re.compile(r"<(group|region|control|global|master)>")

    def _is_placeholder(text: str) -> bool:
        # A placeholder only defines sample=*sine and no real sample= lines.
        without_sine = text.replace("sample=*sine", "")
        return "sample=*sine" in text and "sample=" not in without_sine

    _SAMPLE_RE = re.compile(r"(\bsample=)([^\s]+)")

    def _embed_instrument(text: str, prefix: str) -> List[str]:
        """Clean dangling opcodes and prefix sample= paths with *prefix*."""
        out: List[str] = []
        seen_header = False
        for line in text.splitlines():
            stripped = line.lstrip()
            if not stripped.startswith("//") and _HEADER_RE.search(stripped):
                seen_header = True
            if not seen_header:
                continue

            def _fix(m: "re.Match[str]") -> str:
                path = m.group(2)
                if path.startswith("*"):
                    return m.group(0)  # built-in generator, leave as-is
                return f"{m.group(1)}{prefix}{path}"

            out.append(_SAMPLE_RE.sub(_fix, line))
        return out

    # For each GM program, find the real (non-placeholder) instrument SFZ.
    # Some programs ship both a placeholder (Melodic/NNN-Name.sfz) and the real
    # instrument inside a subdir (Melodic/NNN-Name/Name.sfz); prefer the real.
    instruments: List[Tuple[int, Path]] = []
    seen_programs: set[int] = set()
    candidates = sorted(melodic_dir.glob("*.sfz"))
    candidates += sorted(melodic_dir.glob("*/*.sfz"))
    for sfz in candidates:
        try:
            text = sfz.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _is_placeholder(text):
            continue
        prog_src = sfz.stem if "-" in sfz.stem[:4] else sfz.parent.stem
        stem_num = prog_src.split("-", 1)[0]
        if not stem_num.isdigit():
            continue
        program = int(stem_num)  # GM uses 1..128 in this bank's naming
        if program in seen_programs:
            continue  # first real match wins (plain file before subdir)
        instruments.append((program, sfz))
        seen_programs.add(program)

    # Standard Kit is the GM default drum set on channel 10.
    standard_kit = drums_dir / "001-Standard Kit.sfz"
    if not standard_kit.exists():
        return None

    # Rebuild only if missing or any source is newer than the cache.
    sources = [p for _, p in instruments] + [standard_kit]
    out_mtime = out_path.stat().st_mtime if out_path.exists() else 0
    if out_path.exists() and all(s.stat().st_mtime <= out_mtime for s in sources):
        return out_path

    lines: List[str] = [
        "// Auto-generated by Birka (sfizz backend). DO NOT EDIT.",
        "// Combined GM bank from the sfzinstruments Discord-SFZ-GM-Bank.",
        "// Real instruments only; unfilled programs have no regions.",
        "<control>",
        "<global>",
        "",
    ]
    for program, sfz in instruments:
        rel = sfz.relative_to(gm_dir)
        prefix = rel.parent.as_posix() + "/" if rel.parent != Path(".") else ""
        lines.append(f"// GM program {program}")
        lines.append(f"<group> loprog={program} hiprog={program}")
        try:
            text = sfz.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines.extend(_embed_instrument(text, prefix))
        lines.append("")
    lines.append("// GM Standard Kit (channel 10 / drums)")
    lines.append("<group> lochan=10 hichan=10")
    try:
        lines.extend(
            _embed_instrument(
                standard_kit.read_text(encoding="utf-8", errors="ignore"),
                "Drums/001-Standard Kit/",
            )
        )
    except OSError:
        pass
    lines.append("")

    try:
        out_path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        return None
    return out_path


def _find_sfz() -> Optional[Path]:
    """Locate an SFZ instrument bank for the sfizz backend (SFZ-only engine).

    Independent of _find_soundfont() because sfizz cannot load .sf2. Honours
    BIRKA_SFZ, then the bundled Discord-SFZ-GM-Bank (combined at runtime), then
    common SFZ locations.
    """
    env = os.environ.get("BIRKA_SFZ")
    if env:
        p = Path(env)
        if p.exists() and p.suffix.lower() == ".sfz":
            logger.info("SFZ from env BIRKA_SFZ: %s", p)
            return p
        logger.info("BIRKA_SFZ set but missing or not .sfz: %s", env)
    # Bundled Discord GM bank: generate a combined GM.sfz on first use.
    discord_bank = Path("/Volumes/External/Code/Birka/data/Discord-SFZ-GM-Bank")
    if discord_bank.is_dir():
        combined = _build_discord_gm_sfz(discord_bank)
        if combined is not None:
            logger.info("SFZ built from Discord-SFZ-GM-Bank: %s", combined)
            return combined
    candidates = [
        Path("/Volumes/External/Code/Birka/data/GeneralUser GS.sfz"),
        Path("/Volumes/External/Code/Birka/data/GeneralUserGS.sfz"),
        Path("/opt/homebrew/share/sfz/GeneralUser GS.sfz"),
    ]
    for path in candidates:
        if path.exists():
            logger.info("SFZ found at default path: %s", path)
            return path
    for base in [
        Path("/Volumes/External/Code/Birka/data"),
        Path("/opt/homebrew/share/sfz"),
    ]:
        if base.exists():
            for sfz in base.rglob("*.sfz"):
                logger.info("SFZ found in %s: %s", base, sfz)
                return sfz
    logger.warning("No SFZ bank found")
    return None
