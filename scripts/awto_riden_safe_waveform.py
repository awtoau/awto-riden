#!/usr/bin/env python3
"""Safety-first waveform runner for Riden PSUs.

This script performs a mandatory load sanity-check before running a waveform.
By default it expects about a 10 ohm load and aborts if the measured resistance
is out of tolerance.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

# Allow running as: python3 scripts/awto_riden_safe_waveform.py from any cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from riden_daemon import RidenWorker


def _warn_banner() -> None:
    print("=" * 78)
    print("DANGER: LIVE POWER OUTPUT TEST")
    print("This will ENABLE PSU OUTPUT and drive an automated waveform.")
    print("Verify DUT/load wiring before proceeding.")
    print("Recommended: isolated resistor load, no sensitive electronics connected.")
    print("=" * 78)


def _confirm_or_exit(assume_yes: bool) -> None:
    if assume_yes:
        return
    print("Type RUN TEST to continue:", end=" ", flush=True)
    entered = input().strip()
    if entered != "RUN TEST":
        print("Aborted: confirmation text did not match.")
        sys.exit(2)


def _safe_output_off(worker: RidenWorker) -> None:
    try:
        worker.set_output(False)
    except Exception:
        pass


def _safe_set_voltage(worker: RidenWorker, volts: float) -> None:
    try:
        worker.set_voltage(volts)
    except Exception:
        pass


def _try_beep(worker: RidenWorker, enable: bool) -> None:
    try:
        res = worker.beep(enable)
        if not res.get("supported", False):
            print("Note: buzzer control is not supported on this model/firmware.")
    except Exception as exc:
        print(f"Note: beep request failed: {exc}")


def _measure_load_ohms(
    worker: RidenWorker,
    probe_current_a: float,
    max_probe_v: float,
    step_v: float,
    dwell_s: float,
    min_meas_current_a: float,
) -> dict[str, Any]:
    samples: list[float] = []

    worker.set_output(False)
    worker.set_current(probe_current_a)
    worker.set_voltage(0.0)
    worker.set_output(True)

    v = 0.0
    while v <= max_probe_v + 1e-9:
        worker.set_voltage(v)
        time.sleep(dwell_s)
        st = worker.status()
        v_out = float(st.get("v_out", 0.0))
        i_out = float(st.get("i_out", 0.0))
        if i_out >= min_meas_current_a:
            r = v_out / i_out
            if r > 0:
                samples.append(r)
        v += step_v

    worker.set_output(False)
    worker.set_voltage(0.0)

    if not samples:
        return {
            "ok": False,
            "reason": "No measurable current during probe",
            "samples": 0,
            "resistance_ohm": None,
        }

    median_r = statistics.median(samples)
    return {
        "ok": True,
        "samples": len(samples),
        "resistance_ohm": round(median_r, 3),
        "all_samples_ohm": [round(x, 3) for x in samples],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Run waveform only after load safety-check.")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--address", type=int, default=1)

    ap.add_argument("--shape", choices=["sine", "triangle", "sawtooth", "square"], default="sine")
    ap.add_argument("--v-center", type=float, default=6.0)
    ap.add_argument("--v-amplitude", type=float, default=6.0)
    ap.add_argument("--freq-hz", type=float, default=0.1)
    ap.add_argument("--duration-s", type=float, default=60.0)
    ap.add_argument("--step-s", type=float, default=0.5)
    ap.add_argument("--duty-cycle", type=float, default=0.5)
    ap.add_argument("--current-limit-a", type=float, default=1.0)

    ap.add_argument("--target-load-ohm", type=float, default=10.0)
    ap.add_argument("--load-tol-frac", type=float, default=0.25, help="fractional tolerance, e.g. 0.25 = +/-25%")
    ap.add_argument("--probe-current-a", type=float, default=0.2)
    ap.add_argument("--probe-max-v", type=float, default=3.0)
    ap.add_argument("--probe-step-v", type=float, default=0.1)
    ap.add_argument("--probe-dwell-s", type=float, default=0.25)
    ap.add_argument("--probe-min-i-a", type=float, default=0.05)

    ap.add_argument("--beep", action="store_true", help="Attempt buzzer toggle at key stages")
    ap.add_argument("--yes", action="store_true", help="Skip interactive confirmation prompt")
    ap.add_argument("--force", action="store_true", help="Run waveform even if load-check fails")

    args = ap.parse_args()

    _warn_banner()
    _confirm_or_exit(args.yes)

    worker = RidenWorker(port=args.port, baud=args.baud, address=args.address)
    worker.open()

    try:
        if args.beep:
            _try_beep(worker, True)

        print("Running load probe...")
        probe = _measure_load_ohms(
            worker=worker,
            probe_current_a=args.probe_current_a,
            max_probe_v=args.probe_max_v,
            step_v=args.probe_step_v,
            dwell_s=args.probe_dwell_s,
            min_meas_current_a=args.probe_min_i_a,
        )
        print(json.dumps({"load_probe": probe}, indent=2))

        load_ok = False
        if probe.get("ok") and probe.get("resistance_ohm") is not None:
            r = float(probe["resistance_ohm"])
            lo = args.target_load_ohm * (1.0 - args.load_tol_frac)
            hi = args.target_load_ohm * (1.0 + args.load_tol_frac)
            load_ok = lo <= r <= hi
            print(f"Load acceptance window: {lo:.2f} .. {hi:.2f} ohm")
            print(f"Measured resistance: {r:.3f} ohm")

        if not load_ok and not args.force:
            print("ABORT: load check failed. Use --force to override.")
            if args.beep:
                _try_beep(worker, False)
            sys.exit(3)

        if not load_ok and args.force:
            print("WARNING: continuing despite failed load check (--force).")

        worker.set_current(args.current_limit_a)

        print("Starting waveform run...")
        result = worker.waveform(
            shape=args.shape,
            v_center=args.v_center,
            v_amplitude=args.v_amplitude,
            freq_hz=args.freq_hz,
            duration_s=args.duration_s,
            step_s=args.step_s,
            duty_cycle=args.duty_cycle,
        )

        print(json.dumps({"waveform_result": result}, indent=2))
        if args.beep:
            _try_beep(worker, True)

    finally:
        _safe_set_voltage(worker, 0.0)
        _safe_output_off(worker)
        worker.close()


if __name__ == "__main__":
    main()
