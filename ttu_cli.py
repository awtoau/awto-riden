"""
awto-riden CLI — direct serial commands (no daemon required).

One-shot Riden RD60xx commands for human use at the bench.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
from typing import Any

import colorlog
from rich_argparse import RichHelpFormatter

from protocol import ERR_INTERNAL, ERR_INVALID_ARG, ERR_IO, ERR_NOT_CONNECTED, ERR_TIMEOUT
from riden_daemon import RidenWorker

log = logging.getLogger("awto.cli")


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

    sp_prof = sub.add_parser("profile-serial", help="profile serial timing and recommend stable poll cadence", formatter_class=_F)
    sp_prof.add_argument("--count", type=int, default=20, metavar="N", help="number of reads (default: 20)")
    sp_prof.add_argument("--sleep-ms", type=int, default=100, metavar="MS", help="inter-read delay during profiling (default: 100)")

    args = ap.parse_args()
    if args.subcmd is None:
        ap.print_help()
        sys.exit(2)

    _setup_logging(args.verbose)

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
            result = worker.speed_test(args.count)
        elif c == "profile-serial":
            result = worker.profile_serial(args.count, args.sleep_ms)

        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e), "code": _error_code(e)}), file=sys.stderr)
        sys.exit(1)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
