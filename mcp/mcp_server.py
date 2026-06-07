"""
awto-riden MCP server — direct serial, multi-PSU.

ACTIVE: re-enabled and wired into VS Code via .vscode/mcp.json. Verified
end-to-end — all tools register and rd_discover/connect/firmware/status work
against a live RK6006. (The CLI, awto_riden.py, remains the primary path and
talks to RidenWorker directly.) The separate socket-server / multi-instrument
"device hub" idea stays parked under issue #7 — that is not this stdio server.

Exposes Riden RD60xx power supply control as MCP tools for Copilot / AI agents.
Supports zero-config autodiscovery at startup, while still allowing explicit
single-PSU or multi-PSU CLI configuration when needed.

Default startup (autodiscover all likely serial ports, register found PSUs
disconnected until the user approves them with rd_connect):
        python3 mcp/mcp_server.py

Single PSU (explicit, backward compatible):
        python3 mcp/mcp_server.py --port /dev/ttyUSB0 --baud 115200 --name bench

Multiple PSUs (explicit):
        python3 mcp/mcp_server.py \
                --psu bench:/dev/ttyUSB0:115200:1 \
                --psu mr11:/dev/ttyUSB1:115200:1

To re-enable VS Code discovery, copy mcp/mcp.json back to .vscode/mcp.json and
point args at "${workspaceFolder}/mcp/mcp_server.py".

All tools accept an optional 'psu' parameter (default "default" or first PSU).
Use rd_list_psus() to see available PSUs and approval/connection state.
Use rd_disconnect()/rd_connect() to release/reacquire the serial port — useful
when you need to run a standalone script that requires exclusive port access.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from typing import Any

# Parked under ./mcp/, but the core modules (riden_daemon, riden_transport,
# protocol) live in the repo root. Put the repo root on sys.path so the sibling
# imports below resolve regardless of the working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import colorlog
from mcp.server.fastmcp import FastMCP
from riden_transport import find_riden_port, list_serial_ports

from protocol import ERR_INTERNAL, ERR_INVALID_ARG, ERR_IO, ERR_NOT_CONNECTED, ERR_TIMEOUT
from riden_daemon import RidenWorker, discover_devices

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
Control one or more Riden RD60xx series power supplies (RD6006 / RD6012 / RD6018 / RD6024).

FIRST USE — PSU approval flow:
1. Call rd_list_psus() to see all discovered PSUs.
2. If any PSU has needs_approval=True, show the list to the user and ask which
   PSU(s) they want to use (allow) and which to ignore (disallow).
3. For each approved PSU call rd_connect(psu=NAME) to open the serial port.
4. Disallowed PSUs simply stay disconnected — no action needed.
5. Once at least one PSU is connected you can proceed with normal tool calls.

Safety rules:
- Always call rd_status() first to confirm current state
- Disable output (rd_output(on=False)) before large voltage changes
- Check protect field — if OVP/OCP is set, clear fault before re-enabling
- Use rd_power_cycle() to safely reset load without touching setpoints
- rd_log_status() writes JSONL; call rd_log_stop() when done

CC mode guidance:
- cv_cc="CC" immediately after output turn-on is NORMAL for capacitive or inductive loads
  (LED drivers, motor windings, bulbs, capacitor-input filters). The PSU is current-limiting
  during inrush. It will transition to CV automatically once the load settles.
- Do NOT treat CC mode as a fault unless it persists beyond the expected inrush window.
- To observe the inrush transition, use rd_log_current() — it samples V_OUT and I_OUT
  as fast as the Modbus link allows. Practical floor is ~35 ms (USB latency timer default);
  reduce to ~5 ms by writing 1 to /sys/bus/usb-serial/devices/ttyUSB0/latency_timer.
""")

# ---------------------------------------------------------------------------
# PSU registry — populated at startup
# ---------------------------------------------------------------------------

_workers: dict[str, RidenWorker] = {}  # name → worker
_default_psu: str = "default"          # name used when psu arg is omitted
_auto_discovered: set[str] = set()     # PSU names found via autodiscovery (need user approval)


def _resolve(psu: str) -> RidenWorker:
    """Return the named worker, falling back to the default if psu=="default"."""
    name = psu if psu and psu != "default" else _default_psu
    w = _workers.get(name)
    if w is None:
        available = list(_workers.keys())
        raise RuntimeError(
            f"PSU '{name}' not found. Available: {available}. "
            "Use rd_list_psus() to see all registered PSUs."
        )
    return w


def _ensure_connected(psu: str = "default") -> RidenWorker:
    """Return a connected worker for the named PSU."""
    w = _resolve(psu)
    if not w.is_connected:
        raise RuntimeError(
            f"PSU '{w.name}' is disconnected. "
            "Call rd_connect(psu='{w.name}') to reconnect."
        )
    return w


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


def _raise_tool_error(op: str, exc: Exception) -> None:
    code = _error_code(exc)
    raise RuntimeError(f"[{code}] {op} failed: {exc}") from exc


