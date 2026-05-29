"""
Shared protocol helpers for awto-mcp-riden.

Transport
---------
All communication is newline-delimited JSON (JSON-Lines) over a Unix domain
socket at DEFAULT_SOCKET_PATH.  Each exchange is one request line followed by
one response line.  All commands that affect PSU state are serialised inside
the daemon; concurrent clients are safe.

Multi-PSU routing
-----------------
Every command accepts an optional "psu" key (string name, default "default").
The daemon resolves "default" to the first registered PSU.  Use list_psus to
enumerate names.

----------------------------------------------------------------------------
DAEMON LIFECYCLE
----------------------------------------------------------------------------

ping
  Request:   {"cmd": "ping"}
  Response:  {"ok": true, "response": "pong", "uptime_s": <float>}

daemon_status
  Request:   {"cmd": "daemon_status"}
  Response:  {"ok": true, "uptime_s": <float>, "pid": <int>,
               "psus": [<psu_summary>, ...]}
  psu_summary: {"name": str, "port": str, "connected": bool,
                "needs_approval": bool}

shutdown
  Request:   {"cmd": "shutdown"}
  Response:  {"ok": true, "response": "shutting down"}
  Note: daemon closes the socket and exits after sending the response.

----------------------------------------------------------------------------
PSU REGISTRY
----------------------------------------------------------------------------

list_psus
  Request:   {"cmd": "list_psus"}
  Response:  {"ok": true, "psus": [<psu_entry>, ...]}
  psu_entry: {"name": str, "port": str, "baud": int, "address": int,
               "connected": bool, "needs_approval": bool,
               "model": str|null, "firmware": str|null}

connect
  Request:   {"cmd": "connect", "psu": "default"}
  Response:  {"ok": true, "name": str, "model": str, "firmware": str}
           | {"ok": false, "error": str, "code": str}

disconnect
  Request:   {"cmd": "disconnect", "psu": "default"}
  Response:  {"ok": true, "name": str}
           | {"ok": false, "error": str, "code": str}

all_off
  Request:   {"cmd": "all_off"}
  Response:  {"ok": true, "disabled": [str, ...]}
  Note: disables output on every connected PSU; never raises on partial failure.

----------------------------------------------------------------------------
READ-ONLY QUERIES
----------------------------------------------------------------------------

status
  Request:   {"cmd": "status", "psu": "default"}
  Response:  {"ok": true, <status_fields>}

capabilities
  Request:   {"cmd": "capabilities", "psu": "default"}
  Response:  {"ok": true, "model": str, "max_v": float, "max_i": float,
               "max_p": float, "v_multi": int, "i_multi": int, "p_multi": int}

firmware
  Request:   {"cmd": "firmware", "psu": "default"}
  Response:  {"ok": true, "model": str, "id": int, "serial": str,
               "firmware": str}

profile_serial
  Request:   {"cmd": "profile_serial", "count": 20, "sleep_ms": 100,
               "psu": "default"}
  Response:  {"ok": true, "recommended_poll_ms": int, "strategy": str,
               "timing": {<timing_stats>}, ...}

list_parameters
  Request:   {"cmd": "list_parameters", "psu": "default"}
  Response:  {"ok": true, "parameters": [<param_entry>, ...]}
  param_entry: {"name": str, "value": <any>, "unit": str|null,
                "writable": bool, "description": str}

get_parameter
  Request:   {"cmd": "get_parameter", "name": str, "psu": "default"}
  Response:  {"ok": true, "name": str, "value": <any>}

modbus_read
  Request:   {"cmd": "modbus_read", "start": int, "count": 1, "psu": "default"}
  Response:  {"ok": true, "start": int, "count": int, "values": [int, ...]}

register_scan
  Request:   {"cmd": "register_scan", "start": 0, "end": 256, "batch": 8,
               "skip_zero": true, "psu": "default"}
  Response:  {"ok": true, "registers": [<reg_entry>, ...],
               "unknown_nonzero": [<reg_entry>, ...], ...}
  reg_entry: {"addr": int, "name": str, "value": int, "hex": str,
               "known": bool}

diff_scan
  Request:   {"cmd": "diff_scan", "start": 0, "end": 256, "batch": 8,
               "unknown_only": false, "output_off_first": true,
               "psu": "default"}
  Response:  {"ok": true, "changed": [<diff_entry>, ...],
               "scan_before_count": int, "scan_after_count": int}
  diff_entry: {"addr": int, "name": str, "before": int, "after": int,
                "delta": int, "known": bool}

----------------------------------------------------------------------------
WRITE COMMANDS  (all return <status_fields> on success)
----------------------------------------------------------------------------

set_voltage
  Request:   {"cmd": "set_voltage", "volts": 5.0, "psu": "default"}
  Response:  {"ok": true, <status_fields>}

set_current
  Request:   {"cmd": "set_current", "amps": 1.0, "psu": "default"}
  Response:  {"ok": true, <status_fields>}

output
  Request:   {"cmd": "output", "on": true, "psu": "default"}
  Response:  {"ok": true, <status_fields>}

set_ovp
  Request:   {"cmd": "set_ovp", "volts": 6.0, "psu": "default"}
  Response:  {"ok": true, <status_fields>}

set_ocp
  Request:   {"cmd": "set_ocp", "amps": 2.0, "psu": "default"}
  Response:  {"ok": true, <status_fields>}

power_cycle
  Request:   {"cmd": "power_cycle", "seconds": 2.0, "psu": "default"}
  Response:  {"ok": true, <status_fields>}
  Note: blocks for <seconds> inside the daemon.

set_parameter
  Request:   {"cmd": "set_parameter", "name": str, "value": <any>,
               "psu": "default"}
  Response:  {"ok": true, "name": str, "value": <any>}

beep
  Request:   {"cmd": "beep", "on": true, "psu": "default"}
  Response:  {"ok": true}

modbus_write
  Request:   {"cmd": "modbus_write", "register": int, "value": int,
               "psu": "default"}
  Response:  {"ok": true, "register": int, "value": int}
  Note: raw write — caller is responsible for safety.

----------------------------------------------------------------------------
LONG-RUNNING / STREAMING OPERATIONS
----------------------------------------------------------------------------

log_start
  Request:   {"cmd": "log_start", "path": "/tmp/riden.jsonl",
               "interval_ms": 1000, "psu": "default"}
  Response:  {"ok": true, "path": str}
  Note: daemon writes JSONL rows in background; use log_stop to end.

log_stop
  Request:   {"cmd": "log_stop", "psu": "default"}
  Response:  {"ok": true, "path": str|null, "rows": int}

log_retrieve
  Request:   {"cmd": "log_retrieve", "path": str, "max_rows": 0,
               "psu": "default"}
  Response:  {"ok": true, "path": str, "rows": int, "data": [<row>, ...]}
  row: {"ts": float, "v_out": float, "i_out": float, "p_out": float}

log_current
  Request:   {"cmd": "log_current", "samples": 50, "interval_ms": 50,
               "v_thresh": 0.0, "i_thresh": 0.0, "psu": "default"}
  Response:  {"ok": true, "samples": int, "data": [<sample>, ...]}
  sample: {"ts": float, "v_out": float, "i_out": float}
  Note: blocks until all samples are collected.

waveform
  Request:   {"cmd": "waveform", "shape": "sine", "v_center": 6.0,
               "v_amplitude": 5.0, "freq_hz": 1.0, "duration_s": 5.0,
               "step_s": 0.05, "psu": "default"}
  Response:  {"ok": true, "shape": str, "rows": int,
               "data": [<sample>, ...]}
  sample: {"ts": float, "v_set": float, "v_out": float, "i_out": float}
  Shapes: "sine" | "triangle" | "sawtooth" | "square"

inrush_capture
  Request:   {"cmd": "inrush_capture", "v_set": 12.0, "i_limit": 6.0,
               "samples": 50, "interval_ms": 10, "psu": "default"}
  Response:  {"ok": true, "samples": int, "data": [<sample>, ...]}
  sample: {"ts": float, "v_out": float, "i_out": float}

vsweep
  Request:   {"cmd": "vsweep", "v_start": 0.0, "v_end": 12.0,
               "v_step": 0.5, "dwell_s": 0.2, "psu": "default"}
  Response:  {"ok": true, "steps": int, "data": [<step>, ...]}
  step: {"v_set": float, "v_out": float, "i_out": float, "p_out": float}

----------------------------------------------------------------------------
STATUS FIELDS  (common payload returned by most write commands)
----------------------------------------------------------------------------

  v_set     float  — voltage setpoint (V)
  i_set     float  — current limit (A)
  v_out     float  — measured output voltage (V)
  i_out     float  — measured output current (A)
  p_out     float  — measured output power (W)
  v_in      float  — input voltage (V)
  output    bool   — output enabled
  cv_cc     str    — "CV" or "CC"
  protect   str    — "none", "OVP", or "OCP"
  temp_c    int    — internal temperature (°C)

----------------------------------------------------------------------------
ERROR RESPONSE
----------------------------------------------------------------------------

  {"ok": false, "error": "<human-readable message>", "code": "<ERR_CODE>"}

  Codes:
    ENOTCONN   — PSU not connected / serial port not open
    ETIMEOUT   — Modbus response timed out
    EIO        — Serial I/O error
    EINVAL     — Bad argument value
    EINTERNAL  — Unexpected internal error
"""

