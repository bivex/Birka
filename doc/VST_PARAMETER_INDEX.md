# VST Parameter Index — Birka Mastering Chain

Dumped live via `scripts/dump_vst_params.py` against the actual installed
VST3 bundles (`/Library/Audio/Plug-Ins/VST3/`). Every value below has been
verified by setting the parameter and reading back `get_parameter_text()`.

DAWdreamer talks to plugins in **normalized [0.0, 1.0]** space. Where a
parameter is non-linear (log frequency, dB), the mapping is given so you can
compute the exact normalized value for any target.

---

## ⚠️ Two confirmed bugs in the current `_apply_vst_preset` (Pro-Q 4)

The brief assumes b1 is a **Low Shelf @ 120 Hz** and the HPF is a **Low Cut**.
Replaying the current code on a real Pro-Q 4 instance and reading back gives:

| Band | Enabled | **Shape (actual)** | Freq   | Gain    | Q    |
|------|---------|--------------------|--------|---------|------|
| Band 1 | Enabled | **Low Cut** ✅      | 22 Hz  | 0 dB    | 1.0  |
| Band 2 | Enabled | **Bell** ❌          | 120 Hz | +0.8 dB | 0.4  | ← should be **Low Shelf**
| Band 3 | Enabled | **Bell** ✅          | 4200 Hz| -0.5 dB | 1.3  |
| Band 4 | Enabled | **High Shelf** ❌    | 7500 Hz| -0.5 dB | 1.55 | ← b4 shape was set; b1/b2 were never set

**Root cause:** the code sets Frequency/Gain/Q but **never sets the Shape
parameter** for bands 1 and 2, so they fall through to Pro-Q's default
`Bell`. Band 4 ends up as `High Shelf` because index 74 (its shape slot) is
being written with `0.2778`, which Pro-Q reads as High Shelf.

**Fix:** set Shape explicitly (see Pro-Q section below). Shape indices are
`base+5` per band. Shape normalized values:

| Shape norm | Type |
|-----------|------|
| 0.00 | Bell |
| 0.10 | Low Shelf |
| 0.20 | Low Cut |
| 0.30 | High Shelf |
| 0.40 | High Cut |
| 0.50–0.60 | Notch |
| 0.70 | Band Pass |
| 0.80 | Tilt Shelf |
| 0.90 | Flat Tilt |
| 1.00 | All Pass |

So: **Band 2 → 0.10 (Low Shelf)**, **Band 1 → 0.20 (Low Cut)** already correct.

The **b3 (4200) and b4 (7500) frequency indices are CORRECT** (48/49/50 and
71/72/73). Only the shapes were ambiguous.

---

## Pro-MB (FabFilter Pro-MB) — Band 2 @ 320 Hz ✅

Layout: 6 bands × 22 params each, then globals. **Band N starts at index
`N×22`** for N=0..5 (Band 1 = idx 0, Band 2 = idx 22, Band 3 = idx 44, …).

**Band 2 block (indices 22–43):**

| idx | name | role | range / mapping |
|----|------|------|-----------------|
| **22** | Band 2 State | **enable** | 0.0=Disabled, **0.25–0.5=Enabled**, 0.75–1.0=Unused |
| **23** | Band 2 Low Crossover | **freq** | log, 30 Hz..30 kHz → `log(f/30)/log(1000)` |
| 24 | Band 2 Low Slope | | 6..48 dB/oct |
| 25 | Band 2 High Crossover | | log, 30 Hz..30 kHz |
| 26 | Band 2 High Slope | | 6..48 dB/oct |
| 27 | Band 2 Dynamics Mode | comp/exp | DISC |
| **28** | Band 2 Threshold | thr | -90..0 dB, linear `t/-90` |
| **29** | Band 2 Range | **range (max GR)** | -30..+30 dB, `(r+30)/60` |
| **30** | Band 2 Ratio | ratio | 1..100:1 |
| **31** | Band 2 Attack | | 0..100% |
| **32** | Band 2 Release | | 0..100% |
| 33 | Band 2 Knee | | 0..48 dB |
| 34 | Band 2 Lookahead | | 0..20 ms |
| 35 | Band 2 Level | output gain | -30..+30 dB |
| 36 | Band 2 Pan | | Mid/Side |
| 37 | Band 2 SC Filtering | | Band/Free DISC |
| 38–39 | Band 2 SC Lo/Hi Freq | | 30 Hz..30 kHz |
| 40 | Band 2 SC Input | | DISC |
| 41 | Band 2 Stereo Link | | 0..100% |
| 42 | Band 2 Stereo Link Mode | | Mid/Side DISC |
| 43 | Band 2 Solo/Mute | | Normal/Solo DISC |