# ---------------------------------------------------------------------------
# PSU management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def rd_list_psus() -> dict[str, Any]:
    """List all registered PSUs, their ports, and connection state.

    Returns a dict of name → {port, baud, address, connected, needs_approval}.
    PSUs with needs_approval=True were found by autodiscovery but are not yet
    connected — call rd_connect(psu=NAME) for each one the user approves.
    Use the 'psu' parameter on any other tool to target a specific PSU.
    """
    psus = {}
    pending = []
    for name, w in _workers.items():
        needs_approval = name in _auto_discovered and not w.is_connected
        psus[name] = {
            "port":          w._port,
            "baud":          w._baud,
            "address":       w._address,
            "connected":     w.is_connected,
            "needs_approval": needs_approval,
        }
        if needs_approval:
            pending.append(name)
    result: dict[str, Any] = {
        "psus":    psus,
        "default": _default_psu,
    }
    if pending:
        result["action_required"] = (
            f"The following PSUs were auto-discovered but are not connected. "
            f"Ask the user which to use, then call rd_connect(psu=NAME) for each approved PSU: "
            + ", ".join(pending)
        )
    return result


@mcp.tool()
def rd_discover_devices(
    addresses: str = "1,2,3,4,5",
    baud: int = 115200,
    timeout_s: float = 0.5,
    retries: int = 3,
    ports: str = "",
    include_errors: bool = False,
) -> dict[str, Any]:
    """Discover reachable PSUs across serial ports and Modbus addresses.

    This scan does not require preconfigured PSU names and is intended for
    plug-and-play discovery when you do not know the active port/address.

    Args:
        addresses:      Comma-separated addresses to probe (default: "1,2,3,4,5")
        baud:           Probe baud rate (default: 115200)
        timeout_s:      Read timeout per probe in seconds (default: 0.5)
        retries:        Retries per probe (default: 3)
        ports:          Optional comma-separated ports to scan (default: likely PSU ports ttyUSB/ttyACM/rfcomm)
        include_errors: Include failed attempts in response (default: False)
    """
    try:
        addr_list = [int(x.strip()) for x in addresses.split(",") if x.strip()]
        port_list = [x.strip() for x in ports.split(",") if x.strip()] if ports else None
        return discover_devices(
            ports=port_list,
            baud=baud,
            addresses=addr_list,
            timeout_s=timeout_s,
            retries=retries,
            include_errors=include_errors,
        )
    except Exception as e:
        _raise_tool_error("discover_devices", e)


@mcp.tool()
def rd_disconnect(psu: str = "default") -> dict[str, Any]:
    """Release the serial port for the named PSU.

    Closes the Modbus/serial connection so that another process (e.g. a
    standalone test script) can take exclusive port access. Use rd_connect()
    to reconnect afterwards.

    Args:
        psu: PSU name (default: the default PSU). Use rd_list_psus() to see names.
    """
    try:
        w = _resolve(psu)
        w.close()
        return {"ok": True, "psu": w.name, "port": w._port, "connected": False}
    except Exception as e:
        _raise_tool_error(f"disconnect({psu})", e)


@mcp.tool()
def rd_connect(psu: str = "default") -> dict[str, Any]:
    """Reopen the serial port for the named PSU after rd_disconnect().

    Args:
        psu: PSU name (default: the default PSU). Use rd_list_psus() to see names.
    """
    try:
        w = _resolve(psu)
        if w.is_connected:
            return {"ok": True, "psu": w.name, "already_connected": True}
        w.open()
        return {"ok": True, "psu": w.name, "port": w._port, "connected": True}
    except Exception as e:
        _raise_tool_error(f"connect({psu})", e)


@mcp.tool()
def rd_all_off() -> dict[str, Any]:
    """Turn output OFF on every connected PSU. Safe emergency stop for the lab.

    Skips disconnected PSUs (logs a warning). Returns per-PSU results.
    """
    results = {}
    for name, w in _workers.items():
        if not w.is_connected:
            results[name] = {"skipped": True, "reason": "disconnected"}
            continue
        try:
            r = w.set_output(False)
            results[name] = {"ok": True, "output": r.get("output")}
        except Exception as exc:
            log.error("rd_all_off: PSU '%s' error: %s", name, exc)
            results[name] = {"ok": False, "error": str(exc)}
    return {"results": results}


# ---------------------------------------------------------------------------
# Per-PSU control tools  (all accept optional psu= param)
# ---------------------------------------------------------------------------

@mcp.tool()
def rd_status(psu: str = "default") -> dict[str, Any]:
    """Get current PSU state: voltage, current, output status, etc.

    Args:
        psu: PSU name (default: the default PSU). Use rd_list_psus() to see names.
    """
    try:
        return _ensure_connected(psu).status()
    except Exception as e:
        _raise_tool_error("status", e)


