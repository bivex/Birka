import re
import glob
from pathlib import Path

# Shifts scanned from Surge XT presets
shifts = {
    0: 12, 3: -12, 6: -12, 10: 12, 18: -12, 20: -24, 21: 12, 22: 12, 24: -12, 30: -12,
    31: -48, 32: -24, 33: -12, 34: -12, 35: -12, 36: -12, 37: -12, 38: -24, 39: -12,
    42: -12, 43: -24, 44: -12, 45: -12, 46: -12, 47: -12, 51: -24, 55: -48, 56: -12,
    59: -12, 60: -12, 61: -12, 67: -12, 70: 12, 75: -12, 76: -12, 77: -12, 78: -12,
    80: -12, 82: -12, 83: -12, 84: -12, 85: -12, 86: -24, 87: -24, 88: -12, 89: -36,
    90: -12, 94: -24, 95: -24, 96: 24, 98: 24, 99: -24, 100: -36, 101: -84, 103: 48,
    104: -24, 107: -12, 109: -12, 114: -12, 115: -12, 116: -12, 117: -12, 119: -12,
    120: 24, 121: 24, 122: -48, 123: -24, 125: -24, 127: 24
}

vst_dir = Path("/Volumes/External/Code/VST2SFZ")

# List of SFZ files to process
sfz_files = [
    vst_dir / "General_MIDI.sfz",
    vst_dir / "General_MIDI_sfizz.sfz",
    vst_dir / "General_MIDI_sfizz_processed.sfz"
] + [Path(p) for p in glob.glob(str(vst_dir / "General_MIDI_instruments" / "*.sfz"))]

def fix_line(line):
    # Regex to find sample and pitch_keycenter
    # Example: <region> sample=gm_000_C2.wav pitch_keycenter=36 lokey=0 hikey=48
    if "<region>" not in line or "pitch_keycenter=" not in line:
        return line
        
    # Find program index in sample filename, e.g. gm_000_
    m_prog = re.search(r"gm_(\d{3})_", line)
    if not m_prog:
        return line
    prog = int(m_prog.group(1))
    shift = shifts.get(prog, 0)
    if shift == 0:
        return line
        
    # Find pitch_keycenter= value
    m_pitch = re.search(r"pitch_keycenter=(\d+)", line)
    if not m_pitch:
        return line
    old_pitch = int(m_pitch.group(1))
    new_pitch = old_pitch + shift
    
    # Replace pitch_keycenter=XX with pitch_keycenter=YY
    new_line = line.replace(f"pitch_keycenter={old_pitch}", f"pitch_keycenter={new_pitch}")
    return new_line

print("Applying pitch corrections to SFZ files...")
updated_count = 0
for sfz_file in sfz_files:
    if not sfz_file.exists():
        continue
    try:
        lines = sfz_file.read_text().splitlines()
        new_lines = []
        for line in lines:
            new_lines.append(fix_line(line))
        sfz_file.write_text("\n".join(new_lines) + "\n")
        updated_count += 1
    except Exception as e:
        print(f"Error processing {sfz_file.name}: {e}")

print(f"Successfully updated {updated_count} SFZ files with corrected pitch_keycenters!")
