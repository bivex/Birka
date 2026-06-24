"""Fast-master chain profiler.

Profiles lightweight VST chains for a quick preview/draft master, vs the full
10-plugin mastering chain. Builds each candidate via dawdreamer, times a
SINGLE-PASS render of a 20s @96k stereo signal (3 runs, best), and measures the
resulting LUFS + true-peak so speed gains can be weighed against quality.

Run: .venv/bin/python scripts/_vst_fastmaster_profile.py
"""
import sys, time, os, tempfile, numpy as np
sys.path.insert(0, "/Volumes/External/Code/Birka/src")
import dawdreamer as daw
from birka.infrastructure import midi_renderer as mr

SR = mr._VST_SAMPLE_RATE       # 96000
BS = mr._VST_BUFFER_SIZE       # 512
P = mr._VST_PLUGIN_PATHS

dur = 20.0
n = int(dur * SR)
rng = np.random.default_rng(0)
x = (rng.standard_normal(n) * 0.1).astype(np.float32)
t = np.arange(n) / SR
x += (0.2 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)
audio = np.stack([x, x]).astype(np.float32)  # (2, n)

devnull = open(os.devnull, "w"); old = os.dup(2); os.dup2(devnull.fileno(), 2)


def _freq_to_val(f):
    import math
    f = max(10.0, min(30000.0, float(f)))
    return math.log10(f / 10.0) / math.log10(3000.0)


def _gain_to_val(g):
    g = max(-30.0, min(30.0, float(g)))
    return (g + 30.0) / 60.0


def cfg_proq_light(pq):
    """HPF (B1) + gentle high tilt shelf (B2). No dynamics, zero latency."""
    pq.set_parameter(0, 1.0); pq.set_parameter(1, 1.0)
    pq.set_parameter(2, _freq_to_val(30.0)); pq.set_parameter(5, 0.20)  # Low Cut 30Hz
    pq.set_parameter(7, 0.5)
    # B2 high shelf +1 dB @ 10k for a touch of air (Used must be 1)
    pq.set_parameter(23, 1.0); pq.set_parameter(24, 1.0)
    pq.set_parameter(25, _freq_to_val(10000.0))
    pq.set_parameter(26, _gain_to_val(1.0))
    pq.set_parameter(28, 0.30)  # High Shelf


def cfg_kot_eco(kot):
    """Light 1-2 dB glue, no fancy parallel path."""
    kot.set_parameter(0, 0.45)   # Threshold ~ -17 dB (gentle)
    kot.set_parameter(5, 0.30)   # Ratio ~1.5:1
    kot.set_parameter(12, 0.0)   # Dry/Wet = 100% wet
    kot.set_parameter(14, 0.55)  # Out gain ~0 dB


def cfg_tape_light(tape):
    tape.set_parameter(16, 0.10)  # Tape Drive low
    tape.set_parameter(2, 1.0)    # Dry/Wet full


def cfg_prol_fast(lim, oversample=0.166, tp_on=False):
    cfg = mr._configure_limiter
    cfg(lim)                      # base premium config
    lim.set_parameter(9, oversample)        # 0.166=2x, 0.0=Off
    lim.set_parameter(10, 1.0 if tp_on else 0.0)  # True Peak limiting


eng = daw.RenderEngine(SR, BS)
_made = {}
def make(key):
    if key not in _made:
        _made[key] = eng.make_plugin_processor("p_" + key, P[key])
    return _made[key]


def time_chain(name, build):
    """build() returns (graph_connections, list_of_(proc,configfn)).
    Times a single-pass render through the last node, then LUFS/TP."""
    procs = build()
    # warmup
    eng.render(dur)
    best = 1e9
    for _ in range(3):
        t0 = time.perf_counter(); eng.render(dur); best = min(best, time.perf_counter() - t0)
    out = eng.get_audio(procs[-1])
    try:
        import pyloudnorm as pyln
        lufs = pyln.Meter(SR).integrated_loudness(out.T)
    except Exception:
        lufs = float("nan")
    from scipy.signal import resample_poly
    tp = max(float(np.max(np.abs(resample_poly(out[c], 4, 1)))) for c in range(out.shape[0]))
    return name, best, lufs, 20 * np.log10(tp + 1e-9)


def build_variantA():
    pb = eng.make_playback_processor("pbA", audio)
    pq = make("pro_q"); cfg_proq_light(pq)
    kot = make("kot"); cfg_kot_eco(kot)
    tape = make("chow"); cfg_tape_light(tape)
    lim = make("limiter"); cfg_prol_fast(lim, oversample=0.166, tp_on=False)
    eng.load_graph([(pb, []), (pq, ["pbA"]), (kot, ["p_pro_q"]),
                    (tape, ["p_kot"]), (lim, ["p_chow"])])
    return ["p_pro_q", "p_kot", "p_chow", "p_limiter"]


def build_variantB():
    pb = eng.make_playback_processor("pbB", audio)
    pq = make("pro_q"); cfg_proq_light(pq)
    lim = make("limiter"); cfg_prol_fast(lim, oversample=0.166, tp_on=False)
    eng.load_graph([(pb, []), (pq, ["pbB"]), (lim, ["p_pro_q"])])
    return ["p_pro_q", "p_limiter"]


def build_recommended():
    # Pro-Q + Kotelnikov + Pro-L (2x, no TP) — best speed/quality balance
    pb = eng.make_playback_processor("pbR", audio)
    pq = make("pro_q"); cfg_proq_light(pq)
    kot = make("kot"); cfg_kot_eco(kot)
    lim = make("limiter"); cfg_prol_fast(lim, oversample=0.166, tp_on=False)
    eng.load_graph([(pb, []), (pq, ["pbR"]), (kot, ["p_pro_q"]), (lim, ["p_kot"])])
    return ["p_pro_q", "p_kot", "p_limiter"]


def build_prol_only():
    pb = eng.make_playback_processor("pbL", audio)
    lim = make("limiter"); cfg_prol_fast(lim, oversample=0.166, tp_on=False)
    eng.load_graph([(pb, []), (lim, ["pbL"])])
    return ["p_limiter"]


rows = []
for name, fn in [
    ("A: ProQ→Kot→Tape→ProL(2x)", build_variantA),
    ("REC: ProQ→Kot→ProL(2x)", build_recommended),
    ("B: ProQ→ProL(2x)", build_variantB),
    ("ProL only(2x)", build_prol_only),
]:
    rows.append(time_chain(name, fn))

os.dup2(old, 2); os.close(old); devnull.close()

print(f"{'fast chain':30s} {'render_s':>9} {'xRT':>6} {'~3.5s_trk':>10} {'LUFS':>7} {'dBTP':>7}")
for name, dt, lufs, tp in rows:
    # extrapolate to a ~3.5s short track (typical loop/preview)
    short = dt * (3.5 / dur)
    print(f"{name:30s} {dt:9.2f} {dur/dt:6.1f} {short:10.2f} {lufs:7.1f} {tp:7.2f}")
print("\nReference: FULL 10-plugin chain (two-pass) was ~8.3s for 20s (2.4x RT).")
print("Single-pass fast chains above; multiply by ~1 (no 2nd pass) for preview.")