@mcp.tool()
def rd_capabilities(psu: str = "default") -> dict[str, Any]:
    """Get supported commands, error codes, and transport capabilities.

    Args:
        psu: PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).capabilities()
    except Exception as e:
        _raise_tool_error("capabilities", e)


@mcp.tool()
def rd_firmware(psu: str = "default") -> dict[str, Any]:
    """Read device identity and firmware version from the PSU.

    Returns the model type, device ID, serial number, firmware version,
    and a note on the latest known firmware for that model (based on
    tjko/riden-flashtool supported models list, last reviewed 2026-05).

    Args:
        psu: PSU name (default: the default PSU).
    """
    # Latest known firmware versions per model (source: tjko/riden-flashtool README
    # https://github.com/tjko/riden-flashtool — cross-referenced with UniSoft custom fw).
    # Firmware integer encoding: 132 → v1.32
    _KNOWN_LATEST: dict[str, int] = {
        "RD6006":  132,
        "RD6006P": 112,
        "RD6012":  109,
        "RD6012P": 114,
        "RD6018":  112,
        "RD6018W": 112,
        "RD6024":  113,
        "RK6006":  109,  # v1.09 application fw confirmed on device SN 00001036
    }

    try:
        w = _ensure_connected(psu)
        with w._lock:
            p = w._assert_connected()
            # Re-read id/sn/fw fresh (registers 0-3, single burst)
            data = p.read(0, 4)
            fw_raw   = int(data[3])
            model_id = int(data[0])
            sn_h     = int(data[1])
            sn_l     = int(data[2])
            sn       = "%08d" % (sn_h << 16 | sn_l)
            model    = getattr(p, "type", None) or "unknown"

        fw_str = f"v{fw_raw // 100}.{fw_raw % 100:02d}"
        latest = _KNOWN_LATEST.get(model)
        if latest is None:
            up_to_date = None
            note = f"No known-latest version on record for model '{model}'."
        elif fw_raw >= latest:
            up_to_date = True
            note = f"Firmware is current ({fw_str})."
        else:
            up_to_date = False
            latest_str = f"v{latest // 100}.{latest % 100:02d}"
            note = (
                f"Newer firmware may be available: {latest_str}. "
                "Check https://github.com/tjko/riden-flashtool for the latest image."
            )

        return {
            "model":       model,
            "device_id":   model_id,
            "serial":      sn,
            "fw_raw":      fw_raw,
            "fw":          fw_str,
            "up_to_date":  up_to_date,
            "note":        note,
        }
    except Exception as e:
        _raise_tool_error("rd_firmware", e)


@mcp.tool()
def rd_set_voltage(volts: float, psu: str = "default") -> dict[str, Any]:
    """Set output voltage (must be within PSU range, e.g. 0–60V for RD6006).

    Args:
        volts: Target voltage.
        psu:   PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).set_voltage(volts)
    except Exception as e:
        _raise_tool_error(f"set_voltage({volts})", e)


@mcp.tool()
def rd_set_current(amps: float, psu: str = "default") -> dict[str, Any]:
    """Set current limit (must be within PSU range, e.g. 0–6A for RD6006).

    Args:
        amps: Current limit in amps.
        psu:  PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).set_current(amps)
    except Exception as e:
        _raise_tool_error(f"set_current({amps})", e)


@mcp.tool()
def rd_output(on: bool, psu: str = "default") -> dict[str, Any]:
    """Enable or disable the PSU output.

    After turning ON, cv_cc may read 'CC' briefly — this is normal inrush
    behaviour for LED drivers, bulbs, or capacitive loads. Wait a moment and
    re-poll; it will switch to 'CV' once the load settles.

    Args:
        on:  True to enable output, False to disable.
        psu: PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).set_output(on)
    except Exception as e:
        _raise_tool_error(f"output({on})", e)


@mcp.tool()
def rd_set_ovp(volts: float, psu: str = "default") -> dict[str, Any]:
    """Set over-voltage protection threshold. PSU cuts output if v_out exceeds this.

    Args:
        volts: OVP threshold.
        psu:   PSU name (default: the default PSU).
    """
    try:
        _ensure_connected(psu).set_ovp(volts)
        return {"ok": True}
    except Exception as e:
        _raise_tool_error(f"set_ovp({volts})", e)


@mcp.tool()
def rd_set_ocp(amps: float, psu: str = "default") -> dict[str, Any]:
    """Set over-current protection threshold. PSU cuts output if i_out exceeds this.

    Args:
        amps: OCP threshold.
        psu:  PSU name (default: the default PSU).
    """
    try:
        _ensure_connected(psu).set_ocp(amps)
        return {"ok": True}
    except Exception as e:
        _raise_tool_error(f"set_ocp({amps})", e)


