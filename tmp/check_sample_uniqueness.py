import sys
import numpy as np
import soundfile as sf
from pathlib import Path

samples_dir = Path("/Volumes/External/Code/VST2SFZ/General_MIDI_samples")

# Pick C4 samples for a few different instruments
files_to_check = {
    0: "Acoustic Grand Piano",
    24: "Acoustic Guitar (nylon)",
    48: "String Ensemble 1",
    71: "Clarinet"
}

loaded_data = {}
for prog, name in files_to_check.items():
    p = samples_dir / f"gm_{prog:03d}_C4.wav"
    if p.exists():
        data, sr = sf.read(p)
        loaded_data[prog] = (data, name)
        print(f"Loaded gm_{prog:03d}_C4.wav ({name}): shape={data.shape}, peak={np.max(np.abs(data)):.4f}")
    else:
        print(f"File not found: {p}")

print("\nComparing audio data between instruments:")
progs = list(loaded_data.keys())
for i in range(len(progs)):
    for j in range(i + 1, len(progs)):
        p1, p2 = progs[i], progs[j]
        d1, name1 = loaded_data[p1]
        d2, name2 = loaded_data[p2]
        
        # Check if they have the same length and are identical
        if d1.shape == d2.shape:
            diff = np.max(np.abs(d1 - d2))
            is_same = diff < 1e-4
            print(f" - {name1} vs {name2}: Max difference = {diff:.6f} -> Same sound: {is_same}")
        else:
            print(f" - {name1} vs {name2}: Different lengths ({d1.shape} vs {d2.shape}) -> Same sound: False")
