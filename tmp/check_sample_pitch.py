import sys
import numpy as np
import soundfile as sf
from pathlib import Path

samples_dir = Path("/Volumes/External/Code/VST2SFZ/General_MIDI_samples")

def estimate_pitch(audio, sr):
    # Use autocorrelation to find the fundamental frequency
    # We take the mono mix down
    mono = np.mean(audio, axis=1) if audio.ndim > 1 else audio
    # Take a portion in the middle of the sample where it is stable
    start = int(0.2 * sr)
    end = int(0.8 * sr)
    signal = mono[start:end]
    
    # Autocorrelation
    corr = np.correlate(signal, signal, mode='full')
    corr = corr[len(corr)//2:]
    
    # Find peaks
    # We want to ignore the initial peak at lag 0
    d = np.diff(corr)
    start_search = np.where(d > 0)[0]
    if len(start_search) == 0:
        return 0.0
    start_idx = start_search[0]
    
    peak_idx = np.argmax(corr[start_idx:]) + start_idx
    freq = sr / peak_idx
    return freq

def freq_to_note_name(freq):
    if freq < 10.0:
        return "Unknown"
    # MIDI note = 12 * log2(freq / 440) + 69
    midi_note = int(round(12 * np.log2(freq / 440.0) + 69))
    notes = ['C', 'Cs', 'D', 'Ds', 'E', 'F', 'Fs', 'G', 'Gs', 'A', 'As', 'B']
    octave = (midi_note // 12) - 1
    note_name = notes[midi_note % 12]
    return f"{note_name}{octave} (MIDI {midi_note}, Freq {freq:.2f} Hz)"

files_to_check = {
    0: "Acoustic Grand Piano",
    24: "Acoustic Guitar (nylon)",
    48: "String Ensemble 1",
    71: "Clarinet"
}

for prog, name in files_to_check.items():
    p = samples_dir / f"gm_{prog:03d}_C4.wav"
    if p.exists():
        data, sr = sf.read(p)
        freq = estimate_pitch(data, sr)
        print(f"gm_{prog:03d}_C4.wav ({name}): Est. Pitch = {freq_to_note_name(freq)}")
    else:
        print(f"File not found: {p}")
