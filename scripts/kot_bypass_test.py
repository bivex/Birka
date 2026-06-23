"""
kot_bypass_test.py — A/B: Kotelnikov GE in-chain vs bypassed.
Изолирует точный contribution kot на трёх типовых сигналах.
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

SR  = _VST_SAMPLE_RATE
BUF = _VST_BUFFER_SIZE
RNG = np.random.default_rng(42)

def _pink(frames, rng):
    w = rng.standard_normal(frames)
    f = np.fft.rfft(w)
    freq = np.fft.rfftfreq(frames)
    freq[0] = 1e-6
    f *= 1.0 / np.sqrt(freq)
    return np.fft.irfft(f, n=frames).astype(np.float32)

def make_signal(kind, duration=5.0):
    frames = int(SR * duration)
    p1 = _pink(frames, RNG)
    p2 = _pink(frames, RNG)
    p1 /= np.max(np.abs(p1)) + 1e-9
    p2 /= np.max(np.abs(p2)) + 1e-9
    if kind == "mono":
        L = p1 * 0.5; R = p1 * 0.5 + p2 * 0.05
    elif kind == "normal":
        L = p1 * 0.7 + p2 * 0.3; R = p1 * 0.5 + p2 * 0.5
    elif kind == "wide":
        L = p1; R = p2
    else:
        raise ValueError(kind)
    return np.stack([L * 0.25, R * 0.25]).astype(np.float32)

def _snap(audio):
    L, R = audio[0], audio[1]
    mid, side = (L+R)*0.5, (L-R)*0.5
    def rms(x): return float(np.sqrt(np.mean(x**2)))
    def db(x):  return 20 * math.log10(rms(x) + 1e-12)
    peak = float(np.max(np.abs(audio)))
    corr = float(np.corrcoef(L, R)[0, 1]) if (L.std() > 1e-9 and R.std() > 1e-9) else 1.0
    width = rms(side) / (rms(mid) + 1e-12)
    return dict(corr=corr, width=width, db_mid=db(mid), db_sid=db(side),
                db_pk=20*math.log10(peak+1e-12), db_L=db(L), db_R=db(R))

def _build_chain(stereo_in, with_kot=True):
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

    if not with_kot:
        kot.set_parameter(12, 1.0)  # Dry Wet = 0% wet → байпас

    cho_d = engine.make_plugin_processor("cho_d", _VST_PLUGIN_PATHS.get("cho", _VST_PLUGIN_PATHS["reverb"]))
    ste_d = engine.make_plugin_processor("ste_d", _VST_PLUGIN_PATHS.get("ste", _VST_PLUGIN_PATHS["reverb"]))

    nodes = dict(tape=tape,sdrr=sdrr,spiff=spiff,soothe=soothe,
                 pro_q=pro_q,pro_mb=pro_mb,nova=nova,kot=kot,
                 fresh=fresh,reverb=reverb,limiter=limiter)

    chain = ["tape","sdrr","spiff","soothe","pro_q","pro_mb","nova",
             "kot","fresh","reverb","limiter"]
    if not with_kot:
        chain = [c for c in chain if c != "kot"]

    conns = [(pb, [])]
    prev = "pb"
    for name in chain:
        conns.append((nodes[name], [prev]))
        prev = name
    engine.load_graph(conns)

    _apply_vst_preset(
        tape, pro_q, pro_mb, reverb, cho_d, ste_d,
        fresh, spiff, sdrr, soothe, _VST_NEUTRAL_PRESET,
    )
    return engine, chain

def render_chain(stereo_in, with_kot=True):
    engine, chain = _build_chain(stereo_in, with_kot)
    target = "limiter"
    engine.render(5.0)
    return engine.get_audio(target)

SIGNALS = ["mono", "normal", "wide"]
os.dup2(_old, 2); os.close(_old); os.close(_dn)

print("═" * 72)
print("  KOTELNIKOV GE  —  bypass A/B test (premium chain)")
print("═" * 72)
print()
print("  Neutral preset: thr -13dB, ratio 1.6:1, attack 28ms, make-up +1dB")
print("  Bypass mode:   Dry Wet idx12 = 1.0  (100% dry = bypassed)")
print()

all_results = {}

for sig in SIGNALS:
    stereo_in = make_signal(sig)
    ref = render_chain(stereo_in, with_kot=True)
    byp = render_chain(stereo_in, with_kot=False)

    r = _snap(ref)
    b = _snap(byp)
    delta = {k: r[k] - b[k] for k in r}
    all_results[sig] = dict(ref=r, byp=b, delta=delta)

    print(f"  ── signal: {sig} ──")
    print(f"  {'metric':<10} {'with kot':>10} {'bypassed':>10} {'delta':>10}")
    print(f"  {'─'*44}")
    for k in ["corr","width","db_mid","db_sid","db_pk","db_L","db_R"]:
        print(f"  {k:<10} {r[k]:>+10.3f} {b[k]:>+10.3f} {delta[k]:>+10.3f}")
    print()

    flags = []
    if abs(delta["db_pk"]) > 0.5:  flags.append(f"peak {delta['db_pk']:+.1f}dB")
    if abs(delta["corr"]) > 0.04:  flags.append(f"corr {delta['corr']:+.3f}")
    if abs(delta["width"]) > 0.08: flags.append(f"width {delta['width']:+.3f}")
    if abs(delta["db_mid"]) > 0.5: flags.append(f"mid {delta['db_mid']:+.1f}dB")
    if abs(delta["db_sid"]) > 0.5: flags.append(f"side {delta['db_sid']:+.1f}dB")
    print(f"  {'⚡ kot impact:' if flags else '  ✓ kot — '}{', '.join(flags) if flags else 'в пределах порога'}")
    print()

print("═" * 72)
print("  VERDICT")
print("═" * 72)
print()
avg_mid = np.mean([abs(all_results[s]["delta"]["db_mid"]) for s in SIGNALS])
avg_sid = np.mean([abs(all_results[s]["delta"]["db_sid"]) for s in SIGNALS])
avg_pk  = np.mean([abs(all_results[s]["delta"]["db_pk"])  for s in SIGNALS])
avg_cor = np.mean([abs(all_results[s]["delta"]["corr"])  for s in SIGNALS])
avg_w   = np.mean([abs(all_results[s]["delta"]["width"]) for s in SIGNALS])
print(f"  avg |Δmid|   = {avg_mid:.2f} dB")
print(f"  avg |Δside|  = {avg_sid:.2f} dB")
print(f"  avg |Δpeak|  = {avg_pk:.2f} dB")
print(f"  avg |Δcorr|  = {avg_cor:.3f}")
print(f"  avg |Δwidth| = {avg_w:.3f}")
print()
if avg_pk > 2.0:
    print("  !! GAIN BUG: kot добавляет >2 dB — makeup gain (idx 10=0.58 / idx 14=0.55) слишком высокий")
elif avg_pk > 1.0:
    print("  ⚠ Makeup gain повышено — проверь idx 10 (Makeup) и idx 14 (Out Gain)")
else:
    print("  ✓ Makeup gain в норме")
print()
if avg_cor > 0.04 or avg_w > 0.1:
    print("  ⚠ Kot влияет на стерео-поле — проверь stereo link / sidechain HP")
else:
    print("  ✓ Kot стерео-совместим (mid/side обработаны равномерно)")
print()
