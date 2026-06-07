#!/usr/bin/env python3
# awto-riden-test.py — master HARDWARE test + characterization runner for Riden PSUs.
#
# Chains named test STAGES. Safety model (same contract as output_test.py):
#   * Every stage runs on its OWN fresh RidenWorker connection (reconnect per stage).
#   * A SEPARATE second-stage connection drives the PSU to a safe state
#     (0.0 V / 0.0 A / output OFF) after every stage, at the very end, and from a
#     GLOBAL error handler on ANY failure — the supply is never left energized.
#
# Stages:
#   identify      read device identity (model / fw / serial)            [read-only]
#   status        read live PSU state                                   [read-only]
#   write         write-path checks: set V/I + output toggle, read-back [low power]
#   measure       measure the connected load's resistance (low-V probe) [<~6 W]
#   characterize  load-line sweep up to --power-limit, record V/I/P/R   [<= ceiling]
#
# Usage:
#   scripts/awto-riden-test.py --all
#   scripts/awto-riden-test.py --chain identify,status,measure,characterize
#   scripts/awto-riden-test.py --chain characterize --power-limit 60 --steps 12

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from riden_daemon import RidenWorker

# --- Device / safety limits -------------------------------------------------
PSU_MAX_V = 60.0          # RK6006 ceiling
PSU_MAX_I = 6.0
V_MARGIN = 58.0           # stay just under the rails
I_MARGIN = 5.5
SAFE_V = 0.0
SAFE_I = 0.0
PSU_TEMP_ABORT_C = 80     # abort a sweep if the PSU's own internal temp gets this hot
SETTLE_S = 0.30           # ~2x the ~143 ms RD/RK firmware scan cycle

STAGES = ["identify", "status", "write", "measure", "characterize"]


def _connect(args) -> RidenWorker:
    w = RidenWorker(port=args.port, baud=args.baud, address=args.address)
    w.open()
    return w


def safe_zero(args, reason: str = "") -> None:
    """SECOND STAGE: fresh connection -> 0 V / 0 A / output OFF. Never raises."""
    tag = f" ({reason})" if reason else ""
    try:
        w = _connect(args)
        try:
            w.set_voltage(SAFE_V)
            w.set_current(SAFE_I)
            w.set_output(False)
            time.sleep(SETTLE_S)
            s = w.status()
            print(f"  safe-state{tag}: v_set={s['v_set']} i_set={s['i_set']} "
                  f"output={'ON' if s['output'] else 'OFF'}", file=sys.stderr)
        finally:
            w.close()
    except Exception as exc:
        print(f"  WARNING: safe-state{tag} not applied: {exc}", file=sys.stderr)


# --- Stages -----------------------------------------------------------------
# Each stage: stage(args, ctx) -> dict. It opens its own worker, does its work,
# closes; the runner then zeros via a SEPARATE connection.

def stage_identify(args, ctx) -> dict:
    w = _connect(args)
    try:
        return w.firmware()
    finally:
        w.close()


def stage_status(args, ctx) -> dict:
    w = _connect(args)
    try:
        return w.status()
    finally:
        w.close()


def stage_write(args, ctx) -> dict:
    """Low-power write-path checks with read-back."""
    results = {}
    w = _connect(args)
    try:
        w.set_voltage(1.0)
        time.sleep(SETTLE_S)
        results["set_voltage_1.0"] = abs(float(w.status()["v_set"]) - 1.0) < 0.05
        w.set_current(0.5)
        time.sleep(SETTLE_S)
        results["set_current_0.5"] = abs(float(w.status()["i_set"]) - 0.5) < 0.05
        w.set_output(True)
        time.sleep(SETTLE_S)
        results["output_on"] = w.status()["output"] is True
        w.set_output(False)
        time.sleep(SETTLE_S)
        results["output_off"] = w.status()["output"] is False
    finally:
        w.close()
    results["passed"] = all(results.values())
    return results


def stage_measure(args, ctx) -> dict:
    """Measure the connected load's resistance with a low-voltage probe.

    Probes at a couple of low setpoints (current-limited) and computes R = V/I.
    Stores load_ohms in ctx for the characterize stage. Power stays < ~6 W.
    """
    points = []
    w = _connect(args)
    try:
        w.set_current(3.0)          # limit during probe
        w.set_output(True)
        for v in (1.5, 3.0):
            w.set_voltage(v)
            time.sleep(SETTLE_S * 2)  # let it settle before reading current
            s = w.status()
            i_out = float(s["i_out"])
            v_out = float(s["v_out"])
            r = (v_out / i_out) if i_out > 0.001 else None
            points.append({"v_out": v_out, "i_out": i_out, "p_out": float(s["p_out"]),
                           "r_ohms": round(r, 3) if r else None})
        w.set_output(False)
    finally:
        w.close()
    rs = [p["r_ohms"] for p in points if p["r_ohms"]]
    load_ohms = round(sum(rs) / len(rs), 3) if rs else None
    ctx["load_ohms"] = load_ohms
    return {"points": points, "load_ohms": load_ohms,
            "note": "no current drawn — is a load connected?" if not load_ohms else None}


