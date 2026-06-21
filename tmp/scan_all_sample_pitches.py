import os
import sys
import numpy as np
import soundfile as sf
from pathlib import Path

# Add VST2SFZ dir
sys.path.insert(0, "/Volumes/External/Code/VST2SFZ")

samples_dir = Path("/Volumes/External/Code/VST2SFZ/General_MIDI_samples")

def estimate_pitch(audio, sr):
    mono = np.mean(audio, axis=1) if audio.ndim > 1 else audio
    # Take a portion in the middle of the sample where it is stable
    start = int(0.2 * sr)
    end = int(0.8 * sr)
    if start >= len(mono) or end > len(mono):
        return 0.0
    signal = mono[start:end]
    if len(signal) < 100:
        return 0.0
    
    # Autocorrelation
    corr = np.correlate(signal, signal, mode='full')
    corr = corr[len(corr)//2:]
    
    # Find peaks
    d = np.diff(corr)
    start_search = np.where(d > 0)[0]
    if len(start_search) == 0:
        return 0.0
    start_idx = start_search[0]
    
    peak_idx = np.argmax(corr[start_idx:]) + start_idx
    if peak_idx == 0:
        return 0.0
    freq = sr / peak_idx
    return freq

def freq_to_midi(freq):
    if freq < 10.0:
        return None
    return int(round(12 * np.log2(freq / 440.0) + 69))

# Nominals
nominal_notes = [36, 60, 84, 108]  # C2, C4, C6, C8

shifts = {}

print("Scanning sample pitches...")
for i in range(128):
    instrument_shifts = []
    for idx, note in enumerate(nominal_notes):
        note_name = ['C2', 'C4', 'C6', 'C8'][idx]
        p = samples_dir / f"gm_{i:03d}_{note_name}.wav"
        if not p.exists():
            continue
        try:
            audio, sr = sf.read(p)
            freq = estimate_pitch(audio, sr)
            midi_val = freq_to_midi(freq)
            if midi_val is not None:
                # Diff between actual pitch and nominal note
                diff = midi_val - note
                # We round diff to nearest octave (multiple of 12) if it's close to prevent minor detection errors
                nearest_octave = int(round(diff / 12.0)) * 12
                # If it's within 2 semitones of nearest octave, count it
                if abs(diff - nearest_octave) <= 2:
                    instrument_shifts.append(nearest_octave)
        except Exception:
            pass
            
    # Find the most common shift for this instrument
    if instrument_shifts:
        # Get the mode
        best_shift = max(set(instrument_shifts), key=instrument_shifts.count)
        if best_shift != 0:
            shifts[i] = best_shift

print(f"\nScan complete! Found {len(shifts)} instruments with octave shifts:")
for prog, shift in sorted(shifts.items()):
    octaves = shift // 12
    sign = "+" if octaves > 0 else ""
    print(f" - Program {prog:3d} (shift: {sign}{octaves} octaves / {sign}{shift} semitones)")

# Output a Python dict structure to easily integrate or map
print("\nShift mapping dict:")
print(shifts)
