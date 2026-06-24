"""
stereo_diff_v2.py

Изменения vs v1:
1. Реалистичный тестовый сигнал — розовый шум с корреляцией ~0.6
   (типичная запись: не моно и не полностью разделённая).
2. Несколько тест-сигналов: mono-ish, normal, wide — тройная проверка.
3. Diff теперь показывает относительное изменение к предыдущему слоту,
   а не к первому снимку.
4. Итоговая таблица "проблемных плагинов" в конце.
5. Neutral-preset guard: если kot добавляет > 3dB — флажок GAIN BUG.
"""

import os, sys, math, itertools
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

# ── тестовые сигналы ───────────────────────────────────────────────────────

def _pink(frames: int, rng) -> np.ndarray:
    """Approx pink noise via 1/f shaping of white noise."""
    w = rng.standard_normal(frames)
    f = np.fft.rfft(w)
    freq = np.fft.rfftfreq(frames)
    freq[0] = 1e-6
    f *= 1.0 / np.sqrt(freq)
    return np.fft.irfft(f, n=frames).astype(np.float32)

def make_signal(kind: str, duration: float) -> np.ndarray:
    frames = int(SR * duration)
    p1 = _pink(frames, RNG)
    p2 = _pink(frames, RNG)
    p1 /= np.max(np.abs(p1)) + 1e-9
    p2 /= np.max(np.abs(p2)) + 1e-9

    if kind == "mono":
        # L≈R, corr ≈ +0.95 — узкая запись типа центрального вокала
        L = p1 * 0.5
        R = p1 * 0.5 + p2 * 0.05
    elif kind == "normal":
        # corr ≈ +0.55 — типичный мастер
        L = p1 * 0.7 + p2 * 0.3
        R = p1 * 0.5 + p2 * 0.5
    elif kind == "wide":
        # corr ≈ +0.05 — широкое стерео / EDM
        L = p1
        R = p2
    else:
        raise ValueError(kind)

    scale = 0.25
    return np.stack([L * scale, R * scale]).astype(np.float32)

# ── snapshot ───────────────────────────────────────────────────────────────

def _snapshot(audio: np.ndarray) -> dict:
    L, R = audio[0], audio[1]
    mid  = (L + R) * 0.5
    side = (L - R) * 0.5
    def rms(x): return float(np.sqrt(np.mean(x**2)))
    def db(x):  return 20 * math.log10(rms(x) + 1e-12)
    peak = float(np.max(np.abs(audio)))
    std_L, std_R = L.std(), R.std()
    corr = float(np.corrcoef(L, R)[0, 1]) if (std_L > 1e-9 and std_R > 1e-9) else 1.0
    width = rms(side) / (rms(mid) + 1e-12)
    return dict(
        corr=corr, width=width,
        db_mid=db(mid), db_sid=db(side),
        db_pk=20*math.log10(peak+1e-12),
        db_L=db(L), db_R=db(R),
    )

def _diff(prev: dict | None, cur: dict) -> list[str]:
    if prev is None:
        return []
    flags = []
    if abs(cur["corr"]   - prev["corr"])   > 0.06: flags.append(f"corr{cur['corr']-prev['corr']:+.2f}")
    if abs(cur["width"]  - prev["width"])  > 0.12: flags.append(f"width{cur['width']-prev['width']:+.2f}")
    if abs(cur["db_mid"] - prev["db_mid"]) > 0.4:  flags.append(f"mid{cur['db_mid']-prev['db_mid']:+.1f}dB")
    if abs(cur["db_sid"] - prev["db_sid"]) > 0.4:  flags.append(f"side{cur['db_sid']-prev['db_sid']:+.1f}dB")
    return flags

# ── one full render up to a tap ────────────────────────────────────────────

TAP_ORDER = ["tape","spiff","soothe","pro_q","pro_mb","kot","sdrr","reverb","fresh","limiter"]