@mcp.tool()
def rd_power_cycle(seconds: float = 2.0, psu: str = "default") -> dict[str, Any]:
    """Turn output off, wait N seconds, then turn it back on.

    Args:
        seconds: Off duration (default 2.0).
        psu:     PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).power_cycle(seconds)
    except Exception as e:
        _raise_tool_error(f"power_cycle({seconds})", e)


@mcp.tool()
def rd_log_status(
    path: str = "/tmp/riden.log",
    interval_ms: int = 1000,
    psu: str = "default",
) -> dict[str, Any]:
    """Start logging PSU status to file. Call rd_log_stop() to stop.

    Args:
        path:        Output file path.
        interval_ms: Poll interval in ms.
        psu:         PSU name (default: the default PSU).
    """
    try:
        worker = _ensure_connected(psu)
        worker.log_start(path, interval_ms)
        return {"ok": True}
    except Exception as e:
        _raise_tool_error("log_status", e)


@mcp.tool()
def rd_log_stop(psu: str = "default") -> dict[str, Any]:
    """Stop PSU logging and return a summary of the captured session.

    Works for both rd_log_status and rd_log_current sessions.
    Returns sample count, duration, peak/avg current, avg voltage, and total Wh
    (stats are only populated if the log file is readable).

    Args:
        psu: PSU name (default: the default PSU).
    """
    try:
        worker = _ensure_connected(psu)
        return worker.log_stop()
    except Exception as e:
        _raise_tool_error("log_stop", e)


@mcp.tool()
def rd_log_current(
    path: str = "/tmp/riden_current.log",
    interval_ms: int = 100,
    v_thresh: float = 0.01,
    i_thresh: float = 0.005,
    psu: str = "default",
) -> dict[str, Any]:
    """Start fast current+voltage logging to a JSONL file.

    Reads only V_OUT and I_OUT registers in one Modbus call — much faster than
    a full status poll. Good for monitoring inrush, CC→CV transitions, and
    plotting load current over time.

    Only writes a row when V or I changes by more than the threshold — keeps
    files small during steady-state (e.g. stable 12 V / 0.55 A writes nothing).

    Each line: {"ts": <unix seconds>, "v_out": <V>, "i_out": <A>}

    Args:
        path:        Output file path (default /tmp/riden_current.log, appended)
        interval_ms: Poll interval in ms (default 100). Practical floor ~35 ms.
        v_thresh:    Min voltage change to write a row (default 0.01 V)
        i_thresh:    Min current change to write a row (default 0.005 A)
        psu:         PSU name (default: the default PSU).

    Call rd_log_stop() to stop and get a summary.
    Call rd_log_retrieve() to peek at stats without stopping.
    """
    try:
        worker = _ensure_connected(psu)
        worker.log_current_start(path, interval_ms, v_thresh, i_thresh)
        return {"ok": True, "path": path, "interval_ms": interval_ms,
                "v_thresh": v_thresh, "i_thresh": i_thresh}
    except Exception as e:
        _raise_tool_error("log_current", e)


@mcp.tool()
def rd_log_retrieve(
    path: str = "/tmp/riden_current.log",
    max_rows: int = 0,
    psu: str = "default",
) -> dict[str, Any]:
    """Retrieve stats (and optionally data) from a current log file.

    Safe to call while logging is still running — reads the file non-destructively.
    Returns summary stats by default (LLM-safe). Set max_rows > 0 to get a
    downsampled columnar slice for plotting or analysis.

    Args:
        path:     JSONL file written by rd_log_current (default /tmp/riden_current.log)
        max_rows: Max rows to return in columnar format. 0 = stats only (default).
        psu:      PSU name (default: the default PSU).
    """
    try:
        worker = _ensure_connected(psu)
        return worker.log_retrieve(path, max_rows)
    except Exception as e:
        _raise_tool_error("log_retrieve", e)


@mcp.tool()
def rd_sine_wave(
    v_center: float = 6.0,
    v_amplitude: float = 6.0,
    freq_hz: float = 0.1,
    duration_s: float = 60.0,
    step_s: float = 0.5,
    psu: str = "default",
) -> dict[str, Any]:
    """Drive a sine-wave voltage profile on the PSU output.

    Turns output ON, sweeps voltage as a sine wave for duration_s seconds,
    then turns output OFF. Blocks until complete.

    Args:
        v_center:    Centre voltage of the sine wave (default 6.0 V)
        v_amplitude: Peak deviation from centre (default 6.0 V → 0..12 V swing)
        freq_hz:     Frequency in Hz (default 0.1 = 10 s period)
        duration_s:  Total run time in seconds (default 60)
        step_s:      Seconds between voltage writes (default 0.5)
        psu:         PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).sine_wave(
            v_center=v_center,
            v_amplitude=v_amplitude,
            freq_hz=freq_hz,
            duration_s=duration_s,
            step_s=step_s,
        )
    except Exception as e:
        _raise_tool_error("sine_wave", e)


