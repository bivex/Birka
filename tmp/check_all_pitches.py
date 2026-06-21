import os
import numpy as np
import soundfile as sf
from pathlib import Path

samples_dir = Path("/Volumes/External/Code/VST2SFZ/General_MIDI_samples")
nominal_notes = [36, 60, 84, 108]  # C2, C4, C6, C8
note_names = ["C2", "C4", "C6", "C8"]

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

print("Scanning all sample files for pitch inconsistencies...")
inconsistencies = []
for i in range(128):
    pitches = []
    for idx, note in enumerate(nominal_notes):
        p = samples_dir / f"gm_{i:03d}_{note_names[idx]}.wav"
        if not p.exists():
            continue
        try:
            audio, sr = sf.read(p)
            freq = estimate_pitch(audio, sr)
            midi_val = freq_to_midi(freq)
            pitches.append((note_names[idx], note, midi_val))
        except Exception as e:
            print(f"Error reading {p.name}: {e}")
            
    if len(pitches) < 4:
        print(f"Warning: Instrument {i} has only {len(pitches)} samples.")
        continue
        
    shifts = []
    for name, nominal, actual in pitches:
        if actual is not None:
            shifts.append(actual - nominal)
        else:
            shifts.append(None)
            
    # Check if all non-None shifts are the same
    valid_shifts = [s for s in shifts if s is not None]
    if not valid_shifts:
        print(f"Instrument {i}: no valid pitches detected.")
        continue
        
    # Check if there is any variation in shifts
    unique_shifts = set(valid_shifts)
    if len(unique_shifts) > 1:
        inconsistencies.append((i, pitches, shifts))
        print(f"INCONSISTENT Instrument {i:03d} (shifts: {shifts})")
        for idx, (name, nominal, actual) in enumerate(pitches):
            print(f"  - {name}: nominal={nominal}, actual={actual}, shift={shifts[idx]}")

print(f"\nDone! Found {len(inconsistencies)} instruments with inconsistent pitches across notes.")
