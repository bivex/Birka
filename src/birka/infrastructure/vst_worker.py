"""Standalone VST mastering worker — runs in a subprocess without Qt.

Called by _render_sfizz_vst_chain via subprocess.run. Reads a float32 WAV
from stdin (or a temp file), applies the DAWdreamer VST chain, writes the
mastered WAV to output_path, and exits 0 on success / non-zero on failure.

CLI:
    python vst_worker.py <input_wav> <output_wav> <sample_rate> <mode> <fast_master>

All heavy imports (dawdreamer, numpy, scipy) happen here, isolated from the
Qt process so CoreAudio / LV2 URI conflicts don't occur.
"""
import sys
import os
import struct
import math

def main():
    if len(sys.argv) < 6:
        print("usage: vst_worker.py <input_wav> <output_wav> <sample_rate> <mode> <plugin_paths_json>", file=sys.stderr)
        sys.exit(1)

    input_wav   = sys.argv[1]
    output_wav  = sys.argv[2]
    sample_rate = int(sys.argv[3])
    mode        = sys.argv[4]  # "digital", "analog_clean", etc. or "full"
    import json
    plugin_paths = json.loads(sys.argv[5])
    target_lufs  = float(sys.argv[6]) if len(sys.argv) > 6 else -13.8

    try:
        import numpy as np
        import dawdreamer as daw
    except ImportError as e:
        print(f"import error: {e}", file=sys.stderr)
        sys.exit(2)

    # --- Read input WAV ---
    with open(input_wav, "rb") as f:
        data = f.read()
    idx = data.find(b"data")
    if idx < 0:
        print("no data chunk", file=sys.stderr)
        sys.exit(3)
    chunk_size = struct.unpack_from("<I", data, idx + 4)[0]
    raw = data[idx + 8 : idx + 8 + chunk_size]
    fmt_code = struct.unpack_from("<H", data, 20)[0]
    sr_in    = struct.unpack_from("<I", data, 24)[0]
    if fmt_code == 3:
        audio = np.frombuffer(raw, dtype=np.float32).copy()
    else:
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    # --- Resample to VST SR if needed ---
    VST_SR = 96000
    if sr_in != VST_SR:
        try:
            from math import gcd
            from scipy.signal import resample_poly
            stereo = audio.reshape(-1, 2).T
            g  = gcd(VST_SR, sr_in)
            up = VST_SR // g
            dn = sr_in  // g
            L = resample_poly(stereo[0], up, dn).astype(np.float32)
            R = resample_poly(stereo[1], up, dn).astype(np.float32)
            audio = np.stack([L, R]).T.flatten().astype(np.float32)
        except Exception as e:
            print(f"resample error: {e}", file=sys.stderr)
            sys.exit(4)

    audio_2d = audio.reshape(-1, 2).T.astype(np.float32)  # (2, frames)
    duration = audio_2d.shape[1] / VST_SR
    BUF = 512

    # --- Fast chain node/plugin maps (mirrors _FAST_MASTER_CHAINS / _FAST_NODE_PLUGIN in midi_renderer) ---
    FAST_CHAINS = {
        "digital":       ["pro_q", "kot", "limiter"],
        "analog_clean":  ["tape", "kot", "pro_q", "limiter"],
        "analog_warm":   ["tape", "sdrr", "kot", "limiter"],
        "analog_ultra":  ["tape", "limiter"],
        "analog_thick":  ["tape", "sdrr_tube", "pro_q", "limiter"],
        "polished":      ["tape", "sdrr", "soothe", "limiter"],
        "modern_loud":   ["tape", "pro_mb", "sdrr", "limiter"],
        "airy":          ["tape", "fresh", "kot", "limiter"],
        "punch":         ["tape", "spiff", "kot", "limiter"],
        "reel":          ["tape_track", "tape_mix", "limiter"],
        "reference":     ["pro_q_balance", "kot", "sdrr_tube", "pro_q_tone", "limiter"],
        "sonic_scoop":   ["pro_mb_sonic", "pro_q_cut", "kot", "sdrr_tube", "pro_q_widen", "limiter"],
        "transparent":   ["pro_q_hpf", "soothe", "kot_trans", "pro_q_trans_wide", "limiter"],
        "cinematic":     ["pro_q_balance", "tape", "kot", "sdrr_tube", "pro_q_film", "fresh", "limiter"],
        "lo_fi":         ["tape_lofi", "sdrr", "pro_q_lofi", "limiter"],
        "vintage_radio": ["pro_q_radio", "pro_mb", "sdrr", "limiter_radio"],
    }
    NODE_PLUGIN = {
        "tape":             "chow",
        "tape_track":       "chow",
        "tape_mix":         "chow",
        "tape_lofi":        "chow",
        "sdrr":             "sdrr",
        "sdrr_tube":        "sdrr",
        "spiff":            "spiff",
        "soothe":           "soothe",
        "pro_q":            "pro_q",
        "pro_q_balance":    "pro_q",
        "pro_q_tone":       "pro_q",
        "pro_q_cut":        "pro_q",
        "pro_q_widen":      "pro_q",
        "pro_q_hpf":        "pro_q",
        "pro_q_trans_wide": "pro_q",
        "pro_q_film":       "pro_q",
        "pro_q_lofi":       "pro_q",
        "pro_q_radio":      "pro_q",
        "pro_mb":           "pro_mb",
        "pro_mb_sonic":     "pro_mb",
        "kot":              "kot",
        "kot_trans":        "kot",
        "fresh":            "fresh",
        "cho":              "cho",
        "ste":              "ste",
        "reverb":           "reverb",
        "limiter":          "limiter",
        "limiter_radio":    "limiter",
    }

    chain = FAST_CHAINS.get(mode, FAST_CHAINS["digital"])
    print(f"[vst] mode={mode}  chain={' → '.join(chain)}", flush=True)

    # Redirect stderr to suppress LV2/LADSPA URI noise
    devnull = open(os.devnull, "w")
    old_stderr = os.dup(2)
    os.dup2(devnull.fileno(), 2)

    try:
        engine = daw.RenderEngine(VST_SR, BUF)
        dummy = np.zeros((2, BUF), dtype=np.float32)
        pb = engine.make_playback_processor("pb", dummy)
        procs = {"pb": pb}
        connections = [(pb, [])]
        prev = "pb"
        loaded = []
        for name in chain:
            plugin_key = NODE_PLUGIN[name]
            if plugin_key not in plugin_paths:
                os.dup2(old_stderr, 2)
                print(f"plugin not found: {plugin_key}", file=sys.stderr)
                sys.exit(5)
            proc = engine.make_plugin_processor(name, plugin_paths[plugin_key])
            procs[name] = proc
            connections.append((proc, [prev]))
            prev = name
            loaded.append(f"{name}({plugin_key})")
        engine.load_graph(connections)
        os.dup2(old_stderr, 2)
        print(f"[vst] loaded: {' → '.join(loaded)}", flush=True)
        os.dup2(devnull.fileno(), 2)

        # Configure nodes (minimal — just limiter ceiling)
        lim = procs["limiter"]
        try:
            lim.set_parameter(1, 0.0)
            lim.set_parameter(0, 0.0)
        except Exception:
            pass

        out_node = chain[-1]
        pb.set_data(audio_2d)

        # Pass 1
        engine.render(duration)
        out = engine.get_audio(out_node)

        # Measure LUFS (simple RMS approximation if pyloudnorm unavailable)
        def measure_lufs(arr, sr):
            try:
                import pyloudnorm as pyln
                meter = pyln.Meter(sr)
                stereo = arr.T if arr.shape[0] == 2 else arr
                return meter.integrated_loudness(stereo.T)
            except Exception:
                rms = float(np.sqrt(np.mean(arr ** 2)))
                return 20 * math.log10(max(rms, 1e-9)) - 0.7

        current_lufs = measure_lufs(out, VST_SR)
        os.dup2(old_stderr, 2)
        print(f"[vst] pass1: LUFS={current_lufs:.1f}  target={target_lufs:.1f}  diff={target_lufs-current_lufs:+.1f} dB", flush=True)
        os.dup2(devnull.fileno(), 2)

        # Iterative normalize
        cumulative = 1.0
        for _pass in range(4):
            gain_db = target_lufs - current_lufs
            if abs(gain_db) <= 0.5:
                break
            step = max(gain_db, -8.0) if gain_db < -0.5 else min(gain_db, 24.0)
            cumulative *= 10.0 ** (step / 20.0)
            pb.set_data((audio_2d * cumulative).astype(np.float32))
            engine.render(duration)
            out = engine.get_audio(out_node)
            current_lufs = measure_lufs(out, VST_SR)
            cum_db = 20.0 * math.log10(max(cumulative, 1e-9))
            os.dup2(old_stderr, 2)
            print(f"[vst] pass{_pass+2}: step={step:+.1f} dB  cumulative={cum_db:+.1f} dB  LUFS={current_lufs:.1f}", flush=True)
            os.dup2(devnull.fileno(), 2)

        os.dup2(old_stderr, 2)

        # Write output
        interleaved = out.T.flatten().astype(np.float32)
        raw_out = interleaved.tobytes()
        channels = 2
        byte_rate = VST_SR * channels * 4
        with open(output_wav, "wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + len(raw_out)))
            f.write(b"WAVE")
            f.write(b"fmt ")
            f.write(struct.pack("<I", 16))
            f.write(struct.pack("<H", 3))       # IEEE_FLOAT
            f.write(struct.pack("<H", channels))
            f.write(struct.pack("<I", VST_SR))
            f.write(struct.pack("<I", byte_rate))
            f.write(struct.pack("<H", channels * 4))
            f.write(struct.pack("<H", 32))
            f.write(b"data")
            f.write(struct.pack("<I", len(raw_out)))
            f.write(raw_out)

        print(f"OK LUFS={current_lufs:.1f}", flush=True)
        sys.exit(0)

    except Exception as e:
        try:
            os.dup2(old_stderr, 2)
        except Exception:
            pass
        print(f"worker error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(10)
    finally:
        try:
            os.close(old_stderr)
        except Exception:
            pass
        devnull.close()


if __name__ == "__main__":
    main()