@mcp.tool()
def rd_waveform(
    shape: str = "sine",
    v_center: float = 6.0,
    v_amplitude: float = 6.0,
    freq_hz: float = 0.1,
    duration_s: float = 60.0,
    step_s: float = 0.5,
    duty_cycle: float = 0.5,
    psu: str = "default",
) -> dict[str, Any]:
    """Drive a generator-style waveform (sine/triangle/sawtooth/square).

    Turns output ON, runs the waveform for duration_s seconds, then turns output OFF.

    Args:
        psu: PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).waveform(
            shape=shape,
            v_center=v_center,
            v_amplitude=v_amplitude,
            freq_hz=freq_hz,
            duration_s=duration_s,
            step_s=step_s,
            duty_cycle=duty_cycle,
        )
    except Exception as e:
        _raise_tool_error("waveform", e)


@mcp.tool()
def rd_inrush_capture(
    voltage: float = 12.0,
    max_current: float = 6.0,
    duration_s: float = 4.0,
    path: str = "/tmp/mr11_inrush.jsonl",
    psu: str = "default",
) -> dict[str, Any]:
    """Capture start-up inrush current for a lamp or capacitive load.

    Turns output OFF, programs VSET/ISET, turns ON, then samples V_OUT+I_OUT
    as fast as the Modbus link allows for duration_s seconds. Turns output OFF
    when done. Writes JSONL to path.

    Args:
        voltage:     Output voltage to apply (default 12 V)
        max_current: Current limit — set to PSU max (6 A for RK6006) to catch
                     full inrush without the PSU clipping early (default 6 A)
        duration_s:  Capture window in seconds (default 4)
        path:        Output JSONL file (default /tmp/mr11_inrush.jsonl)
        psu:         PSU name (default: the default PSU).

    Returns summary: sample count, rate, peak_i_a, peak_i_ms (ms after turn-on).
    Follow with rd_plot_results() to generate a chart.
    """
    try:
        return _ensure_connected(psu).inrush_capture(
            voltage=voltage,
            max_current=max_current,
            duration_s=duration_s,
            path=path,
        )
    except Exception as e:
        _raise_tool_error("inrush_capture", e)


@mcp.tool()
def rd_vsweep(
    max_current: float = 6.0,
    v_max: float = 15.0,
    v_step: float = 0.25,
    dwell_ms: int = 300,
    path: str = "/tmp/mr11_vsweep.jsonl",
    psu: str = "default",
) -> dict[str, Any]:
    """Sweep output voltage 0 → v_max and record V+I at each step.

    Produces the load's VI characteristic curve and wattage. Turns output ON
    at 0 V, steps up to v_max, then turns off. Writes JSONL to path.

    Args:
        max_current: Current limit (default 6 A — set high so the PSU doesn't
                     clip your lamp characterisation at low voltages)
        v_max:       Maximum voltage to sweep to (default 15 V)
        v_step:      Step size in volts (default 0.25 V)
        dwell_ms:    Settle time per step in ms (default 300)
        path:        Output JSONL file (default /tmp/mr11_vsweep.jsonl)
        psu:         PSU name (default: the default PSU).

    Returns summary. Follow with rd_plot_results() to generate a chart.
    """
    try:
        return _ensure_connected(psu).vsweep(
            max_current=max_current,
            v_max=v_max,
            v_step=v_step,
            dwell_ms=dwell_ms,
            path=path,
        )
    except Exception as e:
        _raise_tool_error("vsweep", e)


@mcp.tool()
def rd_plot_results(
    inrush_path: str = "/tmp/mr11_inrush.jsonl",
    vsweep_path: str = "/tmp/mr11_vsweep.jsonl",
    current_log_path: str = "",
    out_png: str = "/tmp/mr11_characterisation.png",
    title: str = "Load Characterisation",
    open_viewer: bool = False,
) -> dict[str, Any]:
    """Plot inrush and/or VI sweep results from JSONL files into a PNG chart.

    Reads JSONL files produced by rd_inrush_capture(), rd_vsweep(), and/or
    rd_log_current(). Missing or empty files are skipped. Saves PNG and returns the path.

    rd_log_current data produces a layered time-series panel showing V_out, I_out,
    CV/CC mode, protection state, and output on/off — all on a shared time axis.

    Args:
        inrush_path:      JSONL from rd_inrush_capture (default /tmp/mr11_inrush.jsonl)
        vsweep_path:      JSONL from rd_vsweep (default /tmp/mr11_vsweep.jsonl)
        current_log_path: JSONL from rd_log_current (default empty = skip)
        out_png:          Output PNG path (default /tmp/mr11_characterisation.png)
        title:            Chart title (default "Load Characterisation")
        open_viewer:      Open PNG with xdg-open after saving (default False)
    """
    import subprocess
    from pathlib import Path

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
        import numpy as np
    except ImportError:
        raise RuntimeError(
            "matplotlib not installed — run: pip install matplotlib numpy"
        )

    ip = Path(inrush_path)
    vp = Path(vsweep_path)
    cp = Path(current_log_path) if current_log_path else None

    def _load(p: Path) -> list[dict]:
        if not p.exists():
            return []
        lines = [l.strip() for l in p.read_text().splitlines() if l.strip()]
        return [__import__("json").loads(l) for l in lines]

    inrush_rows  = _load(ip)
    vsweep_rows  = _load(vp)
    current_rows = _load(cp) if cp else []

    panels = sum([bool(inrush_rows), bool(vsweep_rows), bool(current_rows)])
    if panels == 0:
        raise RuntimeError("No data found in any file")

    fig = plt.figure(figsize=(12, 5 * panels), tight_layout=True)
    fig.suptitle(title, fontsize=14, fontweight="bold")
    gs = gridspec.GridSpec(panels, 1, figure=fig, hspace=0.4)
    panel = 0

    # --- Current log panel (time-series with state overlay) ---
    if current_rows:
        t0   = current_rows[0]["ts"]
        t    = np.array([r["ts"] - t0    for r in current_rows])
        v    = np.array([r["v_out"]       for r in current_rows])
        i    = np.array([r["i_out"]       for r in current_rows])

        # Estimate actual sample rate
        dt_med = float(np.median(np.diff(t))) if len(t) > 1 else 0
        rate_hz = round(1.0 / dt_med, 1) if dt_med > 0 else 0

        ax = fig.add_subplot(gs[panel])
        ax.set_title(
            f"Current Log — V & I vs Time  (actual poll rate ≈ {rate_hz} Hz, "
            f"Δt median {dt_med*1000:.0f} ms)",
            pad=8,
        )
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Current (A)", color="#d62728")
        ax.tick_params(axis="y", labelcolor="#d62728")
        ax.plot(t, i, color="#d62728", linewidth=1.0, label="I_out (A)")
        ax.set_ylim(bottom=0)

        ax2 = ax.twinx()
        ax2.set_ylabel("Voltage (V)", color="#1f77b4")
        ax2.tick_params(axis="y", labelcolor="#1f77b4")
        ax2.plot(t, v, color="#1f77b4", linewidth=1.2, alpha=0.85, label="V_out (V)")

        # Shade CC regions (cv_cc field if present)
        if "cv_cc" in current_rows[0]:
            cc_on = np.array([r.get("cv_cc", "CV") == "CC" for r in current_rows])
            # Draw shaded bands for CC regions
            in_cc = False
            t_start = 0.0
            for idx, val in enumerate(cc_on):
                if val and not in_cc:
                    t_start = t[idx]
                    in_cc = True
                elif not val and in_cc:
                    ax.axvspan(t_start, t[idx], alpha=0.15, color="orange", label="CC mode")
                    in_cc = False
            if in_cc:
                ax.axvspan(t_start, t[-1], alpha=0.15, color="orange", label="CC mode")

        # Mark OVP/OCP events
        if "protect" in current_rows[0]:
            for r, ti in zip(current_rows, t):
                p = r.get("protect", "none")
                if p and p != "none":
                    ax.axvline(ti, color="red", linewidth=1.5, linestyle="--", alpha=0.8)
                    ax.text(ti, ax.get_ylim()[1] * 0.95, p, color="red", fontsize=7,
                            ha="center", va="top")

        # Mark output-off events
        if "output" in current_rows[0]:
            for r, ti in zip(current_rows, t):
                if not r.get("output", True):
                    ax.axvline(ti, color="black", linewidth=1.0, linestyle=":", alpha=0.6)

        # Legend (deduplicate)
        handles, labels = [], []
        for h, l in zip(*ax.get_legend_handles_labels()):
            if l not in labels:
                handles.append(h); labels.append(l)
        for h, l in zip(*ax2.get_legend_handles_labels()):
            if l not in labels:
                handles.append(h); labels.append(l)
        ax.legend(handles, labels, loc="upper right", fontsize=8)
        panel += 1

    if inrush_rows:
        t = np.array([r["t"]     for r in inrush_rows])
        v = np.array([r["v_out"] for r in inrush_rows])
        i = np.array([r["i_out"] for r in inrush_rows])

        ax = fig.add_subplot(gs[panel])
        ax.set_title("Start-up Inrush — Current & Voltage vs Time", pad=8)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Current (A)", color="#d62728")
        ax.tick_params(axis="y", labelcolor="#d62728")
        ax.plot(t, i, color="#d62728", linewidth=1.2, label="I_out (A)")
        ax.set_ylim(bottom=0)

        ax2 = ax.twinx()
        ax2.set_ylabel("Voltage (V)", color="#1f77b4")
        ax2.tick_params(axis="y", labelcolor="#1f77b4")
        ax2.plot(t, v, color="#1f77b4", linewidth=1.0, alpha=0.7, label="V_out (V)")
        ax2.set_ylim(bottom=0)

        i_peak = float(i.max())
        t_peak = float(t[i.argmax()])
        ax.annotate(
            f"peak {i_peak:.3f} A @ {t_peak*1000:.0f} ms",
            xy=(t_peak, i_peak),
            xytext=(t_peak + (t[-1] - t[0]) * 0.05, i_peak * 0.90),
            arrowprops=dict(arrowstyle="->", color="black"),
            fontsize=9,
        )
        lines1, l1 = ax.get_legend_handles_labels()
        lines2, l2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, l1 + l2, loc="upper right", fontsize=8)
        panel += 1

    if vsweep_rows:
        v = np.array([r["v_out"] for r in vsweep_rows])
        i = np.array([r["i_out"] for r in vsweep_rows])
        p = v * i

        ax = fig.add_subplot(gs[panel])
        ax.set_title("VI Characteristic (voltage sweep)", pad=8)
        ax.set_xlabel("Output Voltage (V)")
        ax.set_ylabel("Current (A)", color="#d62728")
        ax.tick_params(axis="y", labelcolor="#d62728")
        ax.plot(v, i, "o-", color="#d62728", markersize=3, linewidth=1.2, label="I (A)")
        ax.set_ylim(bottom=0)

        ax2 = ax.twinx()
        ax2.set_ylabel("Power (W)", color="#2ca02c")
        ax2.tick_params(axis="y", labelcolor="#2ca02c")
        ax2.plot(v, p, "s--", color="#2ca02c", markersize=3, linewidth=1.0,
                 alpha=0.8, label="P (W)")
        ax2.set_ylim(bottom=0)

        lines1, l1 = ax.get_legend_handles_labels()
        lines2, l2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, l1 + l2, loc="upper left", fontsize=8)

    plt.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    if open_viewer:
        try:
            subprocess.Popen(["xdg-open", out_png])
        except Exception:
            pass

    return {"ok": True, "png": out_png, "inrush_samples": len(inrush_rows),
            "vsweep_steps": len(vsweep_rows), "current_log_samples": len(current_rows)}


@mcp.tool()
def rd_list_parameters(psu: str = "default") -> dict[str, Any]:
    """List model/firmware-supported PSU parameters and access mode.

    Args:
        psu: PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).list_parameters()
    except Exception as e:
        _raise_tool_error("list_parameters", e)


