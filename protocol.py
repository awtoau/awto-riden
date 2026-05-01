"""
Shared protocol helpers for awto-mcp-riden.

All communication is JSON-lines over a Unix domain socket.

Requests (client → daemon):
    {"cmd": "ping"}
    {"cmd": "status"}
    {"cmd": "set_voltage",  "volts": 5.0}
    {"cmd": "set_current",  "amps": 1.0}
    {"cmd": "output",       "on": true}
    {"cmd": "set_ovp",      "volts": 6.0}
    {"cmd": "set_ocp",      "amps": 2.0}
    {"cmd": "power_cycle",  "seconds": 2.0}
    {"cmd": "log_start",    "path": "/tmp/riden.log", "interval_ms": 1000}
    {"cmd": "log_stop"}
    {"cmd": "info"}

Responses (daemon → client):
    {"ok": true,  "response": "pong"}
    {"ok": true,  "v_set": 5.0, "i_set": 1.0, "v_out": ..., ...}
    {"ok": false, "error": "<reason>"}

Status fields returned by status / set_* / output / power_cycle:
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