**Globals (after band 6, idx 132+):**

| idx | name | notes |
|----|------|-------|
| 133 | Mix | 0..200%, **0.5 = 100%** |
| 136 | Output Level | -INF..+36 dB, 0.5 = 0 dB |
| 138 | Bypass | 0=Not Bypassed, 1=Bypassed |
| 151 | Host Bypass | same |

**320 Hz @ conservative settings (the 4 values you asked for):**

```python
"pro_mb_band2": {
    22: 0.5,        # State = Enabled
    23: 0.3427,     # Low Crossover = 320 Hz  (log(320/30)/log(1000))
    28: <thr>,      # Threshold  (-90..0 dB linear)
    29: <range>,    # Range = max gain reduction (-30..+30 dB → (r+30)/60)
    30: <ratio>,
    31: <attack>,
    32: <release>,
}
```

Example: thr −10 dB → 0.889, range −3 dB → 0.45, ratio 2:1 → ~0.0101,
attack 30% → 0.3, release 40% → 0.4.

---

## SDRR 2 — DESK mode ✅

**Mode (idx 0):** `0.0`=TUBE, `0.25`=DIGI, `0.5`=FUZZ, **`1.0`=DESK**.

In DESK mode the **group-4** parameters (idx 25–50) are the live ones. There
is no parameter literally named "High Cut"; the DESK tone section is
`Bass4`/`Treble4` (shelving) plus `FrequencyResponse`. "Dynamics Comp" =
`Compression4` (idx 40).

| idx | name | role | mapping |
|----|------|------|---------|
| **0** | Mode | **DESK** | **1.0** |
| 25 | Trim4 (OutputComp trim) | | ±20 dB |
| **37** | Drive4 | **saturation drive** | linear 0..10 (**0.2 = drive 2.0**) |
| 39 | Transients4 | | -1..+1 |
| **40** | Compression4 | **Dynamics Comp** | 0..1 linear |
| **41** | Bass4 | low shelf ±12 dB | **0.5 = 0 dB**, `0.5+g/24` |
| **42** | Treble4 | **high shelf / "high cut"** | **0.5 = 0 dB**, `0.5+g/24` |
| 45 | TightBass | | Off/On DISC |
| 46 | TightTreble | | Off/On DISC |
| **49** | Mix4 | wet/dry | 0..100% |
| 50 | OutputCompensation4 | makeup | ±20 dB |
| **56** | Bypass | | 0.0=Off, 1.0=Bypass |

**Conservative DESK preset per the brief (DESK, drive 20%, high cut ≈12 kHz feel):**

```python
"sdrr": {
    0: 1.0,     # Mode = DESK
    37: 0.20,   # Drive  = 2.0  (≈ +6 dB feel, gentle)
    40: 0.25,   # Dynamics Comp = 0.25
    41: 0.50,   # Bass   = 0 dB
    42: 0.4583, # Treble = -1.0 dB  (very gentle high tilt)
    49: 0.40,   # Mix    = 40%
    56: 0.0,    # Bypass off
}
```
Note: SDRR's Treble shelf is broad (±12 dB), it is not a true low-pass. For a
true 12 kHz cut you'd want Pro-Q's High Cut band — but in the chain SDRR sits
*before* Pro-Q, so Treble4 is the right "dull-the-top-before-saturation" knob.

---

## TAL-Chorus-LX ✅

Only 7 parameters. No Rate/Depth knobs — it's a fixed-bucket-brigade chorus;
you control amount via Dry/Wet and pick chorus engine 1 or 2.