def stage_characterize(args, ctx) -> dict:
    """Sweep the load line from 0 up to the power ceiling; record V/I/P/R."""
    r = ctx.get("load_ohms")
    if not r:
        raise RuntimeError("characterize needs a measured load_ohms — run 'measure' first")
    if not (0.3 <= r <= 1000):
        raise RuntimeError(f"measured load {r} ohms outside sane range; aborting")

    p_limit = float(args.power_limit)
    # Largest V that keeps V^2/R <= p_limit, also under the PSU rails / current.
    v_by_power = (p_limit * r) ** 0.5
    v_by_current = I_MARGIN * r
    v_max = min(v_by_power, v_by_current, V_MARGIN)
    i_max = v_max / r
    i_limit = min(i_max * 1.2, I_MARGIN + 0.3)   # keep supply in CV across the sweep

    print(f"  load={r}ohm  ceiling={p_limit}W  -> V_max={v_max:.2f}V "
          f"I_max={i_max:.3f}A  ({v_max * i_max:.1f}W)", file=sys.stderr)

    steps = max(2, int(args.steps))
    dwell = float(args.dwell)
    sweep = []
    w = _connect(args)
    try:
        w.set_voltage(0.0)
        w.set_current(round(i_limit, 3))
        w.set_output(True)
        for k in range(1, steps + 1):
            v_set = round(v_max * k / steps, 3)
            w.set_voltage(v_set)
            time.sleep(dwell)
            s = w.status()
            v_out, i_out = float(s["v_out"]), float(s["i_out"])
            p_out = float(s["p_out"])
            temp = s.get("temp_c")
            r_calc = round(v_out / i_out, 3) if i_out > 0.001 else None
            sweep.append({"v_set": v_set, "v_out": v_out, "i_out": i_out,
                          "p_out": p_out, "r_ohms": r_calc, "temp_c": temp})
            print(f"    {v_set:5.2f}V -> {v_out:5.2f}V {i_out:5.3f}A "
                  f"{p_out:5.2f}W  R={r_calc}  T={temp}C", file=sys.stderr)
            if temp is not None and temp >= PSU_TEMP_ABORT_C:
                print("    PSU temp abort!", file=sys.stderr)
                break
        w.set_voltage(0.0)
        w.set_output(False)
    finally:
        w.close()

    p_peak = max((p["p_out"] for p in sweep), default=0.0)
    r_vals = [p["r_ohms"] for p in sweep if p["r_ohms"]]
    return {
        "load_ohms_measured": r,
        "power_ceiling_w": p_limit,
        "v_max": round(v_max, 3),
        "i_limit": round(i_limit, 3),
        "steps": len(sweep),
        "peak_power_w": round(p_peak, 2),
        "r_ohms_mean_under_load": round(sum(r_vals) / len(r_vals), 3) if r_vals else None,
        "sweep": sweep,
    }


STAGE_FUNCS = {
    "identify": stage_identify,
    "status": stage_status,
    "write": stage_write,
    "measure": stage_measure,
    "characterize": stage_characterize,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Riden master hardware test + characterization runner")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--address", type=int, default=1)
    p.add_argument("--all", action="store_true", help="run every stage in order")
    p.add_argument("--chain", default="",
                   help=f"comma-separated stages to run, in order (of: {','.join(STAGES)})")
    p.add_argument("--power-limit", type=float, default=60.0,
                   help="characterize power ceiling in watts (default 60 = 120%% of a 50 W load)")
    p.add_argument("--steps", type=int, default=12, help="characterize sweep steps (default 12)")
    p.add_argument("--dwell", type=float, default=0.4,
                   help="seconds to dwell at each characterize step (default 0.4)")
    p.add_argument("--out", default=None, help="write the full JSON summary to this path")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.all:
        chain = list(STAGES)
    else:
        chain = [s.strip() for s in args.chain.split(",") if s.strip()]
    if not chain:
        print("nothing to run: pass --all or --chain <stages>", file=sys.stderr)
        return 2
    unknown = [s for s in chain if s not in STAGE_FUNCS]
    if unknown:
        print(f"unknown stage(s): {unknown}; valid: {STAGES}", file=sys.stderr)
        return 2

    ctx: dict = {}
    report: dict = {"chain": chain, "stages": {}}
    try:
        for name in chain:
            print(f"[stage] {name}", file=sys.stderr)
            report["stages"][name] = STAGE_FUNCS[name](args, ctx)
            safe_zero(args, reason=f"after {name}")   # separate-connection teardown
    except BaseException as exc:
        print(f"GLOBAL ERROR ({type(exc).__name__}): {exc}", file=sys.stderr)
        safe_zero(args, reason="global error handler")
        report["error"] = f"{type(exc).__name__}: {exc}"
        _emit(report, args)
        return 1
    finally:
        safe_zero(args, reason="final")

    _emit(report, args)
    return 0


def _emit(report: dict, args) -> None:
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"WROTE {args.out}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
