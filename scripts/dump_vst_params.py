#!/usr/bin/env python3
"""Dump all parameters of a VST3 plugin via DAWdreamer.

Usage:
    python scripts/dump_vst_params.py "Pro-MB"
    python scripts/dump_vst_params.py "/Library/Audio/Plug-Ins/VST3/FabFilter Pro-MB.vst3"

Outputs one row per parameter with: index | name | text value | range | default.
Prints as a formatted table and also dumps a JSON copy next to this script.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import dawdreamer as daw

VST_DIR = Path("/Library/Audio/Plug-Ins/VST3")

# Friendly alias -> actual bundle path
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
    # fuzzy match against bundle names
    cands = [b for b in VST_DIR.iterdir() if key in b.stem.lower()]
    if len(cands) == 1:
        return cands[0]
    if cands:
        print(f"Multiple matches for {arg!r}:")
        for c in cands:
            print(f"  {c.name}")
        sys.exit(1)
    raise FileNotFoundError(f"No VST3 matching {arg!r} in {VST_DIR}")


def dump_plugin(bundle: Path) -> list[dict]:
    engine = daw.RenderEngine(96000, 512)
    proc = engine.make_plugin_processor("p", str(bundle))

    rows: list[dict] = []
    n = proc.get_plugin_parameter_size()
    for i in range(n):
        name = ""
        text = ""
        rng = None
        default = None
        try:
            name = proc.get_parameter_name(i)
        except Exception:
            pass
        try:
            text = proc.get_parameter_text(i)
        except Exception:
            pass
        try:
            rng = list(proc.get_parameter_range(i))
        except Exception:
            pass
        try:
            default = proc.get_parameter(i)
        except Exception:
            pass
        rows.append(
            {
                "index": i,
                "name": name,
                "default_norm": default,
                "text": text,
                "range": rng,
            }
        )
    return rows


def print_table(rows: list[dict], plugin_name: str) -> None:
    # column widths
    idx_w = max(len(str(r["index"])) for r in rows) if rows else 3
    name_w = min(
        44, max((len(r["name"] or "") for r in rows), default=4) if rows else 4
    )
    print(f"\n{'='*100}")
    print(f"  {plugin_name}   ({len(rows)} parameters)")
    print(f"{'='*100}")
    header = f"{'idx':>{idx_w}}  {'name':<{name_w}}  {'def':>7}  {'text':<22}  range"
    print(header)
    print("-" * len(header))
    for r in rows:
        name = (r["name"] or "")[:name_w]
        text = (r["text"] or "")[:22]
        defv = f"{r['default_norm']:.4f}" if isinstance(r["default_norm"], float) else str(
            r["default_norm"]
        )
        rng = (
            f"[{r['range'][0]:.3f}, {r['range'][1]:.3f}]"
            if r["range"]
            else ""
        )
        print(f"{r['index']:>{idx_w}}  {name:<{name_w}}  {defv:>7}  {text:<22}  {rng}")


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
    # suppress chatty plugin stderr
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

    out_json = Path(__file__).parent / f"dump_{bundle.stem.replace(' ', '_').lower()}.json"
    out_json.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\nJSON written to: {out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
