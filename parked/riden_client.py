"""
awto-riden thin client — connects to a running riden_server.py over the
Unix domain socket defined in protocol.py.

PARKED (see issue #7): development is CLI-first for now. This client pairs with
riden_server.py, which is kept for a future multi-instrument hub, not the
active path. Use awto_riden.py for direct serial control.

Usage as a library:
    from riden_client import RidenClient

    with RidenClient() as c:
        print(c.status())
        c.set_voltage(5.0)
        c.output(True)

Usage as a CLI (quick-test / scripting):
    python3 riden_client.py status
    python3 riden_client.py set_voltage 5.0
    python3 riden_client.py output on
    python3 riden_client.py ping
    python3 riden_client.py --socket /tmp/awto-riden.sock --psu bench status

Every method raises RidenClientError on {"ok": false} responses.
The raw response dict is available as exc.response.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from typing import Any

# Parked under ./parked/, but protocol.py lives in the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import (
    DEFAULT_SOCKET_PATH,
    ERR_INVALID_ARG,
    make_err,
    recv_response,
    send_request,
)


class RidenClientError(Exception):
    """Raised when the daemon returns {"ok": false}."""

    def __init__(self, message: str, code: str = "", response: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.response = response or {}


# ---------------------------------------------------------------------------
# Core client
# ---------------------------------------------------------------------------

class RidenClient:
    """Thin synchronous client for riden_server.py.

    Each method opens a new connection, sends one request, reads one response,
    and closes the connection.  This is intentionally stateless so multiple
    clients can coexist without sharing a socket fd.

    Args:
        socket_path: Path to the Unix domain socket (default: DEFAULT_SOCKET_PATH).
        psu:         Default PSU name sent with every request (default: "default").
        timeout:     Socket timeout in seconds (default: 30.0).
                     Long-running commands (waveform, vsweep, …) may need more.
    """

    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET_PATH,
        psu: str = "default",
        timeout: float = 30.0,
    ) -> None:
        self._socket_path = socket_path
        self._psu = psu
        self._timeout = timeout

    def __enter__(self) -> "RidenClient":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # Internal transport
    # ------------------------------------------------------------------

    def _send(self, req: dict[str, Any]) -> dict[str, Any]:
        """Open connection, send req, return response, close."""
        req.setdefault("psu", self._psu)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect(self._socket_path)
            resp = send_request(sock, req)
        finally:
            sock.close()
        if not resp.get("ok"):
            raise RidenClientError(
                resp.get("error", "unknown error"),
                code=resp.get("code", ""),
                response=resp,
            )
        return resp

    # ------------------------------------------------------------------
    # Daemon lifecycle
    # ------------------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        return self._send({"cmd": "ping"})

    def daemon_status(self) -> dict[str, Any]:
        return self._send({"cmd": "daemon_status"})

    def shutdown(self) -> dict[str, Any]:
        return self._send({"cmd": "shutdown"})

    # ------------------------------------------------------------------
    # PSU registry
    # ------------------------------------------------------------------

    def list_psus(self) -> dict[str, Any]:
        return self._send({"cmd": "list_psus"})

    def connect(self, psu: str | None = None) -> dict[str, Any]:
        return self._send({"cmd": "connect", "psu": psu or self._psu})

    def disconnect(self, psu: str | None = None) -> dict[str, Any]:
        return self._send({"cmd": "disconnect", "psu": psu or self._psu})

    def all_off(self) -> dict[str, Any]:
        return self._send({"cmd": "all_off"})

    # ------------------------------------------------------------------
    # Read-only queries
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return self._send({"cmd": "status"})

    def capabilities(self) -> dict[str, Any]:
        return self._send({"cmd": "capabilities"})

    def firmware(self) -> dict[str, Any]:
        return self._send({"cmd": "firmware"})

    def profile_serial(self, count: int = 20, sleep_ms: int = 100) -> dict[str, Any]:
        return self._send({"cmd": "profile_serial", "count": count, "sleep_ms": sleep_ms})

    def list_parameters(self) -> dict[str, Any]:
        return self._send({"cmd": "list_parameters"})

    def get_parameter(self, name: str) -> dict[str, Any]:
        return self._send({"cmd": "get_parameter", "name": name})

    def modbus_read(self, start: int, count: int = 1) -> dict[str, Any]:
        return self._send({"cmd": "modbus_read", "start": start, "count": count})

    def register_scan(
        self,
        start: int = 0,
        end: int = 256,
        batch: int = 8,
        skip_zero: bool = True,
    ) -> dict[str, Any]:
        return self._send({
            "cmd": "register_scan",
            "start": start, "end": end, "batch": batch, "skip_zero": skip_zero,
        })

    def diff_scan(
        self,
        start: int = 0,
        end: int = 300,
        batch: int = 50,
        output_on: bool = True,
        settle_ms: int = 500,
    ) -> dict[str, Any]:
        return self._send({
            "cmd": "diff_scan",
            "start": start, "end": end, "batch": batch,
            "output_on": output_on, "settle_ms": settle_ms,
        })

    # ------------------------------------------------------------------
    # Write commands
    # ------------------------------------------------------------------

    def set_voltage(self, volts: float) -> dict[str, Any]:
        return self._send({"cmd": "set_voltage", "volts": volts})

    def set_current(self, amps: float) -> dict[str, Any]:
        return self._send({"cmd": "set_current", "amps": amps})

    def output(self, on: bool) -> dict[str, Any]:
        return self._send({"cmd": "output", "on": on})

    def set_ovp(self, volts: float) -> dict[str, Any]:
        return self._send({"cmd": "set_ovp", "volts": volts})

    def set_ocp(self, amps: float) -> dict[str, Any]:
        return self._send({"cmd": "set_ocp", "amps": amps})

    def power_cycle(self, seconds: float = 2.0) -> dict[str, Any]:
        return self._send({"cmd": "power_cycle", "seconds": seconds})

    def set_parameter(self, name: str, value: Any) -> dict[str, Any]:
        return self._send({"cmd": "set_parameter", "name": name, "value": value})

    def beep(self, on: bool = True) -> dict[str, Any]:
        return self._send({"cmd": "beep", "on": on})

    def modbus_write(self, register: int, value: int) -> dict[str, Any]:
        return self._send({"cmd": "modbus_write", "register": register, "value": value})

    # ------------------------------------------------------------------
    # Long-running operations
    # ------------------------------------------------------------------

    def log_start(self, path: str, interval_ms: int = 1000) -> dict[str, Any]:
        return self._send({"cmd": "log_start", "path": path, "interval_ms": interval_ms})

    def log_stop(self) -> dict[str, Any]:
        return self._send({"cmd": "log_stop"})

    def log_retrieve(self, path: str, max_rows: int = 0) -> dict[str, Any]:
        return self._send({"cmd": "log_retrieve", "path": path, "max_rows": max_rows})

    def log_current(
        self,
        samples: int = 50,
        interval_ms: int = 50,
        v_thresh: float = 0.0,
        i_thresh: float = 0.0,
    ) -> dict[str, Any]:
        """Block until all samples are collected (timeout auto-extended)."""
        timeout = max(self._timeout, samples * interval_ms / 1000.0 + 10.0)
        req = {
            "cmd": "log_current",
            "samples": samples, "interval_ms": interval_ms,
            "v_thresh": v_thresh, "i_thresh": i_thresh,
        }
        req.setdefault("psu", self._psu)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self._socket_path)
            resp = send_request(sock, req)
        finally:
            sock.close()
        if not resp.get("ok"):
            raise RidenClientError(
                resp.get("error", "unknown error"),
                code=resp.get("code", ""),
                response=resp,
            )
        return resp

    def waveform(
        self,
        shape: str = "sine",
        v_center: float = 6.0,
        v_amplitude: float = 5.0,
        freq_hz: float = 1.0,
        duration_s: float = 5.0,
        step_s: float = 0.05,
    ) -> dict[str, Any]:
        """Block for the full waveform duration + headroom."""
        timeout = max(self._timeout, duration_s + 30.0)
        req = {
            "cmd": "waveform",
            "shape": shape, "v_center": v_center, "v_amplitude": v_amplitude,
            "freq_hz": freq_hz, "duration_s": duration_s, "step_s": step_s,
        }
        req.setdefault("psu", self._psu)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self._socket_path)
            resp = send_request(sock, req)
        finally:
            sock.close()
        if not resp.get("ok"):
            raise RidenClientError(
                resp.get("error", "unknown error"),
                code=resp.get("code", ""),
                response=resp,
            )
        return resp

    def inrush_capture(
        self,
        v_set: float = 12.0,
        i_limit: float = 6.0,
        samples: int = 50,
        interval_ms: int = 10,
    ) -> dict[str, Any]:
        timeout = max(self._timeout, samples * interval_ms / 1000.0 + 10.0)
        req = {
            "cmd": "inrush_capture",
            "v_set": v_set, "i_limit": i_limit,
            "samples": samples, "interval_ms": interval_ms,
        }
        req.setdefault("psu", self._psu)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self._socket_path)
            resp = send_request(sock, req)
        finally:
            sock.close()
        if not resp.get("ok"):
            raise RidenClientError(
                resp.get("error", "unknown error"),
                code=resp.get("code", ""),
                response=resp,
            )
        return resp

    def vsweep(
        self,
        v_start: float = 0.0,
        v_end: float = 12.0,
        v_step: float = 0.5,
        dwell_s: float = 0.2,
    ) -> dict[str, Any]:
        steps = max(1, int(abs(v_end - v_start) / max(v_step, 0.001)))
        timeout = max(self._timeout, steps * dwell_s + 30.0)
        req = {
            "cmd": "vsweep",
            "v_start": v_start, "v_end": v_end, "v_step": v_step, "dwell_s": dwell_s,
        }
        req.setdefault("psu", self._psu)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self._socket_path)
            resp = send_request(sock, req)
        finally:
            sock.close()
        if not resp.get("ok"):
            raise RidenClientError(
                resp.get("error", "unknown error"),
                code=resp.get("code", ""),
                response=resp,
            )
        return resp


# ---------------------------------------------------------------------------
# Minimal CLI
# ---------------------------------------------------------------------------

_SIMPLE_COMMANDS: dict[str, dict[str, Any]] = {
    "ping":           {"cmd": "ping"},
    "daemon_status":  {"cmd": "daemon_status"},
    "shutdown":       {"cmd": "shutdown"},
    "list_psus":      {"cmd": "list_psus"},
    "status":         {"cmd": "status"},
    "capabilities":   {"cmd": "capabilities"},
    "firmware":       {"cmd": "firmware"},
    "list_parameters":{"cmd": "list_parameters"},
    "all_off":        {"cmd": "all_off"},
    "log_stop":       {"cmd": "log_stop"},
}

_HELP = """\
Usage: riden_client.py [OPTIONS] COMMAND [ARGS...]

