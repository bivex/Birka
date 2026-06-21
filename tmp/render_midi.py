import os
import sys
from pathlib import Path

# Add src/ to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Set environment variables for the renderer
os.environ["BIRKA_BACKEND"] = "sfizz"
os.environ["BIRKA_SFZ"] = "/Volumes/External/Code/VST2SFZ/General_MIDI_sfizz_processed.sfz"

from birka.infrastructure.midi_renderer import render_midi_to_wav

midi_path = Path("/Volumes/External/Code/Melodica/output/album_six_worlds/04_Flamenco_de_la_Luna.mid")
out_path = Path("/Volumes/External/Code/Melodica/output/album_six_worlds/04_Flamenco_de_la_Luna_processed.wav")

print("Rendering...")
success = render_midi_to_wav(midi_path, out_path, bit_depth=32)
if success:
    print(f"Success! Rendered to {out_path}")
else:
    print("Failed to render.")
