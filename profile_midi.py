#!/usr/bin/env python3
import os
import sys
import time
from pathlib import Path
import numpy as np
import mido

# Add src/ to Python path to import Birka modules
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pysfizz import _sfizz
from birka.infrastructure.midi_renderer import _find_sfz, _write_float_wav

_SFIZZ_SYNTH_CACHE = {}

def render_with_quality(midi_path, sfz_path, out_path, quality, sample_rate=44100, polyphony=256):
    mid = mido.MidiFile(str(midi_path))
    events = []
    abs_time = 0.0
    for msg in mid:
        abs_time += msg.time
        if msg.type in ("note_on", "note_off", "control_change", "pitchwheel"):
            events.append((abs_time, msg))

    total_seconds = max(1.0, mid.length + 2.0)
    frames_needed = int(total_seconds * sample_rate)

    block_frames = 1024
    
    cache_key = (str(sfz_path), sample_rate, polyphony, quality)
    if cache_key in _SFIZZ_SYNTH_CACHE:
        synth = _SFIZZ_SYNTH_CACHE[cache_key]
        synth.all_sound_off()
    else:
        synth = _sfizz.Synth(sample_rate, block_frames)
        synth.enable_freewheeling()
        synth.set_num_voices(max(1, min(polyphony, 512)))
        synth.set_sample_quality(quality)
        if not synth.load_sfz_file(str(sfz_path)):
            raise RuntimeError("Failed to load SFZ file")
        _SFIZZ_SYNTH_CACHE[cache_key] = synth

    interleaved_blocks = []
    event_index = 0
    n_events = len(events)
    rendered = 0

    while rendered < frames_needed:
        block_start = rendered
        block_end = rendered + block_frames
        while event_index < n_events:
            msg_time, msg = events[event_index]
            event_frame = int(msg_time * sample_rate)
            if event_frame >= block_end:
                break
            delay = max(0, min(block_frames, event_frame - block_start))
            if msg.type == "note_on" and msg.velocity > 0:
                synth.note_on(delay, msg.note, msg.velocity)
            elif msg.type in ("note_off", "note_on"):
                synth.note_off(delay, msg.note, 0)
            elif msg.type == "control_change":
                synth.cc(delay, msg.control, msg.value)
            elif msg.type == "pitchwheel":
                synth.pitch_wheel(delay, msg.pitch)
            event_index += 1

        left, right = synth.render_block()
        left_arr = np.asarray(left, dtype=np.float32)
        right_arr = np.asarray(right, dtype=np.float32)
        block = np.column_stack((left_arr, right_arr)).flatten()
        interleaved_blocks.append(block)
        rendered += len(left_arr)

    buf = np.concatenate(interleaved_blocks)[:frames_needed * 2].tolist()
    _write_float_wav(buf, out_path, sample_rate)

def main():
    midi_path = Path("/Volumes/External/Code/Melodica/output/welcome_to_home/02_Open_Door.mid")
    sfz_path = _find_sfz()
    
    if not sfz_path:
        print("ERROR: SFZ bank not found.")
        sys.exit(1)

    print("=== Sfizz Quality Benchmark ===")
    print(f"MIDI File: {midi_path}")
    print(f"SFZ Bank: {sfz_path}")

    # Benchmark Quality 2 (kInterpolatorHermite3 - Cold Load)
    print("\n[1/3] Rendering with Quality 2 (Hermite3) - COLD LOAD (loads bank from disk)...")
    out_2_cold = Path("tmp/rendered_q2_cold.wav")
    start = time.perf_counter()
    render_with_quality(midi_path, sfz_path, out_2_cold, quality=2)
    time_2_cold = time.perf_counter() - start
    print(f"  Finished in {time_2_cold:.3f} seconds.")

    # Benchmark Quality 2 (kInterpolatorHermite3 - Warm Reuse)
    print("\n[2/3] Rendering with Quality 2 (Hermite3) - WARM REUSE (reuses loaded bank)...")
    out_2_warm = Path("tmp/rendered_q2_warm.wav")
    start = time.perf_counter()
    render_with_quality(midi_path, sfz_path, out_2_warm, quality=2)
    time_2_warm = time.perf_counter() - start
    print(f"  Finished in {time_2_warm:.3f} seconds.")
    print(f"  Speedup: {time_2_cold / time_2_warm:.2f}x faster (warm)")

    # Benchmark Quality 1 (kInterpolatorLinear - Cold Load)
    print("\n[3/3] Rendering with Quality 1 (Linear) - COLD LOAD (loads bank from disk because quality changed)...")
    out_1 = Path("tmp/rendered_q1.wav")
    start = time.perf_counter()
    render_with_quality(midi_path, sfz_path, out_1, quality=1)
    time_1 = time.perf_counter() - start
    print(f"  Finished in {time_1:.3f} seconds.")

if __name__ == "__main__":
    main()