Options:
  --socket PATH     Unix socket (default: {sock})
  --psu NAME        PSU name (default: default)
  --timeout SECS    Socket timeout (default: 30)

Commands (no args):
  ping  daemon_status  shutdown  list_psus  status  capabilities
  firmware  list_parameters  all_off  log_stop

Commands with args:
  connect [psu]
  disconnect [psu]
  set_voltage <volts>
  set_current <amps>
  output <on|off|true|false>
  set_ovp <volts>
  set_ocp <amps>
  power_cycle [seconds]
  beep [on|off]
  get_parameter <name>
  set_parameter <name> <value>
  modbus_read <start> [count]
  modbus_write <register> <value>
  register_scan [start] [end] [batch]
  profile_serial [count] [sleep_ms]
  log_start <path> [interval_ms]
  log_retrieve <path> [max_rows]
  log_current [samples] [interval_ms]
  waveform [shape] [v_center] [v_amplitude] [freq_hz] [duration_s] [step_s]
  inrush_capture [v_set] [i_limit] [samples] [interval_ms]
  vsweep [v_start] [v_end] [v_step] [dwell_s]
""".format(sock=DEFAULT_SOCKET_PATH)


def _coerce_bool(s: str) -> bool:
    return s.strip().lower() in {"1", "true", "on", "yes"}


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])

    socket_path = DEFAULT_SOCKET_PATH
    psu = "default"
    timeout = 30.0

    while args and args[0].startswith("--"):
        opt = args.pop(0)
        if opt == "--socket" and args:
            socket_path = args.pop(0)
        elif opt == "--psu" and args:
            psu = args.pop(0)
        elif opt == "--timeout" and args:
            timeout = float(args.pop(0))
        elif opt in {"--help", "-h"}:
            print(_HELP)
            return 0
        else:
            print(f"unknown option: {opt}", file=sys.stderr)
            return 2

    if not args:
        print(_HELP)
        return 0

    cmd = args.pop(0)
    c = RidenClient(socket_path=socket_path, psu=psu, timeout=timeout)

    try:
        if cmd in _SIMPLE_COMMANDS:
            req = dict(_SIMPLE_COMMANDS[cmd])
            resp = c._send(req)
        elif cmd == "connect":
            resp = c.connect(args[0] if args else None)
        elif cmd == "disconnect":
            resp = c.disconnect(args[0] if args else None)
        elif cmd == "set_voltage":
            resp = c.set_voltage(float(args[0]))
        elif cmd == "set_current":
            resp = c.set_current(float(args[0]))
        elif cmd == "output":
            resp = c.output(_coerce_bool(args[0]))
        elif cmd == "set_ovp":
            resp = c.set_ovp(float(args[0]))
        elif cmd == "set_ocp":
            resp = c.set_ocp(float(args[0]))
        elif cmd == "power_cycle":
            resp = c.power_cycle(float(args[0]) if args else 2.0)
        elif cmd == "beep":
            resp = c.beep(_coerce_bool(args[0]) if args else True)
        elif cmd == "get_parameter":
            resp = c.get_parameter(args[0])
        elif cmd == "set_parameter":
            resp = c.set_parameter(args[0], args[1])
        elif cmd == "modbus_read":
            resp = c.modbus_read(int(args[0]), int(args[1]) if len(args) > 1 else 1)
        elif cmd == "modbus_write":
            resp = c.modbus_write(int(args[0]), int(args[1]))
        elif cmd == "register_scan":
            resp = c.register_scan(
                start = int(args[0]) if len(args) > 0 else 0,
                end   = int(args[1]) if len(args) > 1 else 256,
                batch = int(args[2]) if len(args) > 2 else 8,
            )
        elif cmd == "profile_serial":
            resp = c.profile_serial(
                count    = int(args[0]) if args else 20,
                sleep_ms = int(args[1]) if len(args) > 1 else 100,
            )
        elif cmd == "log_start":
            resp = c.log_start(
                path        = args[0],
                interval_ms = int(args[1]) if len(args) > 1 else 1000,
            )
        elif cmd == "log_retrieve":
            resp = c.log_retrieve(
                path     = args[0],
                max_rows = int(args[1]) if len(args) > 1 else 0,
            )
        elif cmd == "log_current":
            resp = c.log_current(
                samples     = int(args[0]) if args else 50,
                interval_ms = int(args[1]) if len(args) > 1 else 50,
            )
        elif cmd == "waveform":
            resp = c.waveform(
                shape       = args[0] if len(args) > 0 else "sine",
                v_center    = float(args[1]) if len(args) > 1 else 6.0,
                v_amplitude = float(args[2]) if len(args) > 2 else 5.0,
                freq_hz     = float(args[3]) if len(args) > 3 else 1.0,
                duration_s  = float(args[4]) if len(args) > 4 else 5.0,
                step_s      = float(args[5]) if len(args) > 5 else 0.05,
            )
        elif cmd == "inrush_capture":
            resp = c.inrush_capture(
                v_set       = float(args[0]) if len(args) > 0 else 12.0,
                i_limit     = float(args[1]) if len(args) > 1 else 6.0,
                samples     = int(args[2])   if len(args) > 2 else 50,
                interval_ms = int(args[3])   if len(args) > 3 else 10,
            )
        elif cmd == "vsweep":
            resp = c.vsweep(
                v_start = float(args[0]) if len(args) > 0 else 0.0,
                v_end   = float(args[1]) if len(args) > 1 else 12.0,
                v_step  = float(args[2]) if len(args) > 2 else 0.5,
                dwell_s = float(args[3]) if len(args) > 3 else 0.2,
            )
        else:
            print(f"unknown command: {cmd!r}", file=sys.stderr)
            return 2

        print(json.dumps(resp, indent=2))
        return 0

    except RidenClientError as e:
        print(json.dumps({"ok": False, "error": str(e), "code": e.code}), file=sys.stderr)
        return 1
    except ConnectionRefusedError:
        print(json.dumps({"ok": False, "error": f"cannot connect to {socket_path} — is riden_server.py running?",
                          "code": "ENOTCONN"}), file=sys.stderr)
        return 1
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "code": "EINTERNAL"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
