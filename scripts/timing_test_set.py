#!/usr/bin/env python3
"""timing_test_set.py

One-command regeneration for connected-load timing test suites.

What this does:
- Runs a quick suite (fast sanity run)
- Runs a comprehensive suite (higher confidence)
- Includes fastest mode via poll cadence 0 ms (no intentional sleep)
- Produces a combined markdown summary and a capabilities overview graph

Example:
  source .venv/bin/activate
  python3 scripts/timing_test_set.py \
    --port /dev/ttyUSB0 \
    --voltage 12 --current 1.5 \
    --mode both
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
MATRIX_SCRIPT = ROOT / "scripts" / "connected_load_timing_matrix.py"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _run_matrix(
    *,
    port: str,
    baud: int,
    address: int,
    voltage: float,
    current: float,
    poll_ms: str,
    samples: int,
    settle_s: float,
    out_prefix: Path,
) -> int:
    cmd = [
        sys.executable,
        str(MATRIX_SCRIPT),
        "--port",
        port,
        "--baud",
        str(baud),
        "--address",
        str(address),
        "--voltage",
        str(voltage),
        "--current",
        str(current),
        "--poll-ms",
        poll_ms,
        "--samples",
        str(samples),
        "--settle-s",
        str(settle_s),
        "--out",
        str(out_prefix),
    ]
    print("RUN:", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


def _build_metrics(data: dict) -> list[dict]:
    rows: list[dict] = []
    for r in data.get("results", []):
        rtt = r.get("rtt", {})
        rows.append(
            {
                "poll_ms": r.get("poll_ms"),
                "samples_ok": r.get("samples_ok", 0),
                "samples_err": r.get("samples_err", 0),
                "timeout_rate_pct": float(r.get("timeout_rate", 0.0)) * 100.0,
                "p50_ms": rtt.get("p50_ms"),
                "p95_ms": rtt.get("p95_ms"),
                "jitter_ms": rtt.get("jitter_p95_minus_p50_ms"),
            }
        )
    return rows


def _recommend(rows: list[dict]) -> int | None:
    valid = [r for r in rows if isinstance(r.get("p95_ms"), (int, float))]
    if not valid:
        return None
    best = min(valid, key=lambda r: (r.get("timeout_rate_pct", 100.0), r.get("jitter_ms", 1e9), r.get("p95_ms", 1e9)))
    p95 = float(best["p95_ms"])
    raw = p95 + 20.0
    # Keep recommendations on practical scheduling buckets.
    if raw <= 50:
        q = 20
    elif raw <= 100:
        q = 20
    else:
        q = 50
    return int(math.ceil(raw / q) * q)


def _write_summary(path: Path, quick_data: dict | None, comp_data: dict | None) -> None:
    lines: list[str] = []
    lines.append("# Timing Test Set Summary")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    lines.append("")

    def add_section(title: str, data: dict | None) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not data:
            lines.append("Not run.")
            lines.append("")
            return
        rows = _build_metrics(data)
        rec = _recommend(rows)
        lines.append("| poll_ms | ok | err | timeout_% | p50_ms | p95_ms | jitter_ms |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|")
        for r in rows:
            lines.append(
                "| {poll_ms} | {samples_ok} | {samples_err} | {timeout_rate_pct:.2f} | {p50_ms} | {p95_ms} | {jitter_ms} |".format(
                    **r
                )
            )
        lines.append("")
        if rec is not None:
            lines.append(f"Recommended poll cadence from this suite: **{rec} ms**")
            lines.append("")

    add_section("Quick Suite", quick_data)
    add_section("Comprehensive Suite", comp_data)

    path.write_text("\n".join(lines) + "\n")


def _plot_overview(out_png: Path, quick_data: dict | None, comp_data: dict | None) -> None:
    plt.figure(figsize=(11, 6.5))

    ax1 = plt.subplot(2, 1, 1)

    def plot_suite(data: dict | None, label: str, marker: str) -> None:
        if not data:
            return
        rows = sorted(_build_metrics(data), key=lambda x: x["poll_ms"])
        x = [r["poll_ms"] for r in rows]
        y50 = [r["p50_ms"] for r in rows]
        y95 = [r["p95_ms"] for r in rows]
        ax1.plot(x, y50, marker + "-", label=f"{label} p50")
        ax1.plot(x, y95, marker + "--", label=f"{label} p95")

    plot_suite(quick_data, "Quick", "o")
    plot_suite(comp_data, "Comprehensive", "s")

    ax1.set_title("Connected-load timing capabilities")
    ax1.set_xlabel("Requested poll cadence (ms), 0 ms = fastest/no cadence")
    ax1.set_ylabel("Measured RTT (ms)")
    ax1.grid(True, alpha=0.3)
    ax1.legend()

    ax2 = plt.subplot(2, 1, 2)

    def bar_suite(data: dict | None, label: str, x_shift: float) -> None:
        if not data:
            return
        rows = sorted(_build_metrics(data), key=lambda x: x["poll_ms"])
        x = [r["poll_ms"] + x_shift for r in rows]
        y = [r["timeout_rate_pct"] for r in rows]
        ax2.bar(x, y, width=8.0, label=label, alpha=0.75)

    bar_suite(quick_data, "Quick timeout %", -4.0)
    bar_suite(comp_data, "Comprehensive timeout %", 4.0)

    ax2.set_xlabel("Requested poll cadence (ms)")
    ax2.set_ylabel("Timeout/error rate (%)")
    ax2.grid(True, axis="y", alpha=0.3)
    ax2.legend()

    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=180)
    plt.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run quick/comprehensive connected-load timing suites")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--address", type=int, default=1)
    p.add_argument("--voltage", type=float, default=12.0)
    p.add_argument("--current", type=float, default=1.5)
    p.add_argument("--mode", choices=["quick", "comprehensive", "both"], default="both")

    p.add_argument("--quick-poll-ms", default="0,100,150")
    p.add_argument("--quick-samples", type=int, default=12)

    p.add_argument("--comprehensive-poll-ms", default="0,20,50,100,150,200")
    p.add_argument("--comprehensive-samples", type=int, default=120)

    p.add_argument("--settle-s", type=float, default=2.0)
    p.add_argument("--out-dir", default="docs")
    p.add_argument("--prefix-quick", default="connected_load_timing_matrix_quick")
    p.add_argument("--prefix-comprehensive", default="connected_load_timing_matrix_comprehensive")
    p.add_argument("--summary", default="timing_test_set_summary.md")
    p.add_argument("--overview-png", default="timing_capabilities_overview.png")
    p.add_argument("--analyze-only", action="store_true", help="Skip hardware runs and only regenerate summary/overview from existing JSON files")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    quick_prefix = out_dir / args.prefix_quick
    comp_prefix = out_dir / args.prefix_comprehensive

    quick_data = None
    comp_data = None

    if args.mode in ("quick", "both"):
        if args.analyze_only:
            quick_json = quick_prefix.with_suffix(".json")
            if quick_json.exists():
                quick_data = _read_json(quick_json)
        else:
            rc = _run_matrix(
                port=args.port,
                baud=args.baud,
                address=args.address,
                voltage=args.voltage,
                current=args.current,
                poll_ms=args.quick_poll_ms,
                samples=args.quick_samples,
                settle_s=args.settle_s,
                out_prefix=quick_prefix,
            )
            if rc != 0:
                return rc
            quick_data = _read_json(quick_prefix.with_suffix(".json"))

    if args.mode in ("comprehensive", "both"):
        if args.analyze_only:
            comp_json = comp_prefix.with_suffix(".json")
            if comp_json.exists():
                comp_data = _read_json(comp_json)
        else:
            rc = _run_matrix(
                port=args.port,
                baud=args.baud,
                address=args.address,
                voltage=args.voltage,
                current=args.current,
                poll_ms=args.comprehensive_poll_ms,
                samples=args.comprehensive_samples,
                settle_s=args.settle_s,
                out_prefix=comp_prefix,
            )
            if rc != 0:
                return rc
            comp_data = _read_json(comp_prefix.with_suffix(".json"))

    summary_path = out_dir / args.summary
    overview_path = out_dir / args.overview_png
    _write_summary(summary_path, quick_data=quick_data, comp_data=comp_data)
    _plot_overview(overview_path, quick_data=quick_data, comp_data=comp_data)

    print(f"WROTE {summary_path}")
    print(f"WROTE {overview_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