from __future__ import annotations

import json
import socket
from typing import Any

DEFAULT_SOCKET_PATH = "/tmp/awto-riden.sock"
DEFAULT_PID_PATH    = "/tmp/awto-riden.pid"
DEFAULT_PORT        = "/dev/ttyUSB0"
DEFAULT_BAUD        = 115200
DEFAULT_ADDRESS     = 1

# Stable error codes (aligned with sibling awto-mcp-* repos)
ERR_NOT_CONNECTED = "ENOTCONN"
ERR_TIMEOUT = "ETIMEOUT"
ERR_IO = "EIO"
ERR_INVALID_ARG = "EINVAL"
ERR_INTERNAL = "EINTERNAL"


def send_request(sock: socket.socket, req: dict[str, Any]) -> dict[str, Any]:
    """Send a JSON-lines request and return the parsed response."""
    sock.sendall((json.dumps(req) + "\n").encode())
    return recv_response(sock)


def recv_response(sock: socket.socket) -> dict[str, Any]:
    """Read one newline-terminated JSON line from *sock*."""
    buf = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("daemon closed connection")
        buf.extend(chunk)
        if b"\n" in buf:
            line, _, _ = buf.partition(b"\n")
            return json.loads(line.decode())


def make_ok(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return {"ok": True, **response}
    return {"ok": True, "response": response}


def make_err(error: str, code: str = ERR_INTERNAL) -> dict[str, Any]:
    return {"ok": False, "error": error, "code": code}
