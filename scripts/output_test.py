#!/usr/bin/env python3
# output_test.py — write-path hardware test for RidenWorker, safe-state first.
#
# Safety model (mandatory for any write-path test on a live supply):
#
#   1. RECONNECT PER TEST. Every test stage opens its OWN fresh RidenWorker
#      connection and closes it — nothing is shared, so a wedged handle in one
#      stage cannot corrupt another.
#
#   2. SEPARATE-CONNECTION ZEROING (second stage). After each test, a DISTINCT
#      connection is opened solely to drive the PSU to 0.0 V / 0.0 A. The
#      zeroing never rides on the test's own connection — it is always its own
#      second stage, so the safe state is reached through an independent path.
#
#   3. GLOBAL ERROR HANDLER. The whole run is wrapped so that ANY exception
#      (including Ctrl-C) forces the same separate-connection 0.0 V / 0.0 A
#      safe state before the error propagates. A failed or aborted test never
#      leaves the supply energized.
#
# The supply is left at 0.0 V / 0.0 A with output OFF when the run ends, however
# it ends. Complements scripts/transport_test.py (read-only).
#
# Usage:
#   python3 scripts/output_test.py --port /dev/ttyUSB0 --address 1
#   python3 scripts/output_test.py --simulate-error   # prove the global handler zeros

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from riden_daemon import RidenWorker

# Safe state the supply is driven to between/after tests and on any error.
SAFE_V = 0.0
SAFE_I = 0.0

# RD/RK firmware refreshes its Modbus registers once per scan cycle (~143 ms);
# wait two cycles after a write before reading back so the setpoint has settled.
SETTLE_S = 0.30


def _connect(args) -> RidenWorker:
    """Open a FRESH RidenWorker connection. Each test and each zeroing stage
    calls this — connections are never reused across stages."""
    w = RidenWorker(port=args.port, baud=args.baud, address=args.address)
    w.open()
    return w


def safe_zero(args, reason: str = "") -> None:
    """SECOND STAGE: open a separate connection and force 0.0 V / 0.0 A + output OFF.

    Independent of any test connection. Best-effort and never raises — it is the
    last line of defence, invoked after every test, by the global error handler,
    and once more at the very end.
    """
    tag = f" ({reason})" if reason else ""
    try:
        w = _connect(args)
        try:
            w.set_voltage(SAFE_V)
            w.set_current(SAFE_I)
            w.set_output(False)
            time.sleep(SETTLE_S)
            s = w.status()
            print(f"  safe-state{tag}: v_set={s['v_set']} V  i_set={s['i_set']} A  "
                  f"output={'ON' if s['output'] else 'OFF'}")
        finally:
            w.close()
    except Exception as exc:
        # Never let the safety stage itself raise — report and move on.
        print(f"  WARNING: safe-state{tag} could not be applied: {exc}", file=sys.stderr)


def run_test(args, name: str, body) -> tuple[str, bool]:
    """Run one test on its OWN fresh connection, then zero via a SEPARATE one.

    *body* receives the open worker and returns True on pass. The test
    connection is always closed before the separate zeroing stage runs.
    """
    print(f"[test] {name}")
    w = _connect(args)                       # (1) reconnect per test
    try:
        ok = bool(body(w))
    finally:
        w.close()                            # close the test connection first ...
    safe_zero(args, reason=f"after {name}")  # (2) ... then zero on a SEPARATE connection
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return name, ok


def run_all_tests(args) -> list[tuple[str, bool]]:
    results: list[tuple[str, bool]] = []

    def t_set_voltage(w) -> bool:
        w.set_voltage(1.0)
        time.sleep(SETTLE_S)
        return abs(float(w.status()["v_set"]) - 1.0) < 0.05
    results.append(run_test(args, "set_voltage 1.0 V", t_set_voltage))

    def t_set_current(w) -> bool:
        w.set_current(0.5)
        time.sleep(SETTLE_S)
        return abs(float(w.status()["i_set"]) - 0.5) < 0.05
    results.append(run_test(args, "set_current 0.5 A", t_set_current))

    def t_output_on(w) -> bool:
        # Output is at 0.0 V from the prior zeroing stage, so this energizes
        # nothing — it only exercises the output-enable write path.
        w.set_output(True)
        time.sleep(SETTLE_S)
        return w.status()["output"] is True
    results.append(run_test(args, "output on", t_output_on))

    if args.simulate_error:
        # Deliberately fail to prove the GLOBAL handler still forces safe state.
        raise RuntimeError("simulated mid-test failure (--simulate-error)")

    def t_output_off(w) -> bool:
        w.set_output(False)
        time.sleep(SETTLE_S)
        return w.status()["output"] is False
    results.append(run_test(args, "output off", t_output_off))

    return results


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RidenWorker write-path test (safe-state first)")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--address", type=int, default=1)
    p.add_argument("--simulate-error", action="store_true",
                   help="raise mid-run to verify the global error handler zeros the PSU")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    results: list[tuple[str, bool]] = []
    try:
        results = run_all_tests(args)
    except BaseException as exc:                       # GLOBAL error handler
        print(f"GLOBAL ERROR ({type(exc).__name__}): {exc}", file=sys.stderr)
        safe_zero(args, reason="global error handler")  # force safe state on ANY failure
        raise
    finally:
        safe_zero(args, reason="final")                 # belt-and-braces: always safe at exit

    ok = all(passed for _, passed in results)
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