@mcp.tool()
def rd_get_parameter(name: str, psu: str = "default") -> dict[str, Any]:
    """Read a parameter by logical name from the connected PSU.

    Args:
        name: Parameter name.
        psu:  PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).get_parameter(name)
    except Exception as e:
        _raise_tool_error(f"get_parameter({name})", e)


@mcp.tool()
def rd_set_parameter(name: str, value: Any, psu: str = "default") -> dict[str, Any]:
    """Set a writable PSU parameter by logical name (model-aware).

    Args:
        name:  Parameter name.
        value: New value.
        psu:   PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).set_parameter(name, value)
    except Exception as e:
        _raise_tool_error(f"set_parameter({name})", e)


@mcp.tool()
def rd_beep(on: bool = True, psu: str = "default") -> dict[str, Any]:
    """Best-effort buzzer control if the connected model/firmware supports it.

    Args:
        on:  True to beep, False to stop.
        psu: PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).beep(on)
    except Exception as e:
        _raise_tool_error("beep", e)


@mcp.tool()
def rd_modbus_read_holding(
    start_register: int, count: int = 1, psu: str = "default",
) -> dict[str, Any]:
    """Read raw Modbus holding registers (FC03) for advanced/unsupported params.

    Args:
        start_register: First register to read.
        count:          Number of registers (default 1).
        psu:            PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).modbus_read_holding(start_register, count)
    except Exception as e:
        _raise_tool_error("modbus_read_holding", e)


