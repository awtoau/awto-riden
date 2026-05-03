"""
awto-riden CLI — direct serial commands (no daemon required).

One-shot Riden RD60xx commands for human use at the bench.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import colorlog
from rich_argparse import RichHelpFormatter

from protocol import ERR_INTERNAL, ERR_INVALID_ARG, ERR_IO, ERR_NOT_CONNECTED, ERR_TIMEOUT
from riden_daemon import RidenWorker

log = logging.getLogger("awto.cli")


def _build_register_scan_report(scan: dict[str, Any]) -> dict[str, Any]:
    """Build a concise report focused on undocumented register hits."""
    unknown = sorted(scan.get("unknown_nonzero", []), key=lambda r: int(r.get("addr", 0)))

    value_counts = Counter(int(r.get("value", 0)) for r in unknown)
    unique_values = [
        {
            "value": val,
            "hex": f"0x{val:04X}",
            "count": count,
        }
        for val, count in sorted(value_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    ranges: list[dict[str, int]] = []
    if unknown:
        start = prev = int(unknown[0]["addr"])
        for row in unknown[1:]:
            addr = int(row["addr"])
            if addr == prev + 1:
                prev = addr
            else:
                ranges.append({"start": start, "end": prev})
                start = prev = addr
        ranges.append({"start": start, "end": prev})

    return {
        "scan_window": {
            "start": scan.get("start"),
            "end": scan.get("end"),
            "batch": scan.get("batch"),
        },
        "unknown_nonzero_count": len(unknown),
        "unknown_ranges": ranges,
        "unique_unknown_values": unique_values,
        "unknown_registers": [
            {
                "addr": int(r["addr"]),
                "value": int(r["value"]),
                "hex": r["hex"],
            }
            for r in unknown
        ],
    }


def _error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return ERR_TIMEOUT
    if isinstance(exc, (ValueError, TypeError)):
        return ERR_INVALID_ARG
    if isinstance(exc, (IOError, OSError)):
        msg = str(exc).lower()
        if "not connected" in msg:
            return ERR_NOT_CONNECTED
        return ERR_IO
    return ERR_INTERNAL


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    root = logging.getLogger()
    root.setLevel(level)

    try:
        syslog = logging.handlers.SysLogHandler(
            address="/dev/log",
            facility=logging.handlers.SysLogHandler.LOG_USER,
        )
        syslog.ident = "awto-riden: "
        syslog.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(syslog)
    except OSError:
        pass

    handler = colorlog.StreamHandler(sys.stderr)
    handler.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(levelname)-8s%(reset)s %(message)s",
        log_colors={
            "DEBUG":    "cyan",
            "INFO":     "green",
            "WARNING":  "yellow",
            "ERROR":    "red",
            "CRITICAL": "bold_red",
        },
    ))
    root.addHandler(handler)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="awto-riden",
        description="Control a Riden RD60xx power supply via USB/Bluetooth serial.",
        formatter_class=RichHelpFormatter,
        epilog=(
            "Examples:\n"
            "  awto-riden --port /dev/ttyUSB0 status\n"
            "  awto-riden --port /dev/ttyUSB0 set-voltage 5.0\n"
            "  awto-riden --port /dev/ttyUSB0 set-current 1.5\n"
            "  awto-riden --port /dev/ttyUSB0 output on\n"
            "  awto-riden --port /dev/ttyUSB0 power-cycle --seconds 3\n"
            "  awto-riden --port /dev/ttyUSB0 info\n"
        ),
    )
    ap.add_argument(
        "--port",
        default="/dev/ttyUSB0",
        help="Serial port (default: /dev/ttyUSB0)",
    )
    ap.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Baud rate (default: 115200)",
    )
    ap.add_argument(
        "--address",
        type=int,
        default=1,
        help="Modbus slave address (default: 1)",
    )
    ap.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="enable debug logging",
    )

    sub = ap.add_subparsers(dest="subcmd", metavar="COMMAND")
    _F = RichHelpFormatter

    sub.add_parser("ping", help="health-check the PSU", formatter_class=_F)
    sub.add_parser("capabilities", help="show API capabilities and error codes", formatter_class=_F)
    sub.add_parser("status", help="show current PSU state", formatter_class=_F)
    sub.add_parser("info", help="show PSU process health", formatter_class=_F)

    sp_v = sub.add_parser("set-voltage", help="set output voltage (V)", formatter_class=_F)
    sp_v.add_argument("volts", type=float, help="voltage in volts")

    sp_a = sub.add_parser("set-current", help="set current limit (A)", formatter_class=_F)
    sp_a.add_argument("amps", type=float, help="current in amps")

    sp_out = sub.add_parser("output", help="enable or disable PSU output", formatter_class=_F)
    sp_out.add_argument("state", choices=["on", "off"])

    sp_ovp = sub.add_parser("set-ovp", help="set over-voltage protection threshold (V)", formatter_class=_F)
    sp_ovp.add_argument("volts", type=float)

    sp_ocp = sub.add_parser("set-ocp", help="set over-current protection threshold (A)", formatter_class=_F)
    sp_ocp.add_argument("amps", type=float)

    sp_pc = sub.add_parser("power-cycle", help="turn output off, wait, turn back on", formatter_class=_F)
    sp_pc.add_argument(
        "--seconds",
        type=float,
        default=2.0,
        metavar="S",
        help="off-time in seconds (default: 2.0)",
    )

    sp_log = sub.add_parser("log-start", help="start periodic JSONL status logging", formatter_class=_F)
    sp_log.add_argument(
        "path",
        nargs="?",
        default="/tmp/riden.log",
        help="output file path (default: /tmp/riden.log)",
    )
    sp_log.add_argument(
        "--interval",
        type=int,
        default=1000,
        metavar="MS",
        help="polling interval in ms (default: 1000)",
    )

    sub.add_parser("log-stop", help="stop periodic logging", formatter_class=_F)

    sp_st = sub.add_parser("speed-test", help="benchmark Modbus round-trip latency", formatter_class=_F)
    sp_st.add_argument("--count", type=int, default=30, metavar="N", help="number of reads (default: 30)")
    sp_st.add_argument(
        "--register-profile",
        action="store_true",
        help="benchmark multiple register groups to detect register-specific slowness",
    )

    sp_prof = sub.add_parser("profile-serial", help="profile serial timing and recommend stable poll cadence", formatter_class=_F)
    sp_prof.add_argument("--count", type=int, default=20, metavar="N", help="number of reads (default: 20)")
    sp_prof.add_argument("--sleep-ms", type=int, default=100, metavar="MS", help="inter-read delay during profiling (default: 100)")

    sp_scan = sub.add_parser(
        "register-scan",
        help="scan Modbus registers and highlight undocumented non-zero values",
        formatter_class=_F,
    )
    sp_scan.add_argument("--start", type=int, default=0, metavar="ADDR", help="first register (default: 0)")
    sp_scan.add_argument("--end", type=int, default=300, metavar="ADDR", help="one-past-last register (default: 300)")
    sp_scan.add_argument("--batch", type=int, default=50, metavar="N", help="registers per read request, 1..125 (default: 50)")
    sp_scan.add_argument(
        "--include-zero",
        action="store_true",
        help="include zero-valued registers in the output list (default: omitted)",
    )
    sp_scan.add_argument(
        "--save-json",
        type=Path,
        default=None,
        metavar="PATH",
        help="optional path to write scan JSON",
    )
    sp_scan.add_argument(
        "--report",
        action="store_true",
        help="print concise report of undocumented/non-known registers",
    )
    sp_scan.add_argument(
        "--report-only",
        action="store_true",
        help="only output concise report (omit full raw scan payload)",
    )

    sp_diff = sub.add_parser(
        "diff-scan",
        help="differential scan: compare registers with output off vs on",
        formatter_class=_F,
    )
    sp_diff.add_argument("--start", type=int, default=0, metavar="ADDR", help="first register (default: 0)")
    sp_diff.add_argument("--end", type=int, default=300, metavar="ADDR", help="one-past-last register (default: 300)")
    sp_diff.add_argument("--batch", type=int, default=50, metavar="N", help="registers per read request (default: 50)")
    sp_diff.add_argument(
        "--output-off-first",
        action="store_true",
        help="scan A=on then B=off (default: scan A=off then B=on)",
    )
    sp_diff.add_argument("--settle-ms", type=int, default=500, metavar="MS", help="ms to wait after toggling output (default: 500)")
    sp_diff.add_argument(
        "--unknown-only",
        action="store_true",
        help="only show registers that are undocumented in the known map",
    )
    sp_diff.add_argument(
        "--save-json",
        type=Path,
        default=None,
        metavar="PATH",
        help="optional path to write diff JSON",
    )

    sp_plot = sub.add_parser(
        "plot",
        help="auto-plot one or more waveform JSONL files (shape detected from data)",
        formatter_class=_F,
    )
    sp_plot.add_argument(
        "files", nargs="+", metavar="FILE.jsonl",
        help="JSONL files captured by waveform_capture.py",
    )
    sp_plot.add_argument(
        "--out", metavar="OUT.png",
        help="Output PNG path (only valid when a single file is given)",
    )

    args = ap.parse_args()
    if args.subcmd is None:
        ap.print_help()
        sys.exit(2)

    _setup_logging(args.verbose)

    # The 'plot' subcommand is offline — no PSU connection needed.
    if args.subcmd == "plot":
        import sys as _sys
        from pathlib import Path as _Path
        _scripts = str(_Path(__file__).resolve().parent / "scripts")
        if _scripts not in _sys.path:
            _sys.path.insert(0, _scripts)
        from plot_waveforms import plot_jsonl
        saved = []
        for _f in args.files:
            _out = plot_jsonl(_f, args.out if len(args.files) == 1 else None)
            print(f"Saved: {_out}")
            saved.append(str(_out))
        print(json.dumps({"ok": True, "saved": saved}, indent=2))
        return

    # Open serial connection
    try:
        worker = RidenWorker(port=args.port, baud=args.baud, address=args.address)
        worker.open()
    except Exception as e:
        print(json.dumps({"error": f"failed to open PSU: {e}", "code": _error_code(e)}), file=sys.stderr)
        sys.exit(1)

    # Execute command
    try:
        c = args.subcmd
        result: dict[str, Any] = {}

        if c == "ping":
            result = {"ok": True}
        elif c == "capabilities":
            result = worker.capabilities()
        elif c == "status":
            result = worker.status()
        elif c == "info":
            result = worker.info()
        elif c == "set-voltage":
            worker.set_voltage(args.volts)
            result = worker.status()
        elif c == "set-current":
            worker.set_current(args.amps)
            result = worker.status()
        elif c == "output":
            worker.set_output(args.state == "on")
            result = worker.status()
        elif c == "set-ovp":
            worker.set_ovp(args.volts)
            result = {"ok": True}
        elif c == "set-ocp":
            worker.set_ocp(args.amps)
            result = {"ok": True}
        elif c == "power-cycle":
            worker.power_cycle(args.seconds)
            result = worker.status()
        elif c == "log-start":
            worker.log_start(args.path, args.interval)
            result = {"ok": True}
        elif c == "log-stop":
            worker.log_stop()
            result = {"ok": True}
        elif c == "speed-test":
            result = worker.speed_test(args.count, register_profile=args.register_profile)
        elif c == "profile-serial":
            result = worker.profile_serial(args.count, args.sleep_ms)
        elif c == "register-scan":
            result = worker.register_scan(
                start=args.start,
                end=args.end,
                batch=args.batch,
                skip_zero=not args.include_zero,
            )
            if args.save_json is not None:
                args.save_json.parent.mkdir(parents=True, exist_ok=True)
                args.save_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
                result["saved"] = str(args.save_json)
            if args.report or args.report_only:
                report = _build_register_scan_report(result)
                result = report if args.report_only else {"scan": result, "report": report}
        elif c == "diff-scan":
            result = worker.diff_scan(
                start=args.start,
                end=args.end,
                batch=args.batch,
                output_on=not args.output_off_first,
                settle_ms=args.settle_ms,
            )
            if args.save_json is not None:
                args.save_json.parent.mkdir(parents=True, exist_ok=True)
                args.save_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
                result["saved"] = str(args.save_json)
            if args.unknown_only:
                result = {k: v for k, v in result.items() if k != "changed"}
                result["changed"] = result.pop("changed_unknown")
                result["changed_count"] = result.pop("changed_unknown_count")

        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e), "code": _error_code(e)}), file=sys.stderr)
        sys.exit(1)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
