"""
stereo_diff.py — per-slot stereo field snapshot across the master chain.

Renders the chain N times, cutting it at each "tap point". On each run
the graph ends at a different processor, so engine.get_audio() returns
the signal at exactly that stage.  We then compute correlation, M/S
levels, width ratio and peak — giving a table that shows *where* the
stereo field changes.

Usage:
    python stereo_diff.py
"""

import os, sys, math
_old = os.dup(2); _dn = os.open(os.devnull, os.O_WRONLY); os.dup2(_dn, 2)

sys.path.insert(0, "/Volumes/External/Code/Birka")
sys.path.insert(0, "/Volumes/External/Code/Birka/.venv/lib/python3.9/site-packages")

import numpy as np
import dawdreamer as daw

from src.birka.infrastructure.midi_renderer import (
    _VST_PLUGIN_PATHS, _VST_SAMPLE_RATE, _VST_BUFFER_SIZE,
    _configure_kotelnikov_ge, _configure_limiter, _configure_nova,
    _apply_vst_preset, _VST_NEUTRAL_PRESET,
)

# ── helpers ────────────────────────────────────────────────────────────────

def _snapshot(L: np.ndarray, R: np.ndarray) -> dict:
    mid  = (L + R) * 0.5
    side = (L - R) * 0.5
    rms_L   = float(np.sqrt(np.mean(L**2)))
    rms_R   = float(np.sqrt(np.mean(R**2)))
    rms_mid = float(np.sqrt(np.mean(mid**2)))
    rms_sid = float(np.sqrt(np.mean(side**2)))
    peak    = float(np.max(np.abs(np.stack([L, R]))))
    corr    = float(np.corrcoef(L, R)[0, 1]) if (L.std() > 1e-9 and R.std() > 1e-9) else 1.0
    width   = rms_sid / (rms_mid + 1e-12)
    db_L    = 20 * math.log10(rms_L   + 1e-12)
    db_R    = 20 * math.log10(rms_R   + 1e-12)
    db_mid  = 20 * math.log10(rms_mid + 1e-12)
    db_sid  = 20 * math.log10(rms_sid + 1e-12)
    db_pk   = 20 * math.log10(peak    + 1e-12)
    return dict(
        corr=corr, width=width,
        db_L=db_L, db_R=db_R, db_mid=db_mid, db_sid=db_sid, db_pk=db_pk,
    )

def _corr_label(c: float) -> str:
    if c >  0.85: return "NARROW/mono"
    if c >  0.4:  return "balanced  "
    if c > -0.2:  return "wide      "
    return               "OOP-RISK  "   # out-of-phase

def _width_label(w: float) -> str:
    if w > 1.5: return "VERY WIDE"
    if w > 0.9: return "wide     "
    if w > 0.5: return "normal   "
    return              "narrow   "

def _diff_marker(prev: dict | None, cur: dict) -> str:
    """Return a short marker if something changed significantly."""
    if prev is None:
        return ""
    flags = []
    dc = cur["corr"]  - prev["corr"]
    dw = cur["width"] - prev["width"]
    dm = cur["db_mid"] - prev["db_mid"]
    ds = cur["db_sid"] - prev["db_sid"]
    if abs(dc) > 0.08:  flags.append(f"corr{dc:+.2f}")
    if abs(dw) > 0.15:  flags.append(f"width{dw:+.2f}")
    if abs(dm) > 0.5:   flags.append(f"mid{dm:+.1f}dB")
    if abs(ds) > 0.5:   flags.append(f"side{ds:+.1f}dB")
    return "  << " + "  ".join(flags) if flags else ""

# ── test signal ────────────────────────────────────────────────────────────

DURATION = 5.0
SR       = _VST_SAMPLE_RATE
BUF      = _VST_BUFFER_SIZE
frames   = int(SR * DURATION)
t        = np.arange(frames) / SR

# L: 100 Hz + noise, R: 800 Hz + noise — clearly separated channels
L_in = (np.sin(2 * math.pi * 100 * t) * 0.25
        + np.random.default_rng(0).standard_normal(frames) * 0.02).astype(np.float32)
R_in = (np.sin(2 * math.pi * 800 * t) * 0.25
        + np.random.default_rng(1).standard_normal(frames) * 0.02).astype(np.float32)
stereo_in = np.stack([L_in, R_in])

# ── tap points: (label, processor_name_to_read) ────────────────────────────
# We cut the graph at each tap — processors AFTER the tap are not loaded.
# The graph always starts at pb → tape → … → <tap>.