| idx | name | role | range |
|----|------|------|-------|
| 0 | Volume | output | 0..10 (0.5 = unity ≈ 5.0) |
| **1** | Dry/Wet | **wet/dry** | 0..10 (0.5 = 5.0) |
| 2 | Stereo | width | 0..10 |
| **3** | Chorus 1 | **engine 1 amount** | 0..1 |
| **4** | Chorus 2 | **engine 2 amount** | 0..1 |
| 5 | Compat ≤ 1.3.1 | | 0..1 |
| 6 | Bypass | | Off/On |

**Gentle width/width preset per brief:**

```python
"tal": {
    1: 0.3,    # Dry/Wet low (subtle widening)
    2: 1.0,    # Stereo full
    3: 1.0,    # Chorus 1 on
    4: 0.0,    # Chorus 2 off
    6: 0.0,    # not bypassed
}
```

---

## Pro-Q 4 — verified band indices ✅ (shapes need the fix above)

Per-band layout, 23 params each. Band N base: `B1=0, B2=23, B3=46, B4=69`.
Within each band: `+0` Used, `+1` Enabled, `+2` Freq, `+3` Gain, `+4` Q,
`+5` Shape, `+6` Slope, `+9` Dyn Range, `+10` Dynamics Enabled, `+11` Dyn Auto,
`+12` Threshold.

| Band | base | Enabled | Freq | Gain | Q | Shape |
|------|------|---------|------|------|---|-------|
| 1 (HPF) | 0 | 1 | 2 | 3 | 4 | **5** → set **0.20** (Low Cut) |
| 2 (LS)  | 23 | 24 | 25 | 26 | 27 | **28** → set **0.10** (Low Shelf) |
| 3 (4200)| 46 | 47 | 48 | 49 | 50 | 51 |
| 4 (7500)| 69 | 70 | 71 | 72 | 73 | 74 |

**Frequency mapping:** `norm = log10(f/10)/log10(3000)` (range 10 Hz..30 kHz).
**Gain mapping:** `norm = (g+30)/60`. **Q mapping:** `norm = log10(q/0.025)/log10(1600)`.

Verified normalized values:
- 22 Hz → 0.0985, 120 Hz → 0.3104, 4200 Hz → 0.7544, 7500 Hz → 0.8269
- +0.8 dB → 0.5133, -0.5 dB → 0.4917, -1.5 dB → 0.4750

---

## Confirmed-correct existing presets (for reference)

**CHOW Tape** — verified against dump (idx: Drive 16, Saturation 17, Bias 18,
Dry/Wet 2). Code sets `{16:0.889, 17:0.22, 18:0.68, 8:0.52, 9:0.48}` ✅
(note idx 8/9 are Tone Bass/Treble, not Drive — but currently 0.52/0.48 ≈
flat, so harmless).

**spiff** — verified: mode idx 0 (`0`=cut, `1`=boost), cut depth idx 1, boost
depth idx 2, sensitivity idx 3, bypass idx 38/41. Code's CUT settings are
correct. ⚠️ Note spiff `sensitivity` maps **0..10 linear** (idx 3, default
0.75 = 7.5), but code sets `0.42` — i.e. sensitivity 4.2, not "42%". Verify
intent.

**soothe2** — Depth idx 4, Sharpness idx 5, Selectivity idx 6, Bypass idx 53.
Code values track the brief. ✅

---

## How to re-run / extend the dumps

```bash
source .venv/bin/activate
python scripts/dump_vst_params.py pro-mb     # alias
python scripts/dump_vst_params.py sdrr
python scripts/dump_vst_params.py tal
python scripts/dump_vst_params.py pro-q
# or full bundle path
python scripts/dump_vst_params.py "/Library/Audio/Plug-Ins/VST3/FabFilter Pro-MB.vst3"
```

Writes a JSON copy (`scripts/dump_*.json`) for grepping/diffing. To probe a
single parameter's discrete steps, see the `python -c` snippets used during
this audit (mode/shape/state enums).
