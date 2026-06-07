#!/usr/bin/env python3
"""Regenerate all waveform PNGs from existing JSONL files and verify the result.

Usage
-----
    python3 scripts/awto_riden_regen.py [--docs-dir docs]

Exits 0 if every expected PNG was created, 1 otherwise.

No PSU connection is needed — this works entirely from pre-captured JSONL data.
To re-capture from hardware first, run::

    python3 scripts/awto_riden_waveform_capture.py --port /dev/ttyUSB0 [capture args...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from awto_riden_plot_waveforms import load, main as _combo_main, plot_jsonl, _descriptive_png_name, _detect_plot_type

# ---------------------------------------------------------------------------
# Expected JSONL inputs (relative to docs_dir)
# ---------------------------------------------------------------------------
PERIOD_JSONL = [
    "mr11_sine_period.jsonl",
    "mr11_triangle_period.jsonl",
    "mr11_sawtooth_period.jsonl",
    "mr11_square_period.jsonl",
]

CLIP_JSONL = [
    "mr11_sine_clipped_current_limit.jsonl",
]

CC_DEMO_JSONL = [
    "mr11_current_limit_demo_sine_0_12v_i200ma.jsonl",
    "mr11_current_limit_demo_fixed_12v_i300ma.jsonl",
]

# The three canonical combo charts (always produced by main())
CANONICAL_PNGS = [
    "mr11_waveform_tracking.png",
    "mr11_waveform_clipping.png",
    "mr11_waveform_cc_demo.png",
]


def _check(path: Path, label: str) -> bool:
    ok = path.exists() and path.stat().st_size > 0
    status = "OK  " if ok else "FAIL"
    print(f"  [{status}] {path.name}  ({label})")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs-dir", default="docs/data", metavar="DIR",
                    help="directory containing JSONL files (default: docs)")
    ap.add_argument("--skip-combo", action="store_true",
                    help="skip regenerating the three canonical combo charts")
    args = ap.parse_args()

    docs = Path(args.docs_dir)
    if not docs.is_dir():
        print(f"ERROR: docs dir not found: {docs}", file=sys.stderr)
        return 1

    all_jsonl = PERIOD_JSONL + CLIP_JSONL + CC_DEMO_JSONL
    missing = [f for f in all_jsonl if not (docs / f).exists()]
    if missing:
        print("ERROR: missing JSONL input files (run waveform_capture.py first):")
        for m in missing:
            print(f"  {docs / m}")
        return 1

    failures = 0

    # ------------------------------------------------------------------
    # 1. Canonical combo charts
    # ------------------------------------------------------------------
    if not args.skip_combo:
        print("\n=== Combo charts ===")
        import os
        orig = os.getcwd()
        os.chdir(REPO_ROOT)
        try:
            ret = _combo_main()
        finally:
            os.chdir(orig)
        if ret != 0:
            print("  [FAIL] combo main() returned non-zero")
            failures += 1
        for name in CANONICAL_PNGS:
            if not _check(docs / name, "combo"):
                failures += 1

    # ------------------------------------------------------------------
    # 2. Per-JSONL auto-plots with descriptive names
    # ------------------------------------------------------------------
    print("\n=== Per-JSONL auto-plots ===")
    per_jsonl_pngs: list[Path] = []
    for fname in all_jsonl:
        jsonl_path = docs / fname
        rows = load(jsonl_path)
        if not rows:
            print(f"  [WARN] {fname} is empty — skipping")
            continue
        kind = _detect_plot_type(rows)
        out = plot_jsonl(jsonl_path)
        per_jsonl_pngs.append(out)
        if not _check(out, kind):
            failures += 1

    # ------------------------------------------------------------------
    # 3. Summary
    # ------------------------------------------------------------------
    total = len(CANONICAL_PNGS) + len(per_jsonl_pngs)
    passed = total - failures
    print(f"\n{'='*40}")
    print(f"Result: {passed}/{total} PNGs OK{'  ✓' if failures == 0 else '  ✗ FAILURES'}")
    if failures == 0:
        print("All waveform artifacts successfully regenerated.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
