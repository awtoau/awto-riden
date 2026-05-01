"""
awto-riden MCP server — direct serial, no daemon.

Exposes Riden RD60xx power supply control as MCP tools for Copilot / AI agents.
Opens the serial port directly on startup; each tool call uses the shared connection.

Usage:
    python3 mcp_server.py --port /dev/ttyUSB0 --baud 115200

Or register in .vscode/mcp.json:
    {
      "mcpServers": {
        "awto-riden": {
          "command": "python3",
          "args": ["/path/to/mcp_server.py", "--port", "/dev/ttyUSB0"]
        }
      }
    }
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from typing import Any

import colorlog
from mcp.server.fastmcp import FastMCP

from riden_daemon import RidenWorker

log = logging.getLogger("awto.mcp")

# ---------------------------------------------------------------------------
# Logging (stderr only — stdout reserved for MCP stdio)
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    try:
        syslog = logging.handlers.SysLogHandler(
            address="/dev/log",
            facility=logging.handlers.SysLogHandler.LOG_USER,
        )
        syslog.ident = "awto-riden-mcp: "
        syslog.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(syslog)
    except OSError:
        pass

    handler = colorlog.StreamHandler(sys.stderr)
    handler.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s %(levelname)-8s%(reset)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        log_colors={
            "DEBUG":    "cyan",
            "INFO":     "green",
            "WARNING":  "yellow",
            "ERROR":    "red",
            "CRITICAL": "bold_red",
        },
    ))
    root.addHandler(handler)


_setup_logging()

mcp = FastMCP("awto-riden", instructions="""
Control a Riden RD60xx series power supply (RD6006 / RD6012 / RD6018 / RD6024).

Safety rules:
- Always call rd_status() first to confirm current state
- Disable output (rd_output(on=False)) before large voltage changes
- Check protect field — if OVP/OCP is set, clear fault before re-enabling
- Use rd_power_cycle() to safely reset load without touching setpoints
- rd_log_status() writes JSONL; call rd_log_stop() when done
""")

# Shared worker instance
_worker: RidenWorker | None = None


def _ensure_connected() -> RidenWorker:
    """Check that PSU is connected."""
    if _worker is None or not hasattr(_worker, '_psu') or _worker._psu is None:
        raise RuntimeError("PSU not connected. Restart server with --port /dev/ttyUSB0")
    return _worker


@mcp.tool()
def rd_status() -> dict[str, Any]:
    """Get current PSU state: voltage, current, output status, etc."""
    try:
        return _ensure_connected().status()
    except Exception as e:
        raise RuntimeError(f"status failed: {e}") from e


@mcp.tool()
def rd_set_voltage(volts: float) -> dict[str, Any]:
    """Set output voltage (must be within PSU range, e.g. 0–60V for RD6006)."""
    try:
        worker = _ensure_connected()
        worker.set_voltage(volts)
        return worker.status()
    except Exception as e:
        raise RuntimeError(f"set_voltage({volts}) failed: {e}") from e


@mcp.tool()
def rd_set_current(amps: float) -> dict[str, Any]:
    """Set current limit (must be within PSU range, e.g. 0–6A for RD6006)."""
    try:
        worker = _ensure_connected()
        worker.set_current(amps)
        return worker.status()
    except Exception as e:
        raise RuntimeError(f"set_current({amps}) failed: {e}") from e


@mcp.tool()
def rd_output(on: bool) -> dict[str, Any]:
    """Enable or disable the PSU output."""
    try:
        worker = _ensure_connected()
        worker.set_output(on)
        return worker.status()
    except Exception as e:
        raise RuntimeError(f"output({on}) failed: {e}") from e


@mcp.tool()
def rd_set_ovp(volts: float) -> dict[str, Any]:
    """Set over-voltage protection threshold. PSU cuts output if v_out exceeds this."""
    try:
        worker = _ensure_connected()
        worker.set_ovp(volts)
        return {"ok": True}
    except Exception as e:
        raise RuntimeError(f"set_ovp({volts}) failed: {e}") from e


@mcp.tool()
def rd_set_ocp(amps: float) -> dict[str, Any]:
    """Set over-current protection threshold. PSU cuts output if i_out exceeds this."""
    try:
        worker = _ensure_connected()
        worker.set_ocp(amps)
        return {"ok": True}
    except Exception as e:
        raise RuntimeError(f"set_ocp({amps}) failed: {e}") from e


@mcp.tool()
def rd_power_cycle(seconds: float = 2.0) -> dict[str, Any]:
    """Turn output off, wait N seconds, then turn it back on."""
    try:
        worker = _ensure_connected()
        worker.power_cycle(seconds)
        return worker.status()
    except Exception as e:
        raise RuntimeError(f"power_cycle({seconds}) failed: {e}") from e


@mcp.tool()
def rd_log_status(path: str = "/tmp/riden.log", interval_ms: int = 1000) -> dict[str, Any]:
    """Start logging PSU status to file. Call rd_log_stop() to stop."""
    try:
        worker = _ensure_connected()
        worker.log_start(path, interval_ms)
        return {"ok": True}
    except Exception as e:
        raise RuntimeError(f"log_status failed: {e}") from e


@mcp.tool()
def rd_log_stop() -> dict[str, Any]:
    """Stop PSU status logging."""
    try:
        worker = _ensure_connected()
        worker.log_stop()
        return {"ok": True}
    except Exception as e:
        raise RuntimeError(f"log_stop failed: {e}") from e


@mcp.tool()
def rd_daemon_info() -> dict[str, Any]:
    """Get process health: PID, memory, CPU, threads, etc."""
    try:
        return _ensure_connected().info()
    except Exception as e:
        raise RuntimeError(f"info failed: {e}") from e


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _worker

    parser = argparse.ArgumentParser(
        description="MCP server for Riden RD60xx power supplies (direct serial, no daemon)",
        prog="mcp_server",
    )
    parser.add_argument(
        "--port",
        default="/dev/ttyUSB0",
        help="Serial port (default: /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Baud rate (default: 115200)",
    )
    parser.add_argument(
        "--address",
        type=int,
        default=1,
        help="Modbus slave address (default: 1)",
    )
    args = parser.parse_args()

    # Open serial connection
    try:
        _worker = RidenWorker(port=args.port, baud=args.baud, address=args.address)
        _worker.open()
        log.info("connected to PSU on %s (baud=%d addr=%d)", args.port, args.baud, args.address)
    except Exception as e:
        log.error("failed to open PSU: %s", e)
        sys.exit(1)

    # Run MCP server
    mcp.run()


if __name__ == "__main__":
    main()
