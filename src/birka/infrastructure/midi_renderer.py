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
import threading
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
        "mode": 1.0,        # DESK
        "drive": 0.16,      # 1.6  (linear 0-10)
        "compression": 0.22,  # 2.2
        "bass": 0.5167,     # +0.4 dB  (0.5 + 0.4/24)
        "treble": 0.4667,   # -0.8 dB  (0.5 - 0.8/24)
        "mix": 0.32,        # 32%
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
        "b1_dyn": 0.0,        # dyn bell moved to 290Hz on Band 2 (below)
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
    # Dragonfly Hall reverb (see _apply_vst_preset reverb block).
    "reverb": {
        "dry": 0.93,
        "early": 0.04,
        "late": 0.07,
        "size": _df_size(28),
        "decay": _df_decay(1.45),
        "predelay": _df_predelay(110),
        "diffuse": 0.82,
        "width": 1.0,
    },
    "chorus_wet": 0.0,
    # A1StereoControl removed: stereo widening now in Pro-Q 4 per-band
    # (B2 bass → Mid, B6 air → Side). No standalone stereo plugin needed.
    "fresh_air": {"bypass": False, "mid": 0.02, "high": 0.09},
    # Pro-MB multiband. Band 2 active at 320 Hz for mid-bass control (brief
    # step 8). Band 2 block: idx 22 State, 23 Freq, 28 Threshold, 29 Range,
    # 30 Ratio, 31 Attack, 32 Release. State 0.25-0.5 = Enabled. Crossover is
    # log: 320 Hz = norm 0.3427. Mappings (verified via live dump):
    #   Threshold -60..0 dB linear: norm = (db+60)/60
    #   Range     -30..+30 dB linear: norm = (db+30)/60
    #   Ratio     1..100:1 power-law: 2:1=0.40, 3:1=0.50
    # Conservative glue: 2:1, -10 dB threshold, -3 dB max GR, so the low-mid
    # never "jumps out" but dynamics are preserved.
    "pro_mb": {
        "bypass": False,
        "params": {
            22: 0.5,      # Band 2 State = Enabled
            23: 0.3427,   # Band 2 Low Crossover = 320 Hz
            28: 0.833,    # Threshold = -10 dB
            29: 0.45,     # Range = -3 dB (max gain reduction)
            30: 0.40,     # Ratio = 2:1
            31: 0.3,      # Attack = 30%
            32: 0.4,      # Release = 40%
            133: 0.5,     # Mix = 100%
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
    # compressor. Premium "invisible glue" settings: gentle ratio, slow-ish
    # RMS release, sidechain high-passed so the bass isn't over-compressed.
    #
    # idx: 0 Threshold | 1 Peak-Crest | 2 Soft Knee | 3 Max GR | 4 Max GR En
    #      5 Ratio | 6 Attack | 7 Release Peak | 8 Release RMS | 10 Makeup
    #      11 Dry Mix | 12 Dry Wet (INVERTED: 0.0=100% wet, 1.0=0% wet/dry!)
    #      14 Out Gain | 15 SC HP Freq | 16 SC HP Slope
    #
    # CRITICAL FIX: idx 12 (Dry Wet) was 1.0 = "0.0" = 0% processed signal,
    # so the entire compressor was bypassed. 0.0 = 100% wet (full processing).
    kotelnikov.set_parameter(0, 0.26)   # Threshold = -13 dB (premium: gentler)
    kotelnikov.set_parameter(1, 0.4091) # Peak-Crest = RMS (smooth, musical)
    kotelnikov.set_parameter(2, 0.0625) # Soft Knee = 1.0 (gentle onset)
    kotelnikov.set_parameter(5, 0.35)   # Ratio = 1.6:1 (premium "expensive movement")
    kotelnikov.set_parameter(6, 0.58)   # Attack ~38 ms (let transients breathe)
    kotelnikov.set_parameter(7, 0.50)   # Release Peak ~140 ms
    kotelnikov.set_parameter(8, 0.53)   # Release RMS ~240 ms (slow, smooth recovery)
    kotelnikov.set_parameter(10, 0.58)  # Makeup +1 dB
    kotelnikov.set_parameter(11, 0.0)   # Dry Mix = off
    kotelnikov.set_parameter(12, 0.0)   # Dry Wet = 100% wet (FIXED: was 1.0 = bypassed)
    kotelnikov.set_parameter(14, 0.55)  # Out Gain = 0 dB
    kotelnikov.set_parameter(15, 0.65)  # SC HP Freq = 180 Hz (premium: bass looser/warmer)
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
    limiter.set_parameter(0, 0.0)       # Gain = 0 dB
    limiter.set_parameter(1, 0.0)       # Style = "Transparent" (cleanest for AAC)
    limiter.set_parameter(2, 0.28)      # Lookahead = 1.4 ms (premium transparent)
    limiter.set_parameter(3, 0.28)      # Attack ~60 ms (smooth gain riding)
    limiter.set_parameter(4, 0.3878)    # Release ~420 ms (smooth, no pumping)
    limiter.set_parameter(9, 0.5)       # Oversampling = 8x (premium/CD quality)
    limiter.set_parameter(10, 1.0)      # True Peak Limiting = On (catches inter-sample peaks)
    limiter.set_parameter(17, 0.0)      # Bypass = Off
    limiter.set_parameter(18, 0.9667)   # Output Level = -1.0 dBTP (Apple Music / streaming safe)
    limiter.set_parameter(19, 1.0)      # Lock Output = Locked
    limiter.set_parameter(22, 1.0)      # True Peak Metering = Show True Peaks


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
    nova.set_parameter(1, 1.0)                          # Active
    nova.set_parameter(4, 0.3832)                       # Freq = 240 Hz
    nova.set_parameter(3, _nova_q_to_val(0.9))          # Q = 0.9
    nova.set_parameter(2, _nova_gain_to_val(-1.8))      # Gain = -1.8 dB
    nova.set_parameter(6, 0.5)                          # Dyn = On
    nova.set_parameter(7, _nova_thr_to_val(-6.0))       # Threshold = -6 dB
    # Band 2: 2.8 kHz, Q 1.6, dyn cut -1.5 dB
    nova.set_parameter(13, 1.0)
    nova.set_parameter(16, 0.6794)                      # Freq = 2.8 kHz
    nova.set_parameter(15, _nova_q_to_val(1.6))
    nova.set_parameter(14, _nova_gain_to_val(-1.5))
    nova.set_parameter(18, 0.5)                         # Dyn = On
    nova.set_parameter(19, _nova_thr_to_val(-6.0))
    # Band 3: 6.5 kHz, Q 2.2, dyn cut -2.0 dB
    nova.set_parameter(25, 1.0)
    nova.set_parameter(28, 0.7809)                      # Freq = 6.5 kHz
    nova.set_parameter(27, _nova_q_to_val(2.2))
    nova.set_parameter(26, _nova_gain_to_val(-2.0))
    nova.set_parameter(30, 0.5)                         # Dyn = On
    nova.set_parameter(31, _nova_thr_to_val(-6.0))
    # Band 4: 11 kHz, high shelf +0.8 dB (static air)
    nova.set_parameter(37, 1.0)
    nova.set_parameter(40, 0.8443)                      # Freq = 11 kHz
    nova.set_parameter(41, 1.0)                         # Type = High Shelf
    nova.set_parameter(38, _nova_gain_to_val(0.8))      # Gain = +0.8 dB
    nova.set_parameter(42, 0.0)                         # Dyn = Off (static shelf)


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
    pro_q.set_parameter(0, 1.0)   # global
    pro_q.set_parameter(1, 1.0)
    # Band 1: HPF (Low Cut) — full stereo, just removes sub rumble
    pro_q.set_parameter(1, 1.0)
    pro_q.set_parameter(2, _freq_to_val(eq_settings["hp_freq"]))
    pro_q.set_parameter(3, _gain_to_val(0.0))
    pro_q.set_parameter(5, 0.20)  # Low Cut
    pro_q.set_parameter(7, 0.5)   # Stereo (full)
    # Band 2: Low Shelf 110Hz — Mid only (mono bass, phase-stable)
    pro_q.set_parameter(24, 1.0)
    pro_q.set_parameter(25, _freq_to_val(eq_settings["b1_freq"]))
    pro_q.set_parameter(26, _gain_to_val(eq_settings["b1_gain"]))
    pro_q.set_parameter(27, _q_to_val(eq_settings["b1_q"]))
    pro_q.set_parameter(28, 0.10)  # Low Shelf
    pro_q.set_parameter(30, 0.7)   # Mid (mono bass)
    # Band 3: dynamic bell at 290 Hz (only cuts when energy builds up) — Stereo
    pro_q.set_parameter(47, 1.0)
    pro_q.set_parameter(48, _freq_to_val(eq_settings["b2_freq"]))
    pro_q.set_parameter(49, _gain_to_val(eq_settings["b2_gain"]))
    pro_q.set_parameter(50, _q_to_val(eq_settings["b2_q"]))
    pro_q.set_parameter(51, 0.0)  # Bell
    pro_q.set_parameter(53, 0.5)  # Stereo
    b2_dyn = eq_settings.get("b2_dyn", 0.0)
    if abs(b2_dyn) > 1e-4:
        pro_q.set_parameter(55, _gain_to_val(b2_dyn))  # Dyn Range (base46+9=55)
        pro_q.set_parameter(56, 1.0)                    # Dynamics Enabled (base46+10=56)
        pro_q.set_parameter(57, 0.0)                    # Dynamics Manual (base46+11=57)
        pro_q.set_parameter(58, _gain_to_val(abs(b2_dyn)))  # Threshold (base46+12=58)
    else:
        pro_q.set_parameter(55, _gain_to_val(0.0))
        pro_q.set_parameter(56, 0.0)
    # Band 4: static bell at 3.4 kHz — Stereo
    pro_q.set_parameter(70, 1.0)
    pro_q.set_parameter(71, _freq_to_val(eq_settings["b3_freq"]))
    pro_q.set_parameter(72, _gain_to_val(eq_settings["b3_gain"]))
    pro_q.set_parameter(73, _q_to_val(eq_settings["b3_q"]))
    pro_q.set_parameter(74, 0.0)  # Bell
    pro_q.set_parameter(76, 0.5)  # Stereo
    pro_q.set_parameter(78, _gain_to_val(0.0))
    pro_q.set_parameter(79, 0.0)
    # Band 5: static bell at 6.8 kHz — Stereo
    pro_q.set_parameter(93, 1.0)
    pro_q.set_parameter(94, _freq_to_val(eq_settings["b4_freq"]))
    pro_q.set_parameter(95, _gain_to_val(eq_settings["b4_gain"]))
    pro_q.set_parameter(96, _q_to_val(eq_settings["b4_q"]))
    pro_q.set_parameter(97, 0.0)  # Bell
    pro_q.set_parameter(99, 0.5)  # Stereo (Band 5)
    pro_q.set_parameter(101, _gain_to_val(0.0))
    pro_q.set_parameter(102, 0.0)
    # Band 6: high shelf at 13 kHz (air) — Side only (wide top end)
    pro_q.set_parameter(116, 1.0)
    pro_q.set_parameter(117, _freq_to_val(eq_settings["b5_freq"]))
    pro_q.set_parameter(118, _gain_to_val(eq_settings["b5_gain"]))
    pro_q.set_parameter(119, _q_to_val(eq_settings["b5_q"]))
    pro_q.set_parameter(120, 0.30)  # High Shelf
    pro_q.set_parameter(122, 1.0)   # Side (wide air — widens only the top end)
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
        # Audio-engineer spec: cinematic expensive tail. Size 28m, Decay 1.45s,
        # Predelay 110ms, Diffuse 82%, Low Cut 180Hz, High Cut 7.8k, mostly dry
        # (93%) with a whisper of early reflection (4%) and tail (7%).
        reverb.set_parameter(2, rvb_settings.get("dry", 0.93))         # Dry 93%
        reverb.set_parameter(3, rvb_settings.get("early", 0.04))       # Early 4%
        reverb.set_parameter(4, rvb_settings.get("late", 0.07))        # Late 7%
        reverb.set_parameter(5, rvb_settings.get("size", _df_size(28)))   # Size 28m
        reverb.set_parameter(17, rvb_settings.get("decay", _df_decay(1.45)))  # Decay 1.45s
        reverb.set_parameter(7, rvb_settings.get("predelay", _df_predelay(110)))  # Predelay 110ms
        reverb.set_parameter(8, rvb_settings.get("diffuse", 0.82))     # Diffuse 82%
        reverb.set_parameter(9, _df_lowcut(180))                       # Low Cut 180Hz
        reverb.set_parameter(12, _df_highcut(7800))                    # High Cut 7.8k
        reverb.set_parameter(6, rvb_settings.get("width", 1.0))        # Width 100%
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
_VST_LOCK = threading.Lock()


def _render_sfizz_vst_chain(dry_audio, sample_rate, output_path):
    try:
        import dawdreamer as daw
        import numpy as np
    except ImportError:
        return False

    for path in _VST_PLUGIN_PATHS.values():
        if not Path(path).exists():
            return False

    devnull = open(os.devnull, "w")
    old_stderr = os.dup(2)
    os.dup2(devnull.fileno(), 2)

    with _VST_LOCK:
        try:
            global _VST_ENGINE, _VST_GRAPH
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
                nova = _VST_ENGINE.make_plugin_processor(
                    "nova", _VST_PLUGIN_PATHS["nova"]
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
                _configure_nova(nova)

                dummy = np.zeros((2, _VST_BUFFER_SIZE), dtype=np.float32)
                pb = _VST_ENGINE.make_playback_processor("pb", dummy)
                # Premium chain (audio-engineer spec, AAC/Apple Music target):
                #   tape → sdrr → spiff → soothe → pro_q → pro_mb → nova → kot
                #   → fresh → reverb(dragonfly) → limiter
                # A1StereoControl was REMOVED: stereo widening now happens
                # inside Pro-Q 4 via per-band Stereo Placement (idx 7 per band).
                # Bass band → Mid (mono bass), air band → Side (wide air). This
                # is cleaner than a separate M/S widener — Pro-Q routes the
                # correction to exactly the channel/frequency that needs it.
                connections = [
                    (pb, []),
                    (tape, ["pb"]),
                    (sdrr, ["tape"]),
                    (spiff, ["sdrr"]),
                    (soothe, ["spiff"]),
                    (pro_q, ["soothe"]),
                    (pro_mb, ["pro_q"]),
                    (nova, ["pro_mb"]),
                    (kot, ["nova"]),
                    (fresh, ["kot"]),
                    (reverb, ["fresh"]),
                    (limiter, ["reverb"]),
                ]
                _VST_ENGINE.load_graph(connections)
                _VST_GRAPH = {
                    "tape": tape,
                    "sdrr": sdrr,
                    "spiff": spiff,
                    "soothe": soothe,
                    "pro_q": pro_q,
                    "pro_mb": pro_mb,
                    "nova": nova,
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
            def _measure_lufs(buf: np.ndarray) -> Optional[float]:
                try:
                    mono = np.mean(buf, axis=0)
                    win = int(0.4 * _VST_SAMPLE_RATE)
                    blocks = []
                    for i in range(0, max(1, len(mono) - win), win):
                        rms = float(np.sqrt(np.mean(mono[i : i + win] ** 2)))
                        if rms > 1e-6:
                            blocks.append(rms)
                    if not blocks:
                        return None
                    mean_rms = float(np.mean(blocks))
                    lufs_calibration_offset = 6.1
                    return 20.0 * np.log10(mean_rms) - 0.691 + lufs_calibration_offset
                except Exception:
                    return None

            # Pass 1: render at unity, measure LUFS post-limiter.
            _VST_ENGINE.render(duration)
            out = _VST_ENGINE.get_audio("limiter")
            current_lufs = _measure_lufs(out)
            # -13.8 LUFS (not -14): Apple Sound Check lands more musically
            # here after AAC encode than a flat -14.
            target_lufs = -13.8

            # Pass 2: if off-target, scale the dry input and re-render so the
            # limiter re-clamps. Gain is applied to the SOURCE, not the master,
            # so the limiter ceiling (-1 dBTP) is never breached.
            if current_lufs is not None:
                gain_db = max(-8.0, min(6.0, target_lufs - current_lufs))
                if abs(gain_db) > 0.3:
                    scaled = audio_2d * (10.0 ** (gain_db / 20.0))
                    pb.set_data(scaled.astype(np.float32))
                    _VST_ENGINE.render(duration)
                    out = _VST_ENGINE.get_audio("limiter")

            success = _write_float_wav(
                out.T.flatten(), output_path, sample_rate, soft_clip=False
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
    tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
    try:
        if not _synth_to_wav_for_backend(
            backend, midi_path, tmp_wav, 96000, 256, quality=quality
        ):
            return None
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
    tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
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
    max_workers = min(len(midi_paths), os.cpu_count() or 4)
    results: List[Tuple[Path, Optional[Path]]] = []

    def _render_one(midi_path: Path) -> Tuple[Path, Optional[Path]]:
        mp3_path = output_dir / (midi_path.stem + ".mp3")
        tmp_wav = Path(tempfile.mktemp(suffix=".wav"))
        try:
            if not _synth_to_wav_for_backend(
                backend, midi_path, tmp_wav, 96000, 256, quality=quality
            ):
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
    max_workers = min(len(midi_paths), os.cpu_count() or 4)
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

            arr = np.asarray(int24_samples, dtype=np.int32)
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
    soft_clip: bool = True,
) -> bool:
    """Write a flat interleaved float buffer as a 32-bit IEEE_FLOAT stereo WAV.

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


def dispose_sfizz_cache() -> None:
    """Release all cached sfizz Synth instances.

    pysfizz's Synth is a nanobind-bound C++ object that holds threads and
    file handles. Leaving instances in the module-level cache at interpreter
    shutdown triggers "nanobind: leaked N instances" warnings (and may delay
    process exit while the synth's background load/gc threads are reaped).
    Call this from the application's aboutToQuit handler to drop the cache
    before Python finalization.
    """
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
    global _VST_ENGINE, _VST_GRAPH
    _VST_ENGINE = None
    _VST_GRAPH = None
    _VST_ENGINE = None
    _VST_GRAPH = None


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
    frames_needed = int(total_seconds * sample_rate)

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
        cache_key = (str(sfz_path), sample_rate, polyphony, quality)
        if cache_key in _SFIZZ_SYNTH_CACHE:
            synth = _SFIZZ_SYNTH_CACHE[cache_key]
            synth.all_sound_off()
        else:
            synth = _sfizz.Synth(sample_rate, _SFIZZ_BLOCK_FRAMES)
            synth.enable_freewheeling()
            synth.set_num_voices(max(1, min(polyphony, 512)))
            synth.set_sample_quality(quality)
            if not synth.load_sfz_file(str(sfz_path)):
                return False
            _SFIZZ_SYNTH_CACHE[cache_key] = synth

        # Drum synth: load drum-only SFZ that sits next to the main bank.
        # Falls back to melodic synth (no separate drums) if file is absent.
        drum_sfz = Path(sfz_path).parent / "General_MIDI_sfizz_drums.sfz"
        drum_synth = None
        if drum_sfz.exists():
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
                # Channel 10 (index 9) = GM drums → route to drum synth
                is_drum = getattr(msg, "channel", None) == 9 and drum_synth is not None
                target = drum_synth if is_drum else synth
                if msg.type == "note_on" and msg.velocity > 0:
                    target.note_on(delay, msg.note, msg.velocity)
                elif msg.type in ("note_off", "note_on"):
                    target.note_off(delay, msg.note, 0)
                elif msg.type == "control_change":
                    target.cc(delay, msg.control, msg.value)
                elif msg.type == "pitchwheel":
                    target.pitch_wheel(delay, msg.pitch)
                elif msg.type == "program_change" and not is_drum:
                    synth.program_change(delay, msg.program)
                event_index += 1

            # Render both synths and sum into a stereo mix
            left, right = synth.render_block()
            left_arr = np.asarray(left, dtype=np.float32)
            right_arr = np.asarray(right, dtype=np.float32)
            if drum_synth is not None:
                d_left, d_right = drum_synth.render_block()
                left_arr = left_arr + np.asarray(d_left, dtype=np.float32)
                right_arr = right_arr + np.asarray(d_right, dtype=np.float32)
            # Tanh soft-clip per block: prevents polyphony sum from creating
            # harsh flat-top clipping inside sfizz's output. tanh maps the
            # summed signal smoothly to [-1, 1], so even when 5-6 notes
            # overlap the result stays musical instead of harshly clipped.
            # Applied at 0.7 drive so signals below ~0.5 pass nearly linear.
            if not use_vst_chain:
                left_arr = np.tanh(left_arr * 0.7) / np.tanh(0.7)
                right_arr = np.tanh(right_arr * 0.7) / np.tanh(0.7)
            block = np.column_stack((left_arr, right_arr)).flatten()
            interleaved_blocks.append(block)
            rendered += len(left_arr)
    except Exception:
        return False

    if interleaved_blocks:
        buf_arr = np.concatenate(interleaved_blocks)[: frames_needed * 2]
    else:
        buf_arr = np.zeros(0, dtype=np.float32)

    if use_vst_chain:
        if _render_sfizz_vst_chain(buf_arr, sample_rate, output_path):
            return True
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

        # ── Step 5: Loudness normalize to -14 LUFS ──
        # Measure and adjust. The clipper+comp already bring us close;
        # this fine-tunes to exact target. Gain is applied BEFORE limiter
        # so no new overs are created.
        mono_ms = np.mean(stereo, axis=0)
        target_lufs = -14.0
        win = int(0.4 * sample_rate)
        if len(mono_ms) > win:
            blocks = []
            for i in range(0, len(mono_ms) - win, win):
                block_rms = np.sqrt(np.mean(mono_ms[i : i + win] ** 2))
                if block_rms > 1e-6:
                    blocks.append(block_rms)
            if blocks:
                # Simplified LUFS: mean of 400ms block RMS in dB, minus
                # the ITU-R BS.1770 K-weighting offset (-0.691) and a
                # correction for the high-pass shelf in K-weighting (-1.5 dB).
                # Empirically calibrated against DMC renders.
                mean_rms = np.mean(blocks)
                current_lufs = 20 * np.log10(mean_rms) - 0.691 + 1.5
                gain_db = target_lufs - current_lufs
                # Clamp to ±3 dB — the clipper should do the heavy lifting,
                # not the normalize. Large gain = crushed dynamics.
                # No clamp. The clipper (step 4) already shaped the signal,
                # and the true-peak limiter (step 6) catches any overs.
                # To hit -14 LUFS the gain needs ~+5-6 dB on quiet renders.
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
    if env and Path(env).exists() and Path(env).suffix.lower() == ".sfz":
        return Path(env)
    # Bundled Discord GM bank: generate a combined GM.sfz on first use.
    discord_bank = Path("/Volumes/External/Code/Birka/data/Discord-SFZ-GM-Bank")
    if discord_bank.is_dir():
        combined = _build_discord_gm_sfz(discord_bank)
        if combined is not None:
            return combined
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
