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
    synth = _sfizz.Synth(sample_rate, block_frames)
    synth.enable_freewheeling()
    synth.set_num_voices(max(1, min(polyphony, 512)))
    
    # Set the sample quality
    synth.set_sample_quality(quality)
    
    if not synth.load_sfz_file(str(sfz_path)):
        raise RuntimeError("Failed to load SFZ file")

    interleaved = []
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
        for i in range(len(left)):
            interleaved.append(float(left[i]))
            interleaved.append(float(right[i]))
        rendered = len(interleaved) // 2

    samples_needed = frames_needed * 2
    buf = interleaved[:samples_needed]
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

    # Benchmark Quality 10 (Sinc72)
    print("\n[1/3] Rendering with Quality 10 (kInterpolatorSinc72 - Default Freewheeling)...")
    out_10 = Path("tmp/rendered_q10.wav")
    start = time.perf_counter()
    render_with_quality(midi_path, sfz_path, out_10, quality=10)
    time_10 = time.perf_counter() - start
    print(f"  Finished in {time_10:.3f} seconds.")

    # Benchmark Quality 2 (kInterpolatorHermite3 - Default Live)
    print("\n[2/3] Rendering with Quality 2 (kInterpolatorHermite3)...")
    out_2 = Path("tmp/rendered_q2.wav")
    start = time.perf_counter()
    render_with_quality(midi_path, sfz_path, out_2, quality=2)
    time_2 = time.perf_counter() - start
    print(f"  Finished in {time_2:.3f} seconds.")
    print(f"  Speedup vs Q10: {time_10 / time_2:.2f}x faster")

    # Benchmark Quality 1 (kInterpolatorLinear)
    print("\n[3/3] Rendering with Quality 1 (kInterpolatorLinear)...")
    out_1 = Path("tmp/rendered_q1.wav")
    start = time.perf_counter()
    render_with_quality(midi_path, sfz_path, out_1, quality=1)
    time_1 = time.perf_counter() - start
    print(f"  Finished in {time_1:.3f} seconds.")
    print(f"  Speedup vs Q10: {time_10 / time_1:.2f}x faster")

if __name__ == "__main__":
    main()
