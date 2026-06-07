#!/usr/bin/env python3
# awto-riden-dev.py — bench/analysis + docs dev tool for the Riden project.
#
# Consolidates the loose analysis/plotting/BLE/doc helpers behind one entry point
# (the "awto standard"). Nothing here drives the PSU's output — for hardware
# tests/characterization use awto-riden-test.py.
#
# Everything is DETERMINISTIC CODE — no LLM/token cost. The register map has a
# single in-code source of truth (register_map.py); docs that are generated from
# code or captured data are (re)built by `gen-docs`, never hand-edited.
#
# Subcommands:
#   gen-docs [--registers-only]            regenerate generated docs: register map
#                                          (from register_map.py) + waveform figures
#   analyze-scan <scan.json> [--map CFG]   classify a scan vs the map: known /
#                                          unimplemented (0xFFFF) / real-value RE targets
#   export-map [--out FILE]                dump the in-code register map -> JSON config
#   plot {waveforms,serial,ble} [args...]  regenerate documentation figures
#   ble  {profile,globe}        [args...]  BLE transport experiments
#   report                      [args...]  (re)build docs/reports + index
#
# Usage:
#   scripts/awto-riden-dev.py gen-docs
#   scripts/awto-riden-dev.py analyze-scan tmp/regscan-rk6006.json

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, REPO_ROOT)

UNIMPLEMENTED = 0xFFFF  # registers that read all-ones are treated as not-present

PLOT_SCRIPTS = {
    "waveforms": "plot_waveforms.py",
    "serial": "plot_serial_comparison.py",
    "ble": "plot_ble_globe.py",
}
BLE_SCRIPTS = {
    "profile": "ble_profile.py",
    "globe": "ble_globe_turnon.py",
}
REPORT_SCRIPT = "report_pages.py"


def _delegate(script: str, passthrough: list[str], optional: bool = False) -> int:
    path = os.path.join(SCRIPTS_DIR, script)
    if not os.path.exists(path):
        msg = f"underlying script not found: {path}"
        print(("(skip) " if optional else "") + msg, file=sys.stderr)
        return 0 if optional else 1
    return subprocess.call([sys.executable, path, *passthrough], cwd=REPO_ROOT)


# --- register map (single in-code source = register_map.py) -----------------

def _reg_names_from_code() -> dict[int, str]:
    import register_map
    return {addr: info["name"] for addr, info in register_map.registers().items()}


def _reg_names_from_config(path: str) -> dict[int, str]:
    if path.endswith(".toml"):
        import tomllib
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    else:
        with open(path) as fh:
            raw = json.load(fh)
    return {int(k): v for k, v in raw.get("registers", {}).items()}


def export_map(out_path: str | None) -> int:
    import register_map
    regs = {str(a): i["name"] for a, i in register_map.registers().items()}
    payload = {"registers": regs, "magic": register_map.magic(),
               "_generated_from": "register_map.py (single source)"}
    text = json.dumps(payload, indent=2)
    if out_path:
        with open(out_path, "w") as fh:
            fh.write(text + "\n")
        print(f"WROTE {out_path}  ({len(regs)} registers)", file=sys.stderr)
    else:
        print(text)
    return 0


def gen_docs(registers_only: bool) -> int:
    """Regenerate everything that is generated from code or captured data."""
    import register_map
    out = os.path.join(REPO_ROOT, "docs", "registers.md")
    with open(out, "w") as fh:
        fh.write(register_map.render_markdown() + "\n")
    print(f"WROTE docs/registers.md  ({len(register_map.registers())} registers)", file=sys.stderr)
    if registers_only:
        return 0
    # Waveform figures: regenerate PNGs from existing JSONL (best-effort; needs no PSU).
    print("regenerating waveform figures (best-effort)...", file=sys.stderr)
    _delegate("regen_waveforms.py", [], optional=True)
    return 0


# --- scan analysis ----------------------------------------------------------

def analyze_scan(path: str, as_json: bool, map_path: str | None) -> int:
    """Classify a register-scan JSON against the in-code map (or a --map config)."""
    try:
        data = json.load(open(path))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read scan JSON {path}: {exc}", file=sys.stderr)
        return 1

    reg_names = _reg_names_from_config(map_path) if map_path else _reg_names_from_code()
    src = map_path or "register_map.py (in-code single source)"

    def known(r) -> bool:
        return r["addr"] in reg_names

    regs = data.get("registers", [])
    known_live = [r for r in regs if known(r)]
    unimpl = [r for r in regs if not known(r) and r.get("value") == UNIMPLEMENTED]
    targets = [r for r in regs if not known(r) and r.get("value") not in (0, UNIMPLEMENTED)]

    summary = {
        "source": path,
        "map": src,
        "scanned": len(regs),
        "known_live": len(known_live),
        "unimplemented_0xffff": len(unimpl),
        "unimplemented_addrs": sorted(r["addr"] for r in unimpl),
        "re_targets": [
            {"addr": r["addr"], "name": reg_names.get(r["addr"], "unknown"),
             "hex": r["hex"], "value": r["value"]} for r in targets
        ],
        "re_target_count": len(targets),
    }

    if as_json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"scan: {path}")
    print(f"  map source              : {summary['map']}")
    print(f"  scanned registers       : {summary['scanned']}")
    print(f"  known/mapped (live)      : {summary['known_live']}")
    print(f"  unimplemented (0xFFFF)   : {summary['unimplemented_0xffff']}  "
          f"-> {summary['unimplemented_addrs']}")
    print(f"  UNKNOWN with real values : {summary['re_target_count']}  (RE targets)")
    for t in summary["re_targets"]:
        print(f"      addr {t['addr']:>3}  {t['name']:<14} {t['hex']}  = {t['value']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Riden bench/analysis + docs dev tool (no PSU output)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="SUBCOMMAND")

    sp = sub.add_parser("gen-docs", help="regenerate generated docs (register map + figures)")
    sp.add_argument("--registers-only", action="store_true", help="only regenerate docs/registers.md")

    sp = sub.add_parser("analyze-scan", help="classify a register-scan JSON vs the map")
    sp.add_argument("scan_json", help="path to a register-scan --save-json file")
    sp.add_argument("--map", default=None, help="register-map config (.json/.toml); default = register_map.py")
    sp.add_argument("--json", action="store_true", help="emit the classification as JSON")

    sp = sub.add_parser("export-map", help="dump the in-code register map to a JSON config")
    sp.add_argument("--out", default=None, help="output path; stdout if omitted")

    sp = sub.add_parser("plot", help="regenerate documentation figures")
    sp.add_argument("which", choices=sorted(PLOT_SCRIPTS), help="figure set")

    sp = sub.add_parser("ble", help="BLE transport experiments")
    sp.add_argument("which", choices=sorted(BLE_SCRIPTS), help="BLE tool")

    sub.add_parser("report", help="(re)build docs/reports + index")

    args, passthrough = p.parse_known_args()

    if args.cmd == "gen-docs":
        return gen_docs(args.registers_only)
    if args.cmd == "analyze-scan":
        return analyze_scan(args.scan_json, args.json, args.map)
    if args.cmd == "export-map":
        return export_map(args.out)
    if args.cmd == "plot":
        return _delegate(PLOT_SCRIPTS[args.which], passthrough)
    if args.cmd == "ble":
        return _delegate(BLE_SCRIPTS[args.which], passthrough)
    if args.cmd == "report":
        return _delegate(REPORT_SCRIPT, passthrough)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