@mcp.tool()
def rd_modbus_write_register(
    register: int, value: int, psu: str = "default",
) -> dict[str, Any]:
    """Write raw Modbus single register (FC06) for advanced/unsupported params.

    Args:
        register: Register address.
        value:    Integer value to write.
        psu:      PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).modbus_write_register(register, value)
    except Exception as e:
        _raise_tool_error("modbus_write_register", e)


@mcp.tool()
def rd_register_scan(
    start: int = 0,
    end: int = 300,
    batch: int = 50,
    skip_zero: bool = True,
    psu: str = "default",
) -> dict[str, Any]:
    """Scan a Modbus register range and annotate known/unknown addresses.

    Reads registers [start, end) in batches and cross-references the known
    Riden register map.  Highlights undocumented registers that hold non-zero
    values — useful for discovering firmware-specific hidden registers.

    The default range (0–300) covers all documented registers plus the gap
    to SYSTEM (256) and a safety margin.

    Args:
        start:     First register address (default 0).
        end:       One past the last address to scan (default 300).
        batch:     Registers per read request, 1–125 (default 50).
        skip_zero: Omit registers whose value is 0 from the full list
                   (unknown_nonzero always shows undocumented non-zero hits).
                   Default True to keep output concise.
        psu:       PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).register_scan(start, end, batch, skip_zero)
    except Exception as e:
        _raise_tool_error("register_scan", e)


@mcp.tool()
def rd_diff_scan(
    start: int = 0,
    end: int = 300,
    batch: int = 50,
    output_on: bool = True,
    settle_ms: int = 500,
    psu: str = "default",
) -> dict[str, Any]:
    """Differential register scan: compare registers with output OFF vs ON.

    Scans [start, end) twice — once with output off, once with output on
    (or reversed when output_on=False) — and returns only registers that changed.
    Useful for identifying undocumented shadow/mirror registers and confirming
    which registers track live output state (voltage, current, protection flags).

    The output is restored to its original state after the scan completes.

    Args:
        start:      First register address (default 0).
        end:        One past the last address to scan (default 300).
        batch:      Registers per read request, 1–125 (default 50).
        output_on:  Second scan state: True → scan A=off then B=on (default).
                    False → scan A=on then B=off.
        settle_ms:  Wait time (ms) after toggling output before scanning (default 500).
        psu:        PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).diff_scan(start, end, batch, output_on, settle_ms)
    except Exception as e:
        _raise_tool_error("diff_scan", e)


@mcp.tool()
def rd_daemon_info(psu: str = "default") -> dict[str, Any]:
    """Get process health: PID, memory, CPU, threads, etc.

    Args:
        psu: PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).info()
    except Exception as e:
        _raise_tool_error("info", e)