TAP_ORDER = [
    "tape", "sdrr", "spiff", "soothe",
    "pro_q", "pro_mb", "nova", "kot",
    "fresh", "reverb", "limiter",
]

# ── run one render per tap ─────────────────────────────────────────────────

snapshots: dict[str, dict] = {}

for tap in TAP_ORDER:
    engine = daw.RenderEngine(SR, BUF)

    pb      = engine.make_playback_processor("pb", stereo_in)
    tape    = engine.make_plugin_processor("tape",   _VST_PLUGIN_PATHS["chow"])
    sdrr    = engine.make_plugin_processor("sdrr",   _VST_PLUGIN_PATHS["sdrr"])
    spiff   = engine.make_plugin_processor("spiff",  _VST_PLUGIN_PATHS["spiff"])
    soothe  = engine.make_plugin_processor("soothe", _VST_PLUGIN_PATHS["soothe"])
    pro_q   = engine.make_plugin_processor("pro_q",  _VST_PLUGIN_PATHS["pro_q"])
    pro_mb  = engine.make_plugin_processor("pro_mb", _VST_PLUGIN_PATHS["pro_mb"])
    nova    = engine.make_plugin_processor("nova",   _VST_PLUGIN_PATHS["nova"])
    kot     = engine.make_plugin_processor("kot",    _VST_PLUGIN_PATHS["kot"])
    fresh   = engine.make_plugin_processor("fresh",  _VST_PLUGIN_PATHS["fresh"])
    reverb  = engine.make_plugin_processor("reverb", _VST_PLUGIN_PATHS["reverb"])
    limiter = engine.make_plugin_processor("limiter",_VST_PLUGIN_PATHS["limiter"])

    _configure_kotelnikov_ge(kot)
    _configure_limiter(limiter)
    _configure_nova(nova)

    proc_map = {
        "tape": tape, "sdrr": sdrr, "spiff": spiff, "soothe": soothe,
        "pro_q": pro_q, "pro_mb": pro_mb, "nova": nova, "kot": kot,
        "fresh": fresh, "reverb": reverb, "limiter": limiter,
    }

    # Build graph up to (and including) the tap
    tap_idx  = TAP_ORDER.index(tap)
    chain    = TAP_ORDER[: tap_idx + 1]

    connections = [(pb, [])]
    prev_name = "pb"
    for name in chain:
        connections.append((proc_map[name], [prev_name]))
        prev_name = name

    engine.load_graph(connections)

    _apply_vst_preset(
        tape, pro_q, pro_mb, reverb,
        engine.make_plugin_processor("cho_dummy", _VST_PLUGIN_PATHS.get("cho", _VST_PLUGIN_PATHS["reverb"])),
        engine.make_plugin_processor("ste_dummy", _VST_PLUGIN_PATHS.get("ste", _VST_PLUGIN_PATHS["reverb"])),
        fresh, spiff, sdrr, soothe,
        _VST_NEUTRAL_PRESET,
    )

    engine.render(DURATION)
    audio = engine.get_audio(tap)
    snapshots[tap] = _snapshot(audio[0], audio[1])

# ── print table ────────────────────────────────────────────────────────────

os.dup2(_old, 2); os.close(_old); os.close(_dn)

HDR = (f"{'plugin':<10}  {'corr':>6}  {'width':>6}  "
       f"{'mid':>7}  {'side':>7}  {'peak':>7}  "
       f"{'corr?':<12}  {'width?':<10}  delta")
SEP = "─" * len(HDR)

print()
print("  STEREO FIELD DIFF  —  per-plugin tap")
print(SEP)
print(HDR)
print(SEP)

prev = None
for name in TAP_ORDER:
    s = snapshots[name]
    diff = _diff_marker(prev, s)
    print(
        f"  {name:<8}  "
        f"{s['corr']:>+6.3f}  "
        f"{s['width']:>6.3f}  "
        f"{s['db_mid']:>7.1f}  "
        f"{s['db_sid']:>7.1f}  "
        f"{s['db_pk']:>7.1f}  "
        f"{_corr_label(s['corr']):<12}  "
        f"{_width_label(s['width']):<10}"
        f"{diff}"
    )
    prev = s

print(SEP)
print()
print("  corr: +1=identical L/R (mono)  0=uncorrelated  -1=anti-phase")
print("  width = side_rms / mid_rms  (<0.5 narrow  0.5–1.0 normal  >1.0 wide)")
print("  delta flags threshold: |Δcorr|>0.08  |Δwidth|>0.15  |Δlevel|>0.5dB")
print()
