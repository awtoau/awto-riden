#!/usr/bin/env python3
"""connected_load_timing_matrix.py

Run a repeatable timing matrix against a REAL connected load while output is ON.

Goal:
- Quantify poll-cadence behavior under load (RTT, jitter, timeout rate)
- Capture output stability metrics (v_out / i_out noise) per cadence
- Save machine-readable JSON + PNG charts for reporting

Example:
  source .venv/bin/activate
  python3 scripts/connected_load_timing_matrix.py \
      --port /dev/ttyUSB0 --voltage 12 --current 1.5 \
      --poll-ms 20,50,100,150,200 --samples 120 --settle-s 3

Safety:
- Script forces output OFF on exit (best effort).
- Do not run while MCP server is holding the same serial port.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt

# Local import (repo root)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from riden_daemon import RidenWorker


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int((p / 100.0) * (len(sorted_vals) - 1))
    return sorted_vals[idx]


def run_case(worker: RidenWorker, poll_ms: int, samples: int) -> dict:
    rows = []
    dts = []
    errs = 0

    prev_t = time.perf_counter()
    interrupted = False
    for _ in range(samples):
        t0 = time.perf_counter()
        try:
            st = worker.status()
            t1 = time.perf_counter()
            dt_ms = (t1 - t0) * 1000.0
            loop_dt_ms = (t1 - prev_t) * 1000.0
            prev_t = t1

            dts.append(dt_ms)
            rows.append(
                {
                    "ts": time.time(),
                    "rtt_ms": dt_ms,
                    "loop_dt_ms": loop_dt_ms,
                    "v_out": st.get("v_out"),
                    "i_out": st.get("i_out"),
                    "p_out": st.get("p_out"),
                    "cv_cc": st.get("cv_cc"),
                    "protect": st.get("protect"),
                    "output": st.get("output"),
                }
            )
        except KeyboardInterrupt:
            interrupted = True
            break
        except Exception:
            errs += 1

        sl = poll_ms / 1000.0 - (time.perf_counter() - t0)
        if sl > 0:
            time.sleep(sl)

    if not dts:
        return {
            "poll_ms": poll_ms,
            "samples_ok": 0,
            "samples_err": errs,
            "timeout_rate": 1.0,
            "interrupted": interrupted,
            "rtt": {},
            "stability": {},
            "rows": rows,
        }

    s = sorted(dts)
    vs = [r["v_out"] for r in rows if isinstance(r.get("v_out"), (int, float))]
    is_ = [r["i_out"] for r in rows if isinstance(r.get("i_out"), (int, float))]

    return {
        "poll_ms": poll_ms,
        "samples_ok": len(dts),
        "samples_err": errs,
        "interrupted": interrupted,
        "timeout_rate": round(errs / max(1, samples), 4),
        "rtt": {
            "min_ms": round(min(s), 3),
            "p50_ms": round(statistics.median(s), 3),
            "p90_ms": round(_percentile(s, 90), 3),
            "p95_ms": round(_percentile(s, 95), 3),
            "max_ms": round(max(s), 3),
            "mean_ms": round(statistics.mean(s), 3),
            "jitter_p95_minus_p50_ms": round(_percentile(s, 95) - statistics.median(s), 3),
        },
        "stability": {
            "v_out_mean": round(statistics.mean(vs), 4) if vs else None,
            "v_out_std": round(statistics.pstdev(vs), 5) if len(vs) > 1 else 0.0,
            "i_out_mean": round(statistics.mean(is_), 5) if is_ else None,
            "i_out_std": round(statistics.pstdev(is_), 6) if len(is_) > 1 else 0.0,
        },
        "rows": rows,
    }


def make_plots(results: list[dict], out_prefix: Path) -> None:
    poll = [r["poll_ms"] for r in results]
    p50 = [r.get("rtt", {}).get("p50_ms", 0) for r in results]
    p95 = [r.get("rtt", {}).get("p95_ms", 0) for r in results]
    tout = [r.get("timeout_rate", 0) * 100.0 for r in results]

    plt.figure(figsize=(10, 5))
    plt.plot(poll, p50, "o-", label="RTT p50")
    plt.plot(poll, p95, "o-", label="RTT p95")
    plt.xlabel("Requested poll cadence (ms)")
    plt.ylabel("Measured RTT (ms)")
    plt.title("Connected-load RTT vs requested cadence")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_prefix.with_suffix(".rtt.png"), dpi=170)
    plt.close()

    plt.figure(figsize=(10, 4))
    plt.bar([str(p) for p in poll], tout)
    plt.xlabel("Requested poll cadence (ms)")
    plt.ylabel("Timeout/error rate (%)")
    plt.title("Connected-load timeout rate by cadence")
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_prefix.with_suffix(".timeout.png"), dpi=170)
    plt.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Connected-load timing matrix for Riden PSU")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--address", type=int, default=1)
    p.add_argument("--voltage", type=float, default=12.0, help="Setpoint voltage during test")
    p.add_argument("--current", type=float, default=1.5, help="Current limit during test")
    p.add_argument("--poll-ms", default="20,50,100,150,200", help="Comma-separated cadence list")
    p.add_argument("--samples", type=int, default=120, help="Samples per cadence point")
    p.add_argument("--settle-s", type=float, default=3.0, help="Settle time after enabling output")
    p.add_argument("--out", default="docs/connected_load_timing_matrix", help="Output prefix (without extension)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    poll_points = [int(x.strip()) for x in args.poll_ms.split(",") if x.strip()]
    out_prefix = Path(args.out)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    worker = RidenWorker(port=args.port, baud=args.baud, address=args.address)
    started = time.time()

    report = {
        "captured_on": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "port": args.port,
        "baud": args.baud,
        "address": args.address,
        "setpoint": {"voltage": args.voltage, "current": args.current},
        "samples_per_point": args.samples,
        "poll_points_ms": poll_points,
        "results": [],
        "notes": [
            "Host-side timing (perf_counter) around worker.status() call.",
            "Modbus RTU has no device timestamp fields; no exact device sample-time recovery.",
            "Use p95 and timeout_rate to choose robust cadence under load.",
        ],
    }

    interrupted = False
    try:
        worker.open()

        # Set deterministic operating point for the load under test.
        worker.set_output(False)
        worker.set_voltage(args.voltage)
        worker.set_current(args.current)
        worker.set_output(True)
        time.sleep(max(0.0, args.settle_s))

        for poll in poll_points:
            case = run_case(worker, poll_ms=poll, samples=args.samples)
            report["results"].append(case)
            rtt = case.get("rtt", {})
            print(
                f"poll={poll:>4}ms ok={case['samples_ok']:>4}/{args.samples} "
                f"p50={rtt.get('p50_ms')} p95={rtt.get('p95_ms')} "
                f"jitter={rtt.get('jitter_p95_minus_p50_ms')} "
                f"timeout={case['timeout_rate']*100:.2f}%"
            )
            if case.get("interrupted"):
                interrupted = True
                break

        make_plots(report["results"], out_prefix)

        json_path = out_prefix.with_suffix(".json")
        json_path.write_text(json.dumps(report, indent=2))
        print(f"WROTE {json_path}")
        print(f"WROTE {out_prefix.with_suffix('.rtt.png')}")
        print(f"WROTE {out_prefix.with_suffix('.timeout.png')}")
        if interrupted:
            print("INTERRUPTED: partial report written")
            return 130
        return 0
    except KeyboardInterrupt:
        interrupted = True
        # Try to persist whatever we have.
        try:
            make_plots(report["results"], out_prefix)
        except Exception:
            pass
        try:
            json_path = out_prefix.with_suffix(".json")
            json_path.write_text(json.dumps(report, indent=2))
            print(f"WROTE {json_path}")
        except Exception:
            pass
        print("INTERRUPTED: partial report written")
        return 130
    finally:
        # Best effort safety shutdown.
        try:
            worker.set_output(False)
        except BaseException:
            pass
        try:
            worker.close()
        except BaseException:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