def render_tap(tap: str, stereo_in: np.ndarray) -> np.ndarray:
    engine = daw.RenderEngine(SR, BUF)
    pb      = engine.make_playback_processor("pb", stereo_in)
    procs   = {
        "tape":    engine.make_plugin_processor("tape",    _VST_PLUGIN_PATHS["chow"]),
        "sdrr":    engine.make_plugin_processor("sdrr",    _VST_PLUGIN_PATHS["sdrr"]),
        "spiff":   engine.make_plugin_processor("spiff",   _VST_PLUGIN_PATHS["spiff"]),
        "soothe":  engine.make_plugin_processor("soothe",  _VST_PLUGIN_PATHS["soothe"]),
        "pro_q":   engine.make_plugin_processor("pro_q",   _VST_PLUGIN_PATHS["pro_q"]),
        "pro_mb":  engine.make_plugin_processor("pro_mb",  _VST_PLUGIN_PATHS["pro_mb"]),
        "kot":     engine.make_plugin_processor("kot",     _VST_PLUGIN_PATHS["kot"]),
        "fresh":   engine.make_plugin_processor("fresh",   _VST_PLUGIN_PATHS["fresh"]),
        "reverb":  engine.make_plugin_processor("reverb",  _VST_PLUGIN_PATHS["reverb"]),
        "limiter": engine.make_plugin_processor("limiter", _VST_PLUGIN_PATHS["limiter"]),
    }
    # dummies для _apply_vst_preset если он требует cho/ste
    cho_d = engine.make_plugin_processor("cho_d", _VST_PLUGIN_PATHS.get("cho", _VST_PLUGIN_PATHS["reverb"]))
    ste_d = engine.make_plugin_processor("ste_d", _VST_PLUGIN_PATHS.get("ste", _VST_PLUGIN_PATHS["reverb"]))

    _configure_kotelnikov_ge(procs["kot"])
    _configure_limiter(procs["limiter"])

    chain = TAP_ORDER[: TAP_ORDER.index(tap) + 1]
    conns = [(pb, [])]
    prev  = "pb"
    for name in chain:
        conns.append((procs[name], [prev]))
        prev = name
    engine.load_graph(conns)

    _apply_vst_preset(
        procs["tape"], procs["pro_q"], procs["pro_mb"], procs["reverb"],
        cho_d, ste_d,
        procs["fresh"], procs["spiff"], procs["sdrr"], procs["soothe"],
        _VST_NEUTRAL_PRESET,
    )
    engine.render(5.0)
    return engine.get_audio(tap)

# ── run all taps for all signal types ─────────────────────────────────────

SIGNALS = ["mono", "normal", "wide"]
results: dict[str, dict[str, dict]] = {}   # signal → tap → snapshot

for sig in SIGNALS:
    stereo_in = make_signal(sig, 5.0)
    results[sig] = {}
    prev = None
    for tap in TAP_ORDER:
        audio = render_tap(tap, stereo_in)
        results[sig][tap] = _snapshot(audio)

# ── restore stderr, print ──────────────────────────────────────────────────

os.dup2(_old, 2); os.close(_old); os.close(_dn)

# Проблемные плагины (изменение на "normal" сигнале)
issues: dict[str, list[str]] = {t: [] for t in TAP_ORDER}

for sig in SIGNALS:
    snaps = results[sig]
    prev  = None
    for tap in TAP_ORDER:
        flags = _diff(prev, snaps[tap])
        if flags:
            for f in flags:
                entry = f"[{sig}] {f}"
                if entry not in issues[tap]:
                    issues[tap].append(entry)
        prev = snaps[tap]

# ── print per-signal tables ────────────────────────────────────────────────

for sig in SIGNALS:
    snaps = results[sig]
    print(f"\n  ══ signal: {sig:6s} ══════════════════════════════════════════════════════")
    print(f"  {'plugin':<10} {'corr':>6} {'width':>6} {'mid':>7} {'side':>7} {'peak':>7}  delta")
    print(f"  {'─'*70}")
    prev = None
    for tap in TAP_ORDER:
        s     = snaps[tap]
        flags = _diff(prev, s)
        marker = ("  << " + "  ".join(flags)) if flags else ""
        print(f"  {tap:<10} {s['corr']:>+6.3f} {s['width']:>6.3f} "
              f"{s['db_mid']:>7.1f} {s['db_sid']:>7.1f} {s['db_pk']:>7.1f}"
              f"{marker}")
        prev = s

# ── summary: что реально меняет стерео поле ───────────────────────────────

print("\n  ══ SUMMARY: плагины изменяющие стерео ══════════════════════════════")
print(f"  {'plugin':<10}  изменения")
print(f"  {'─'*60}")
any_issue = False
for tap in TAP_ORDER:
    if issues[tap]:
        print(f"  {tap:<10}  {',  '.join(issues[tap])}")
        any_issue = True
if not any_issue:
    print("  нет значимых изменений (все в пределах порогов)")

print(f"\n  пороги: |Δcorr|>0.06  |Δwidth|>0.12  |Δlevel|>0.4dB")
print()

# ── специальная проверка: gain bug ────────────────────────────────────────

print("  ══ GAIN CHECK (neutral preset): ожидаем ≈ 0dB gain ════════════════")
for sig in SIGNALS:
    snaps  = results[sig]
    in_pk  = 20 * math.log10(0.25 + 1e-12)   # входной peak ~= 0.25 → -12 dBFS
    out_pk = snaps["limiter"]["db_pk"]
    gain   = out_pk - in_pk
    flag   = "  !! GAIN BUG" if abs(gain) > 3 else ""
    print(f"  [{sig}]  вход≈{in_pk:.1f}dB  выход={out_pk:.1f}dB  Δ={gain:+.1f}dB{flag}")
print()
