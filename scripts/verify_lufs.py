"""
verify_lufs.py — true LUFS (ITU-R BS.1770) via ffmpeg ebur128.
Сравнивает prod _measure_lufs из midi_renderer (используется в двухпроходной
калибровке) с истинным измерением ebur128.
"""
import os, sys, math, subprocess, tempfile, re, wave
_old = os.dup(2); _dn = os.open(os.devnull, os.O_WRONLY); os.dup2(_dn, 2)

sys.path.insert(0, "/Volumes/External/Code/Birka")
sys.path.insert(0, "/Volumes/External/Code/Birka/.venv/lib/python3.9/site-packages")

import numpy as np
import dawdreamer as daw

from src.birka.infrastructure.midi_renderer import (
    _VST_PLUGIN_PATHS, _VST_SAMPLE_RATE, _VST_BUFFER_SIZE,
    _configure_kotelnikov_ge, _configure_limiter, _configure_nova,
    _apply_vst_preset, _VST_NEUTRAL_PRESET,
    _measure_lufs, TARGET_LOUDNESS_LUFS,
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

def make_signal(kind, duration=8.0):
    frames = int(SR * duration)
    p1 = _pink(frames, RNG); p2 = _pink(frames, RNG)
    p1 /= np.max(np.abs(p1)) + 1e-9
    p2 /= np.max(np.abs(p2)) + 1e-9
    if kind == "mono":
        L = p1 * 0.5; R = p1 * 0.5 + p2 * 0.05
    elif kind == "normal":
        L = p1 * 0.7 + p2 * 0.3; R = p1 * 0.5 + p2 * 0.5
    elif kind == "wide":
        L = p1; R = p2
    return np.stack([L * 0.25, R * 0.25]).astype(np.float32)

def build_engine(stereo_in):
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

    cho_d = engine.make_plugin_processor("cho_d", _VST_PLUGIN_PATHS.get("cho", _VST_PLUGIN_PATHS["reverb"]))
    ste_d = engine.make_plugin_processor("ste_d", _VST_PLUGIN_PATHS.get("ste", _VST_PLUGIN_PATHS["reverb"]))

    chain = [(pb, []),
             (tape, ["pb"]), (sdrr, ["tape"]), (spiff, ["sdrr"]),
             (soothe, ["spiff"]), (pro_q, ["soothe"]),
             (pro_mb, ["pro_q"]), (nova, ["pro_mb"]),
             (kot, ["nova"]), (fresh, ["kot"]),
             (reverb, ["fresh"]), (limiter, ["reverb"])]
    engine.load_graph(chain)
    _apply_vst_preset(
        tape, pro_q, pro_mb, reverb, cho_d, ste_d,
        fresh, spiff, sdrr, soothe, _VST_NEUTRAL_PRESET,
    )
    return engine

def rms_lufs_approx(buf):
    """Production loudness measurement (same _measure_lufs the VST two-pass
    calibration and pedalboard fallback use): pyloudnorm ITU-R BS.1770 with an
    RMS-approx fallback. buf is (channels, samples)."""
    return _measure_lufs(buf, SR)

def ffmpeg_ebur128(wav_path):
    proc = subprocess.run(
        ["ffmpeg", "-i", str(wav_path), "-af", "ebur128=peak=true",
         "-f", "null", "-"],
        capture_output=True, text=True
    )
    text = proc.stderr + proc.stdout
    # Last summary line — pick the final frame values
    lines = text.splitlines()
    # All ebur128 lines; last one has final integrated value
    ebur_lines = [l for l in lines if "I: " in l and "LUFS" in l]
    if not ebur_lines:
        return None, None, None
    last = ebur_lines[-1]
    # Format: t: X  TARGET:-23 LUFS  M:XX.X S:XX.X  I: -XX.X LUFS  LRA: XX.X LU  FTPK: X.X X.X dBFS  TPK: X.X X.X dBFS
    m_i     = re.search(r"I:\s*([-\d.]+)\s*LUFS", last)
    m_lra   = re.search(r"LRA:\s*([-\d.]+)\s*LU", last)
    m_tpk   = re.search(r"TPK:\s*([-\d.]+)\s*([-\d.]+)\s*dBFS", last)
    lufs_i  = float(m_i.group(1))  if m_i  else None
    lra     = float(m_lra.group(1)) if m_lra else None
    tp      = float(m_tpk.group(1)) if m_tpk else None
    return lufs_i, lra, tp

SIGNALS = ["mono", "normal", "wide"]
os.dup2(_old, 2); os.close(_old); os.close(_dn)

print("═" * 66)
print("  LUFS VERIFICATION  —  prod _measure_lufs vs true (ffmpeg ebur128, ITU-R BS.1770)")
print("═" * 66)
print()
print(f"  Target LUFS : {TARGET_LOUDNESS_LUFS} (Apple Sound Check)")
print(f"  prod _measure_lufs (pyloudnorm + RMS fallback) vs true (ebur128)")
print(f"  avg |Δ(lufs)| между сигналами = мерой стабильности калибровки")
print()

tmpdir = tempfile.mkdtemp()
errors = []

for sig in SIGNALS:
    stereo_in = make_signal(sig)
    engine = build_engine(stereo_in)
    engine.render(8.0)
    audio = engine.get_audio("limiter")

    wav_path = os.path.join(tmpdir, f"lufs_{sig}.wav")
    pcm = np.clip(audio.T.flatten() * 32767, -32768, 32767).astype(np.int16)
    with wave.open(wav_path, "w") as wf:
        wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())

    approx = rms_lufs_approx(audio)
    true_i, true_lra, true_tp = ffmpeg_ebur128(wav_path)

    print(f"  ── {sig}")
    print(f"    approx LUFS   : {approx:>+7.2f}" if approx is not None else "    approx: N/A")
    print(f"    true Integrated: {true_i:>+7.2f} LUFS" if true_i is not None else "    true Integrated: N/A")
    print(f"    true LRA       : {true_lra:>+7.2f} LU"  if true_lra is not None else "    true LRA: N/A")
    print(f"    true True Peak : {true_tp:>+7.2f} dBTP" if true_tp is not None else "    true True Peak: N/A")
    if approx and true_i:
        err = approx - true_i
        errors.append(err)
        flag = "  ⚠ ERR > 0.5dB" if abs(err) > 0.5 else "  ✓"
        print(f"    approx→true err: {err:>+.2f} dB{flag}")
    print()

if errors:
    mean_err = float(np.mean(errors))
    print(f"  mean offset (prod _measure_lufs vs true) = {mean_err:+.2f} dB")
    if abs(mean_err) > 0.5:
        print(f"  ⚠ prod _measure_lufs систематически расходится с ebur128 на {abs(mean_err):.1f} dB")
        print(f"     (при активном pyloudnorm это указывает на баг в буферизации/каналах)")
    else:
        print("  ✓ prod _measure_lufs совпадает с ebur128 (ошибка < 0.5 dB)")
else:
    print("  нет данных для сравнения")

print()