@mcp.tool()
def rd_profile_serial(count: int = 20, sleep_ms: int = 100, psu: str = "default") -> dict[str, Any]:
    """Profile Modbus serial timing and return a stable polling recommendation.

    Useful for USB and Bluetooth RFCOMM links to derive cadence tuned for
    timestamp stability (not minimum RTT).

    Args:
        count:    Number of reads (default: 20).
        sleep_ms: Inter-read delay during profiling in ms (default: 100).
        psu:      PSU name (default: the default PSU).
    """
    try:
        return _ensure_connected(psu).profile_serial(count=count, sleep_ms=sleep_ms)
    except Exception as e:
        _raise_tool_error("profile_serial", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _workers, _default_psu, _auto_discovered

    parser = argparse.ArgumentParser(
        description="MCP server for Riden RD60xx power supplies (direct serial, multi-PSU)",
        prog="mcp_server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Single PSU (backward compat):
    mcp_server.py --port /dev/ttyUSB0 --baud 115200 --name bench

  Multiple PSUs:
    mcp_server.py --psu bench:/dev/ttyUSB0:115200:1 --psu lamp:/dev/ttyUSB1:115200:1

  --psu format:  NAME:PORT[:BAUD[:ADDRESS]]
  Defaults:      baud=115200, address=1
""",
    )
    # Single-PSU args (backward compat)
    parser.add_argument("--port",    default=None, help="Serial port for single PSU")
    parser.add_argument("--baud",    type=int, default=115200)
    parser.add_argument("--address", type=int, default=1)
    parser.add_argument("--name",    default="default", help="Name for single PSU (default: 'default')")
    # Multi-PSU
    parser.add_argument(
        "--psu",
        action="append",
        dest="psus",
        metavar="NAME:PORT[:BAUD[:ADDR]]",
        help="Add a named PSU. Repeatable. Overrides --port/--baud/--address.",
    )
    args = parser.parse_args()

    # Build PSU list
    psu_specs: list[tuple[str, str, int, int]] = []  # (name, port, baud, address)

    if args.psus:
        for spec in args.psus:
            parts = spec.split(":")
            if len(parts) < 2:
                log.error("Invalid --psu spec '%s': expected NAME:PORT[:BAUD[:ADDR]]", spec)
                sys.exit(1)
            p_name = parts[0]
            p_port = parts[1]
            p_baud = int(parts[2]) if len(parts) > 2 else 115200
            p_addr = int(parts[3]) if len(parts) > 3 else 1
            psu_specs.append((p_name, p_port, p_baud, p_addr))
    elif args.port:
        psu_specs.append((args.name, args.port, args.baud, args.address))
    else:
        # Auto-discover: scan all likely serial ports for Riden PSUs.
        # Fast probe (timeout=0.3s, retries=2) to keep startup time low.
        log.info("awto.mcp: no PSU configured — scanning all serial ports for Riden devices...")
        disc = discover_devices(timeout_s=0.3, retries=2)
        found = disc.get("found", [])

        if found:
            log.info(
                "awto.mcp: discovered %d PSU(s): %s",
                len(found),
                ", ".join(f"{d['model']}@{d['port']}" for d in found),
            )
            for dev in found:
                # Auto-name: model-last4ofserial e.g. rk6006-1036
                suffix = dev["serial"].lstrip("0")[-4:] or dev["serial"][-4:]
                auto_name = f"{dev['model'].lower()}-{suffix}"
                # Deduplicate if two PSUs yield the same name
                base, idx = auto_name, 2
                while base in {s[0] for s in psu_specs}:
                    base = f"{auto_name}-{idx}"
                    idx += 1
                psu_specs.append((base, dev["port"], dev["baud"], dev["address"]))
                _auto_discovered.add(base)
        else:
            # Nothing found — fall back to a single port guess so the server
            # still starts and the user can investigate.
            default_port, default_baud = find_riden_port(fallback="/dev/ttyUSB0")
            log.warning(
                "awto.mcp: no Riden devices found on any port; "
                "falling back to %s (may not connect)",
                default_port,
            )
            psu_specs.append(("default", default_port, default_baud, 1))

    _default_psu = psu_specs[0][0]

    # Open all PSUs
    # Auto-discovered PSUs start disconnected — user must approve via rd_connect().
    any_ok = False
    for (p_name, p_port, p_baud, p_addr) in psu_specs:
        w = RidenWorker(port=p_port, baud=p_baud, address=p_addr, name=p_name)
        if p_name in _auto_discovered:
            # Registered but not connected — awaiting user approval
            _workers[p_name] = w
            any_ok = True  # we have PSUs to offer
            log.info(
                "awto.mcp: registered discovered PSU '%s' on %s "
                "(disconnected — call rd_connect to activate)",
                p_name, p_port,
            )
        else:
            try:
                w.open()
                _workers[p_name] = w
                log.info("awto.mcp: connected to PSU '%s' on %s (baud=%d addr=%d)",
                         p_name, p_port, p_baud, p_addr)
                any_ok = True
            except Exception as e:
                log.error("failed to open PSU '%s' on %s: %s", p_name, p_port, e)
                # Register anyway so disconnect/connect tools work
                _workers[p_name] = w

    if not any_ok:
        log.error("No PSUs connected — exiting.")
        sys.exit(1)

    mcp.run()


if __name__ == "__main__":
    main()
