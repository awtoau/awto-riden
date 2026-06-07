#!/usr/bin/env python3
"""Capture MR11 waveform datasets for period-wide and clipping plots.

Generates:
- sine/triangle/sawtooth/square at the same settings (period-wide comparisons)
- a current-limited sine run to visualize clipping/CC transitions
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from riden_daemon import RidenDevice
from riden_transport import SerialTransport
from awto_riden_report import copy_artifact, normalize_device_slug, update_reports_index, utc_run_stamp, write_manifest


REG_VOUT = 10
REG_CNT = 9
PROT_MAP = {0: "none", 1: "OVP", 2: "OCP"}


def _wave_v(shape: str, phase: float, v_center: float, v_amp: float) -> float:
    if shape == "sine":
        return v_center + v_amp * math.sin(2 * math.pi * phase)
    if shape == "triangle":
        return v_center + v_amp * (4 * abs(phase - 0.5) - 1)
    if shape == "sawtooth":
        return v_center + v_amp * (2 * phase - 1)
    if shape == "dc":
        return v_center
    # square
    return (v_center + v_amp) if phase < 0.5 else (v_center - v_amp)


def _capture_case(
    psu: RidenDevice,
    tr: SerialTransport,
    shape: str,
    freq_hz: float,
    duration_s: float,
    v_center: float,
    v_amp: float,
    i_limit_a: float,
    path: Path,
    device_meta: dict,
) -> int:
    rows = []
    read_errors = 0
    psu.set_i_set(i_limit_a)
    psu.set_v_set(max(0.0, round(v_center - v_amp, 2)))
    psu.set_output(True)

    t0 = time.perf_counter()
    last_v_set = None
    while True:
        now = time.perf_counter()
        elapsed = now - t0
        if elapsed >= duration_s:
            break

        phase = (elapsed * freq_hz) % 1.0
        v_set = round(_wave_v(shape, phase, v_center, v_amp), 2)
        if v_set != last_v_set:
            psu.set_v_set(v_set)
            last_v_set = v_set

        try:
            raw = tr.read(REG_VOUT, REG_CNT)
        except Exception:
            # Keep progressing through the waveform if a poll times out.
            read_errors += 1
            continue
        rows.append(
            {
                "ts": time.time(),
                "elapsed_s": round(elapsed, 4),
                "phase": round(phase, 6),
                "shape": shape,
                "freq_hz": freq_hz,
                "i_limit_a": i_limit_a,
                "v_set": v_set,
                "v_out": round(psu.get_v_out(raw[0]), 3),
                "i_out": round(psu.get_i_out(raw[1]), 4),
                "cv_cc": "CV" if raw[7] == 0 else "CC",
                "protect": PROT_MAP.get(raw[6], "none"),
                "output": bool(raw[8]),
                "device_type": device_meta.get("type"),
                "device_id": device_meta.get("id"),
                "firmware": device_meta.get("fw"),
                "serial_number": device_meta.get("sn"),
            }
        )

    psu.set_output(False)
    path.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""))
    return len(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Capture MR11 waveform datasets")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--address", type=int, default=1)
    p.add_argument("--out-dir", default="docs/data")
    p.add_argument("--freq-hz", type=float, default=0.5)
    p.add_argument("--periods", type=int, default=4)
    p.add_argument("--v-center", type=float, default=6.0)
    p.add_argument("--v-amp", type=float, default=6.0)
    p.add_argument("--i-limit-normal", type=float, default=1.5)
    p.add_argument("--i-limit-clipped", type=float, default=0.2)
    p.add_argument("--cc-demo-duration-s", type=float, default=8.0)
    p.add_argument("--cc-demo-freq-hz", type=float, default=0.5)
    p.add_argument("--cc-demo-fixed-v", type=float, default=12.0)
    p.add_argument("--cc-demo-sine-i-limit", type=float, default=0.2)
    p.add_argument("--cc-demo-fixed-i-limit", type=float, default=0.3)
    p.add_argument("--transport-timeout", type=float, default=0.25,
                   help="Transport read timeout in seconds (default: 0.25)")
    p.add_argument("--transport-retries", type=int, default=2,
                   help="Transport retries per request (default: 2)")
    p.add_argument("--reports-root", default="docs/reports",
                   help="Root directory for per-device run report pages")
    p.add_argument("--no-report-pages", action="store_true",
                   help="Disable writing per-run report page and global index")
    return p.parse_args()


def _device_meta(psu: RidenDevice, args: argparse.Namespace) -> dict:
    return {
        "type": getattr(psu, "type", "unknown"),
        "id": getattr(psu, "id", None),
        "fw": getattr(psu, "fw", None),
        "sn": getattr(psu, "sn", None),
        "port": args.port,
        "baud": args.baud,
        "address": args.address,
    }


def _write_waveform_report(
    *,
    reports_root: Path,
    device_meta: dict,
    args: argparse.Namespace,
    generated_jsonl: list[Path],
) -> Path:
    device_slug = normalize_device_slug(device_meta)
    run_stamp = utc_run_stamp()
    run_dir = reports_root / device_slug / "waveform_capture" / run_stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for src in generated_jsonl:
        copied.append(copy_artifact(src, run_dir / src.name))

    report_path = run_dir / "report.md"
    lines: list[str] = []
    lines.append("# Waveform Capture Report")
    lines.append("")
    lines.append(f"Run: {run_stamp}")
    lines.append("")
    lines.append("## Device")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Model | {device_meta.get('type')} |")
    lines.append(f"| Device ID | {device_meta.get('id')} |")
    lines.append(f"| Firmware | {device_meta.get('fw')} |")
    lines.append(f"| Serial | {device_meta.get('sn')} |")
    lines.append(f"| Port | {device_meta.get('port')} |")
    lines.append("")
    lines.append("## Capture Parameters")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| freq_hz | {args.freq_hz} |")
    lines.append(f"| periods | {args.periods} |")
    lines.append(f"| v_center | {args.v_center} |")
    lines.append(f"| v_amp | {args.v_amp} |")
    lines.append(f"| i_limit_normal | {args.i_limit_normal} |")
    lines.append(f"| i_limit_clipped | {args.i_limit_clipped} |")
    lines.append(f"| cc_demo_duration_s | {args.cc_demo_duration_s} |")
    lines.append(f"| cc_demo_freq_hz | {args.cc_demo_freq_hz} |")
    lines.append(f"| cc_demo_fixed_v | {args.cc_demo_fixed_v} |")
    lines.append(f"| cc_demo_sine_i_limit | {args.cc_demo_sine_i_limit} |")
    lines.append(f"| cc_demo_fixed_i_limit | {args.cc_demo_fixed_i_limit} |")
    lines.append("")
    lines.append("## Data Files")
    lines.append("")
    for p in copied:
        lines.append(f"- [{p.name}]({p.name})")
    report_path.write_text("\n".join(lines) + "\n")

    write_manifest(
        run_dir=run_dir,
        reports_root=reports_root,
        report_kind="waveform_capture",
        report_title=f"Waveform Capture {device_meta.get('type')} {run_stamp}",
        device_meta=device_meta,
        report_path=report_path,
        artifacts=[report_path, *copied],
        extra={
            "script": "scripts/awto_riden_waveform_capture.py",
            "device_slug": device_slug,
        },
    )
    index_path = update_reports_index(reports_root)
    print(f"WROTE {report_path}")
    print(f"WROTE {index_path}")
    return report_path


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    period_s = 1.0 / max(args.freq_hz, 1e-6)
    duration_s = args.periods * period_s

    tr = SerialTransport(
        args.port,
        args.baud,
        args.address,
        retries=args.transport_retries,
        timeout=args.transport_timeout,
    )
    tr.open()
    psu = RidenDevice(tr)
    device_meta = _device_meta(psu, args)
    generated_jsonl: list[Path] = []

    try:
        shapes = ["sine", "triangle", "sawtooth", "square"]
        for shape in shapes:
            out_path = out_dir / f"mr11_{shape}_period.jsonl"
            n = _capture_case(
                psu,
                tr,
                shape=shape,
                freq_hz=args.freq_hz,
                duration_s=duration_s,
                v_center=args.v_center,
                v_amp=args.v_amp,
                i_limit_a=args.i_limit_normal,
                path=out_path,
                device_meta=device_meta,
            )
            print(f"{shape:8s} period capture -> {n:3d} rows {out_path}")
            generated_jsonl.append(out_path)
            time.sleep(1.0)

        clip_path = out_dir / "mr11_sine_clipped_current_limit.jsonl"
        n = _capture_case(
            psu,
            tr,
            shape="sine",
            freq_hz=args.freq_hz,
            duration_s=duration_s,
            v_center=args.v_center,
            v_amp=args.v_amp,
            i_limit_a=args.i_limit_clipped,
            path=clip_path,
            device_meta=device_meta,
        )
        print(f"sine clip  capture -> {n:3d} rows {clip_path}")
        generated_jsonl.append(clip_path)

        # Dedicated current-limit demos requested for documentation.
        cc_sine_path = out_dir / "mr11_current_limit_demo_sine_0_12v_i200ma.jsonl"
        n = _capture_case(
            psu,
            tr,
            shape="sine",
            freq_hz=args.cc_demo_freq_hz,
            duration_s=args.cc_demo_duration_s,
            v_center=6.0,
            v_amp=6.0,
            i_limit_a=args.cc_demo_sine_i_limit,
            path=cc_sine_path,
            device_meta=device_meta,
        )
        print(f"cc demo sine capture -> {n:3d} rows {cc_sine_path}")
        generated_jsonl.append(cc_sine_path)
        time.sleep(1.0)

        cc_fixed_path = out_dir / "mr11_current_limit_demo_fixed_12v_i300ma.jsonl"
        n = _capture_case(
            psu,
            tr,
            shape="dc",
            freq_hz=max(args.cc_demo_freq_hz, 1e-6),
            duration_s=args.cc_demo_duration_s,
            v_center=args.cc_demo_fixed_v,
            v_amp=0.0,
            i_limit_a=args.cc_demo_fixed_i_limit,
            path=cc_fixed_path,
            device_meta=device_meta,
        )
        print(f"cc demo fixed capture-> {n:3d} rows {cc_fixed_path}")
        generated_jsonl.append(cc_fixed_path)

        if not args.no_report_pages:
            _write_waveform_report(
                reports_root=Path(args.reports_root),
                device_meta=device_meta,
                args=args,
                generated_jsonl=generated_jsonl,
            )

        # Safe reset
        psu.set_v_set(12.0)
        psu.set_i_set(1.0)
        psu.set_output(False)
    finally:
        try:
            psu.set_output(False)
        except Exception:
            pass
        tr.close()

    print("Done — PSU reset to 12V/1A, output OFF")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
