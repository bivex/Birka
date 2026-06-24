"""Снимок аудио на каждом стыке премиум-цепочки.
Рендерит сигнал последовательно через каждый блок и снимает
RMS, peak, корреляцию, mid/side после каждого плагина.
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
    _df_size, _df_decay, _df_predelay, _df_lowcut, _df_highcut,
    _freq_to_val, _gain_to_val, _q_to_val,
)

VISION_PATH = "/Library/Audio/Plug-Ins/VST3/VISION 4X.vst3"


def _make_engine():
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

    # VISION — stereo waveform, all-round spectrum
    vision.set_parameter(7, 0.333)
    vision.set_parameter(8, 0.666)   # Stereo
    vision.set_parameter(9, 0.0)
    vision.set_parameter(14, 0.5)
    vision.set_parameter(28, 0.0)
    vision.set_parameter(11, 0.0)
    vision.set_parameter(19, 0.5)

    return {
        "engine": engine,
        "nodes": dict(
            tape=tape, sdrr=sdrr, spiff=spiff, soothe=soothe,
            pro_q=pro_q, pro_mb=pro_mb, kot=kot,
            fresh=fresh, cho=cho, ste=ste, reverb=reverb,
            vision=vision, limiter=limiter,
        ),
    }


def _analyze(buf: np.ndarray) -> dict:
    """Считать метрики из 2×N буфера."""
    L, R = buf[0], buf[1]
    mono = np.mean(buf, axis=0)
    mid = (L + R) * 0.5
    side = (L - R) * 0.5

    def _rms(x):
        return float(np.sqrt(np.mean(x ** 2)))

    def _peak(x):
        return float(np.max(np.abs(x)))

    rms_L, rms_R = _rms(L), _rms(R)
    rms_mid, rms_side = _rms(mid), _rms(side)
    peak = max(_peak(L), _peak(R))
    corr = float(np.corrcoef(L, R)[0, 1])
    width = rms_side / (rms_mid + 1e-12)

    return dict(
        rms_L=rms_L, rms_R=rms_R,
        rms_mid=rms_mid, rms_side=rms_side,
        peak=peak, corr=corr, width=width,
    )


def _db(v):
    return 20 * math.log10(v + 1e-12)


def _db_table(m):
    return f"{_db(m['rms_L']):.1f}/{_db(m['rms_R']):.1f}"


def _corr_label(c):
    if c > 0.8:   return "NARROW"
    if c > 0.3:   return "BALANCED"
    if c > -0.3:  return "WIDE"
    return "VERY WIDE"


def _width_label(w):
    if w > 1.2:   return "WIDE"
    if w > 0.7:   return "NORMAL"
    return "NARROW"


def _render_stage(engine, input_buf, nodes, upto_name, duration):
    """Собрать граф от pb → tape → ... → nodes[upto_name], отрендерить,
    вернуть output буфер узла upto_name."""
    n = engine.make_playback_processor("pb", input_buf.astype(np.float32))

    chain = [
        "tape", "spiff", "soothe", "pro_q", "pro_mb",
        "kot", "sdrr", "reverb", "fresh", "vision", "limiter",
    ]
    # обрезаем до нужного узла включительно
    idx = chain.index(upto_name) + 1
    used = chain[:idx]

    graph = [(n, [])]
    prev = "pb"
    for name in used:
        graph.append((nodes[name], [prev]))
        prev = name

    engine.load_graph(graph)
    _apply_vst_preset(
        nodes["tape"], nodes["pro_q"], nodes["pro_mb"],
        nodes["reverb"], nodes["cho"], nodes["ste"],
        nodes["fresh"], nodes["spiff"], nodes["sdrr"], nodes["soothe"],
        _VST_NEUTRAL_PRESET,
    )
    engine.render(duration)
    return engine.get_audio(upto_name)


def main():
    # Тестовый сигнал — L 100Hz, R 800Hz + noise
    duration = 4.0
    frames = int(_VST_SAMPLE_RATE * duration)
    t = np.arange(frames) / _VST_SAMPLE_RATE
    L = np.sin(2 * math.pi * 100 * t) * 0.22 + np.random.randn(frames) * 0.015
    R = np.sin(2 * math.pi * 800 * t) * 0.22 + np.random.randn(frames) * 0.015
    input_buf = np.stack([L, R]).astype(np.float32)

    stages = [
        "tape", "spiff", "soothe", "pro_q", "pro_mb",
        "kot", "sdrr", "reverb", "fresh", "vision", "limiter",
    ]

    results = {}
    state = _make_engine()
    engine, nodes = state["engine"], state["nodes"]

    for stage in stages:
        buf = _render_stage(engine, input_buf, nodes, stage, duration)
        results[stage] = _analyze(buf)
        # выход этого этапа — вход следующего
        input_buf = buf

    # Наконец печатаем таблицу
    hdr = (
        f"{'stage':<12}  {'L/R dBFS':<14}  {'Mid/Side':<14}  "
        f"{'Peak':>8}  {'Corr':>8}  {'Width':>7}  label"
    )
    sep = "─" * 100
    print("=" * 100)
    print("  VISION 4X  ·  PREMIUM CHAIN  — per-stage stereo field")
    print("=" * 100)
    print()
    print(f"  Input  L=100Hz  R=800Hz  +noise")
    print()
    print(f"  {hdr}")
    print(f"  {sep}")
    for stage in stages:
        m = results[stage]
        lr = f"{_db(m['rms_L']):.1f}/{_db(m['rms_R']):.1f}"
        ms = f"{_db(m['rms_mid']):.1f}/{_db(m['rms_side']):.1f}"
        print(
            f"  {stage:<12}  {lr:<14}  {ms:<14}  "
            f"{_db(m['peak']):>7.1f}  {m['corr']:>+8.3f}  "
            f"{m['width']:>7.3f}  {_corr_label(m['corr'])}"
        )
    print(f"  {sep}")
    print()
    print("  SNAP ANALYSIS (prev→next delta):")
    prev = None
    all_stages = ["input"] + stages
    all_bufs = [None] + [results[s] for s in stages]
    for i, name in enumerate(all_stages):
        if prev is not None:
            a, b = all_bufs[i - 1], all_bufs[i]
            delta_corr = b["corr"] - a["corr"]
            delta_width = b["width"] - a["width"]
            arrow_corr = "→"
            if abs(delta_corr) > 0.05:
                arrow_corr = "▲ broadening" if delta_corr < 0 else "▲ narrowing"
            arrow_w = ""
            if abs(delta_width) > 0.05:
                arrow_w = "(+side)" if delta_width > 0 else "(-side)"
            print(
                f"    {prev} → {name:<10}  "
                f"corr {a['corr']:+.3f}→{b['corr']:+.3f} {arrow_corr:<20}  "
                f"width {a['width']:.3f}→{b['width']:.3f} {arrow_w}"
            )
        prev = name
    print("=" * 100)

    os.dup2(old, 2)
    os.close(old)
    os.close(devnull)


if __name__ == "__main__":
    main()
