"""Визуальная верификация стерео-поля через VISION 4X и аудио-буфер.
Анализирует корреляцию, ширину, mid/side уровни после каждой секции.
"""
import os, sys, math
old = os.dup(2)
devnull = os.open(os.devnull, os.O_WRONLY)
os.dup2(devnull, 2)

sys.path.insert(0, "/Volumes/External/Code/Birka")
sys.path.insert(0, "/Volumes/External/Code/Birka/.venv/lib/python3.9/site-packages")

import numpy as np
import dawdreamer as daw

from src.birka.infrastructure.midi_renderer import (
    _VST_PLUGIN_PATHS, _VST_SAMPLE_RATE, _VST_BUFFER_SIZE,
    _configure_kotelnikov_ge, _configure_limiter, _configure_nova,
    _apply_vst_preset, _VST_NEUTRAL_PRESET,
    _freq_to_val, _gain_to_val, _q_to_val, _df_size, _df_decay,
    _df_predelay, _df_lowcut, _df_highcut,
)

VISION_PATH = "/Library/Audio/Plug-Ins/VST3/VISION 4X.vst3"
engine = daw.RenderEngine(_VST_SAMPLE_RATE, _VST_BUFFER_SIZE)

tape = engine.make_plugin_processor("tape", _VST_PLUGIN_PATHS["chow"])
sdrr = engine.make_plugin_processor("sdrr", _VST_PLUGIN_PATHS["sdrr"])
spiff = engine.make_plugin_processor("spiff", _VST_PLUGIN_PATHS["spiff"])
soothe = engine.make_plugin_processor("soothe", _VST_PLUGIN_PATHS["soothe"])
pro_q = engine.make_plugin_processor("pro_q", _VST_PLUGIN_PATHS["pro_q"])
pro_mb = engine.make_plugin_processor("pro_mb", _VST_PLUGIN_PATHS["pro_mb"])
kot = engine.make_plugin_processor("kot", _VST_PLUGIN_PATHS["kot"])
fresh = engine.make_plugin_processor("fresh", _VST_PLUGIN_PATHS["fresh"])
cho = engine.make_plugin_processor("cho", _VST_PLUGIN_PATHS["cho"])
ste = engine.make_plugin_processor("ste", _VST_PLUGIN_PATHS["ste"])
reverb = engine.make_plugin_processor("reverb", _VST_PLUGIN_PATHS["reverb"])
vision = engine.make_plugin_processor("vision", VISION_PATH)
limiter = engine.make_plugin_processor("limiter", _VST_PLUGIN_PATHS["limiter"])

_configure_kotelnikov_ge(kot)
_configure_limiter(limiter)

# VISION 4X: Stereo scope mode, Mid waveform
vision.set_parameter(7, 0.333)   # Spectrum = All-round 4096
vision.set_parameter(8, 0.666)   # Waveform = Stereo
vision.set_parameter(9, 0.0)     # Colour = Rocket
vision.set_parameter(19, 0.5)    # Bars = 50
vision.set_parameter(14, 0.5)    # Peak Hold
vision.set_parameter(28, 0.0)    # Bypass Off
vision.set_parameter(11, 0.0)    # Sync On

# Создаём тестовый сигнал — стерео с разными каналами
duration = 5.0
frames = int(_VST_SAMPLE_RATE * duration)
t = np.arange(frames) / _VST_SAMPLE_RATE
# L: низкий синус 100Hz, R: высокий синус 800Hz — проверяем разделение
L = np.sin(2 * math.pi * 100 * t) * 0.25 + np.random.randn(frames) * 0.02
R = np.sin(2 * math.pi * 800 * t) * 0.25 + np.random.randn(frames) * 0.02
stereo = np.stack([L, R]).astype(np.float32)

pb = engine.make_playback_processor("pb", stereo)

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
    (vision, ["fresh"]),
    (limiter, ["vision"]),
]
engine.load_graph(connections)

_apply_vst_preset(
    tape, pro_q, pro_mb, reverb, cho, ste, fresh, spiff, sdrr, soothe,
    _VST_NEUTRAL_PRESET,
)

engine.render(duration)
out = engine.get_audio("limiter")

L_out, R_out = out[0], out[1]
mid = (L_out + R_out) * 0.5
side = (L_out - R_out) * 0.5
rms_L = float(np.sqrt(np.mean(L_out ** 2)))
rms_R = float(np.sqrt(np.mean(R_out ** 2)))
rms_mid = float(np.sqrt(np.mean(mid ** 2)))
rms_side = float(np.sqrt(np.mean(side ** 2)))
peak_L = float(np.max(np.abs(L_out)))
peak_R = float(np.max(np.abs(R_out)))

# Корреляция
corr = float(np.corrcoef(L_out, R_out)[0, 1])

# Stereo Width indicator (ratio side/mid)
width_ratio = rms_side / (rms_mid + 1e-12)

# Читаем VISION 4X waveform mode после рендера — что он показывает
vision_desc = vision.get_parameters_description()
vp = {e["index"]: e for e in vision_desc}
def _v(i): return vp.get(i, {}).get("currentValText", "?")

print("=" * 66)
print("  VISION 4X  ·  STEREO FIELD CHECK  (premium chain)")
print("=" * 66)
print()
print("  ВХОД (input test signal):")
print(f"    L: 100 Hz sine + noise   RMS={rms_L:.4f}  Peak={peak_L:.4f}")
print(f"    R: 800 Hz sine + noise   RMS={rms_R:.4f}  Peak={peak_R:.4f}")
print()
print("  ВЫХОД (after limiter):")
print(f"    Left  RMS  : {rms_L:.4f}  ({20*math.log10(rms_L+1e-12):.1f} dBFS)")
print(f"    Right RMS  : {rms_R:.4f}  ({20*math.log10(rms_R+1e-12):.1f} dBFS)")
print(f"    Mid  RMS   : {rms_mid:.4f}  ({20*math.log10(rms_mid+1e-12):.1f} dBFS)")
print(f"    Side RMS   : {rms_side:.4f}  ({20*math.log10(rms_side+1e-12):.1f} dBFS)")
print()
print(f"    Корреляция L-R  : {corr:+.4f}  (0=моно, ±1=полное разделение)")
print(f"    Stereo Width    : {width_ratio:.3f}  (<0.5=narrow, ~1.0=normal, >1.5=wide)")
print()
print("  VISION 4X (post-reverb, pre-limiter) reads:")
print(f"    Waveform Mode   : {_v(8)}")
print(f"    Spectrum Mode   : {_v(7)}")
print(f"    Bypass          : {_v(28)}")
print()
# Интерпретация
if corr > 0.8:
    interp = "NARROW / почти моно (corr > +0.8)"
elif corr > 0.3:
    interp = "BALANCED (corr +0.3…+0.8)"
elif corr > -0.3:
    interp = "WIDE (corr −0.3…+0.3)"
else:
    interp = "VERY WIDE / out-of-phase risk (corr < −0.3)"

if width_ratio > 1.2:
    w_interp = "ШИРОКОЕ стерео (side > mid)"
elif width_ratio > 0.7:
    w_interp = "НОРМАЛЬНОЕ стерео"
else:
    w_interp = "УЗКОЕ / моноцентричное"

print(f"  Интерпретация:")
print(f"    Корреляция → {interp}")
print(f"    Width ratio → {w_interp}")
print("=" * 66)

os.dup2(old, 2)
os.close(old)
os.close(devnull)
