#!/usr/bin/env python3
"""Dump all parameters of a VST3 plugin via DAWdreamer.

Usage:
    python scripts/dump_vst_params.py "Pro-MB"
    python scripts/dump_vst_params.py "/Library/Audio/Plug-Ins/VST3/FabFilter Pro-MB.vst3"

Outputs one row per parameter with: index | name | default | text | min..max.
Prints a formatted table and writes a JSON copy next to this script.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import dawdreamer as daw

VST_DIR = Path("/Library/Audio/Plug-Ins/VST3")

ALIASES = {
    "pro-mb": "FabFilter Pro-MB.vst3",
    "promb": "FabFilter Pro-MB.vst3",
    "pro-q": "FabFilter Pro-Q 4.vst3",
    "proq": "FabFilter Pro-Q 4.vst3",
    "sdrr": "SDRR2.vst3",
    "sdrr2": "SDRR2.vst3",
    "tal": "TAL-Chorus-LX.vst3",
    "chorus": "TAL-Chorus-LX.vst3",
    "chow": "CHOWTapeModel.vst3",
    "tape": "CHOWTapeModel.vst3",
    "spiff": "spiff.vst3",
    "soothe": "soothe2.vst3",
    "kot": "TDR Kotelnikov GE.vst3",
    "kotelnikov": "TDR Kotelnikov GE.vst3",
    "fresh": "Fresh Air.vst3",
    "pro-r": "FabFilter Pro-R 2.vst3",
    "pror": "FabFilter Pro-R 2.vst3",
    "pro-l": "FabFilter Pro-L 2.vst3",
    "prol": "FabFilter Pro-L 2.vst3",
    "ste": "A1StereoControl.vst3",
}


def resolve_path(arg: str) -> Path:
    p = Path(arg)
    if p.exists():
        return p
    key = arg.lower().strip()
    if key in ALIASES:
        return VST_DIR / ALIASES[key]
    cands = [b for b in VST_DIR.iterdir() if key in b.stem.lower()]
    if len(cands) == 1:
        return cands[0]
    if cands:
        print(f"Multiple matches for {arg!r}:")
        for c in cands:
            print(f"  {c.name}")
        sys.exit(1)
    raise FileNotFoundError(f"No VST3 matching {arg!r} in {VST_DIR}")


def _norm_range(proc, i: int) -> tuple[float, float] | None:
    """Return normalized (min,max) if discoverable, else None."""
    try:
        r = proc.get_parameter_range(i)
    except Exception:
        return None
    # observed shape: dict keyed by (lo,hi) tuples -> pick first interval
    if isinstance(r, dict) and r:
        key = next(iter(r))
        if isinstance(key, tuple) and len(key) == 2:
            return (float(key[0]), float(key[1]))
    return None


def dump_plugin(bundle: Path) -> list[dict]:
    engine = daw.RenderEngine(96000, 512)
    proc = engine.make_plugin_processor("p", str(bundle))

    desc = proc.get_parameters_description()
    rows: list[dict] = []
    for d in desc:
        i = d["index"]
        rng = _norm_range(proc, i)
        rows.append(
            {
                "index": i,
                "name": d.get("name", ""),
                "text": d.get("text", ""),
                "currentValText": d.get("currentValText", ""),
                "defaultValue": d.get("defaultValue"),
                "defaultValueText": d.get("defaultValueText", ""),
                "min": d.get("min"),
                "max": d.get("max"),
                "isDiscrete": d.get("isDiscrete", False),
                "isBoolean": d.get("isBoolean", False),
                "numSteps": d.get("numSteps"),
                "category": d.get("category", ""),
                "norm_range": rng,
            }
        )
    rows.sort(key=lambda r: r["index"])
    return rows


def _s(v) -> str:
    return "" if v is None else str(v)


def print_table(rows: list[dict], plugin_name: str) -> None:
    idx_w = max(len(str(r["index"])) for r in rows) if rows else 3
    name_w = min(40, max((len(_s(r["name"])) for r in rows), default=4) if rows else 4)
    print(f"\n{'='*108}")
    print(f"  {plugin_name}   ({len(rows)} parameters)")
    print(f"{'='*108}")
    header = f"{'idx':>{idx_w}}  {'name':<{name_w}}  {'def':>7}  {'text/current':<24}  {'min..max':<20}  flags"
    print(header)
    print("-" * len(header))
    for r in rows:
        name = _s(r["name"])[:name_w]
        defv = (
            f"{r['defaultValue']:.4f}"
            if isinstance(r["defaultValue"], float)
            else _s(r["defaultValue"])
        )
        text = (_s(r["currentValText"]) or _s(r["text"]))[:24]
        mn = _s(r["min"])
        mx = _s(r["max"])
        mm = f"{mn}..{mx}"[:20]
        flags = []
        if r.get("isDiscrete"):
            flags.append("DISC")
        if r.get("isBoolean"):
            flags.append("BOOL")
        print(
            f"{r['index']:>{idx_w}}  {name:<{name_w}}  {defv:>7}  {text:<24}  {mm:<20}  {' '.join(flags)}"
        )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        print("\nAliases:", ", ".join(sorted(ALIASES)))
        return 2
    bundle = resolve_path(argv[1])
    if not bundle.exists():
        print(f"Not found: {bundle}", file=sys.stderr)
        return 1

    print(f"Loading: {bundle.name} ...", file=sys.stderr)
    devnull = os.open(os.devnull, os.O_WRONLY)
    old = os.dup(2)
    try:
        os.dup2(devnull, 2)
        rows = dump_plugin(bundle)
    finally:
        os.dup2(old, 2)
        os.close(devnull)
        os.close(old)

    if not rows:
        print("No parameters enumerated.", file=sys.stderr)
        return 1

    print_table(rows, bundle.stem)

    out_json = (
        Path(__file__).parent
        / f"dump_{bundle.stem.replace(' ', '_').lower()}.json"
    )
    out_json.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\nJSON written to: {out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
