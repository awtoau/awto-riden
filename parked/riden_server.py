#!/usr/bin/env python3
"""
awto-riden socket server — owns all serial/BLE access, multiplexes it over
a Unix domain socket so that multiple clients (MCP, CLI, scripts) can share
one PSU session.

PARKED (see issue #7): development is CLI-first for now (awto_riden.py talks to
RidenWorker directly, no daemon). This server is kept for a future
multi-instrument device hub (Sork/Pulse/nScope), not the active path.

Usage:
    python3 riden_server.py [OPTIONS]

Options:
    --port PORT       Serial port (auto-detected if omitted)
    --baud BAUD       Baud rate (default: 115200)
    --address ADDR    Modbus address (default: 1)
    --name NAME       PSU name (default: "default")
    --socket PATH     Unix socket path (default: /tmp/awto-riden.sock)
    --pid-file PATH   PID file path (default: /tmp/awto-riden.pid)
    --log-level LVL   Logging level (default: INFO)
    --psu KEY         Multi-PSU: NAME:PORT:BAUD:ADDR  (repeatable)

The server handles one request/response per connection (short-lived clients).
Long-running operations (log_start, waveform, inrush_capture, vsweep,
log_current) run inside daemon threads; the client gets an immediate response
and uses log_stop / log_retrieve to collect results.

For waveform / inrush_capture / vsweep / log_current the entire operation
runs to completion inside the handler thread (client blocks for the duration).
This is acceptable because these commands have bounded durations.

Shutdown:
    Send SIGTERM or SIGINT, or issue {"cmd": "shutdown"} over the socket.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import socket
import sys
import threading
import time
from typing import Any

# Parked under ./parked/, but the core modules live in the repo root. Put the
# repo root on sys.path so the sibling imports below resolve from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import colorlog

from protocol import (
    DEFAULT_ADDRESS,
    DEFAULT_BAUD,
    DEFAULT_PID_PATH,
    DEFAULT_PORT,
    DEFAULT_SOCKET_PATH,
    ERR_INTERNAL,
    ERR_INVALID_ARG,
    ERR_IO,
    ERR_NOT_CONNECTED,
    ERR_TIMEOUT,
    make_err,
    make_ok,
)
from riden_daemon import RidenWorker, discover_devices
from riden_transport import find_riden_port

log = logging.getLogger("riden.server")

_start_time = time.monotonic()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(level_name: str = "INFO") -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    try:
        syslog = logging.handlers.SysLogHandler(
            address="/dev/log",
            facility=logging.handlers.SysLogHandler.LOG_DAEMON,
        )
        syslog.ident = "awto-riden-server: "
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


# ---------------------------------------------------------------------------
# PSU registry
# ---------------------------------------------------------------------------

_workers: dict[str, RidenWorker] = {}
_default_psu: str = "default"
_shutdown_event = threading.Event()


def _resolve(name: str) -> RidenWorker:
    key = name if (name and name != "default") else _default_psu
    w = _workers.get(key)
    if w is None:
        raise KeyError(f"PSU '{key}' not found. Available: {list(_workers)}")
    return w


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def _err_code(exc: Exception) -> str:
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


def _exc_to_err(exc: Exception) -> dict[str, Any]:
    return make_err(str(exc), _err_code(exc))


# ---------------------------------------------------------------------------
# Command dispatcher
# ---------------------------------------------------------------------------

def _dispatch(req: dict[str, Any]) -> dict[str, Any]:
    """Route one parsed request dict to the appropriate RidenWorker method."""
    cmd = req.get("cmd", "")
    psu_name: str = req.get("psu", "default")

    # -----------------------------------------------------------------------
    # Lifecycle — no PSU needed
    # -----------------------------------------------------------------------
    if cmd == "ping":
        return make_ok({"response": "pong", "uptime_s": round(time.monotonic() - _start_time, 1)})

    if cmd == "daemon_status":
        psus = []
        for name, w in _workers.items():
            psus.append({
                "name":            name,
                "port":            w._port,
                "baud":            w._baud,
                "address":         w._address,
                "connected":       w.is_connected,
                "needs_approval":  False,
            })
        return make_ok({
            "uptime_s": round(time.monotonic() - _start_time, 1),
            "pid":      os.getpid(),
            "psus":     psus,
        })

    if cmd == "shutdown":
        _shutdown_event.set()
        return make_ok({"response": "shutting down"})

    # -----------------------------------------------------------------------
    # PSU registry
    # -----------------------------------------------------------------------
    if cmd == "list_psus":
        psus = []
        for name, w in _workers.items():
            info: dict[str, Any] = {
                "name":           name,
                "port":           w._port,
                "baud":           w._baud,
                "address":        w._address,
                "connected":      w.is_connected,
                "needs_approval": False,
                "model":          None,
                "firmware":       None,
            }
            if w.is_connected:
                try:
                    fw = w.firmware()
                    info["model"]    = fw.get("type") or fw.get("model")
                    info["firmware"] = fw.get("fw") or fw.get("firmware")
                except Exception as exc:
                    log.warning("list_psus: could not read identity for '%s': %s", name, exc)
            psus.append(info)
        return make_ok({"psus": psus, "default": _default_psu})

    if cmd == "connect":
        try:
            w = _resolve(psu_name)
        except KeyError as e:
            return _exc_to_err(e)
        if w.is_connected:
            return make_ok({"name": w.name, "already_connected": True})
        try:
            w.open()
            fw = w.firmware()
            return make_ok({
                "name":     w.name,
                "model":    fw.get("type") or fw.get("model"),
                "firmware": fw.get("fw") or fw.get("firmware"),
            })
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "disconnect":
        try:
            w = _resolve(psu_name)
        except KeyError as e:
            return _exc_to_err(e)
        try:
            w.close()
            return make_ok({"name": w.name})
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "all_off":
        disabled = []
        errors = []
        for name, w in _workers.items():
            if not w.is_connected:
                continue
            try:
                w.set_output(False)
                disabled.append(name)
            except Exception as exc:
                errors.append({"name": name, "error": str(exc)})
        return make_ok({"disabled": disabled, "errors": errors})

    # -----------------------------------------------------------------------
    # All remaining commands need a resolved worker
    # -----------------------------------------------------------------------
    try:
        w = _resolve(psu_name)
    except KeyError as e:
        return _exc_to_err(e)

    # -----------------------------------------------------------------------
    # Read-only queries
    # -----------------------------------------------------------------------
    if cmd == "status":
        try:
            return make_ok(w.status())
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "capabilities":
        try:
            return make_ok(w.capabilities())
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "firmware":
        try:
            return make_ok(w.firmware())
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "profile_serial":
        count    = int(req.get("count",    20))
        sleep_ms = int(req.get("sleep_ms", 100))
        try:
            return make_ok(w.profile_serial(count=count, sleep_ms=sleep_ms))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "list_parameters":
        try:
            return make_ok(w.list_parameters())
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "get_parameter":
        name = req.get("name")
        if not name:
            return make_err("'name' is required", ERR_INVALID_ARG)
        try:
            return make_ok(w.get_parameter(str(name)))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "modbus_read":
        start = req.get("start")
        if start is None:
            return make_err("'start' is required", ERR_INVALID_ARG)
        count = int(req.get("count", 1))
        try:
            return make_ok(w.modbus_read_holding(int(start), count))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "register_scan":
        try:
            return make_ok(w.register_scan(
                start      = int(req.get("start",     0)),
                end        = int(req.get("end",       256)),
                batch      = int(req.get("batch",     8)),
                skip_zero  = bool(req.get("skip_zero", True)),
            ))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "diff_scan":
        try:
            return make_ok(w.diff_scan(
                start      = int(req.get("start",     0)),
                end        = int(req.get("end",       300)),
                batch      = int(req.get("batch",     50)),
                output_on  = bool(req.get("output_on", True)),
                settle_ms  = int(req.get("settle_ms", 500)),
            ))
        except Exception as e:
            return _exc_to_err(e)

    # -----------------------------------------------------------------------
    # Write commands
    # -----------------------------------------------------------------------
    if cmd == "set_voltage":
        volts = req.get("volts")
        if volts is None:
            return make_err("'volts' is required", ERR_INVALID_ARG)
        try:
            return make_ok(w.set_voltage(float(volts)))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "set_current":
        amps = req.get("amps")
        if amps is None:
            return make_err("'amps' is required", ERR_INVALID_ARG)
        try:
            return make_ok(w.set_current(float(amps)))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "output":
        on = req.get("on")
        if on is None:
            return make_err("'on' is required", ERR_INVALID_ARG)
        try:
            return make_ok(w.set_output(bool(on)))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "set_ovp":
        volts = req.get("volts")
        if volts is None:
            return make_err("'volts' is required", ERR_INVALID_ARG)
        try:
            return make_ok(w.set_ovp(float(volts)))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "set_ocp":
        amps = req.get("amps")
        if amps is None:
            return make_err("'amps' is required", ERR_INVALID_ARG)
        try:
            return make_ok(w.set_ocp(float(amps)))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "power_cycle":
        seconds = float(req.get("seconds", 2.0))
        try:
            return make_ok(w.power_cycle(seconds))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "set_parameter":
        name  = req.get("name")
        value = req.get("value")
        if name is None:
            return make_err("'name' is required", ERR_INVALID_ARG)
        try:
            return make_ok(w.set_parameter(str(name), value))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "beep":
        on = bool(req.get("on", True))
        try:
            return make_ok(w.beep(on))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "modbus_write":
        register = req.get("register")
        value    = req.get("value")
        if register is None or value is None:
            return make_err("'register' and 'value' are required", ERR_INVALID_ARG)
        try:
            return make_ok(w.modbus_write_register(int(register), int(value)))
        except Exception as e:
            return _exc_to_err(e)

    # -----------------------------------------------------------------------
    # Long-running / streaming (blocking inside handler)
    # -----------------------------------------------------------------------
    if cmd == "log_start":
        path        = req.get("path")
        interval_ms = int(req.get("interval_ms", 1000))
        if not path:
            return make_err("'path' is required", ERR_INVALID_ARG)
        try:
            w.log_start(str(path), interval_ms)
            return make_ok({"path": path})
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "log_stop":
        try:
            return make_ok(w.log_stop())
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "log_retrieve":
        path     = req.get("path")
        max_rows = int(req.get("max_rows", 0))
        if not path:
            return make_err("'path' is required", ERR_INVALID_ARG)
        try:
            return make_ok(w.log_retrieve(str(path), max_rows))
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "log_current":
        try:
            result = w.log_current_start(
                samples     = int(req.get("samples",     50)),
                interval_ms = int(req.get("interval_ms", 50)),
                v_thresh    = float(req.get("v_thresh",  0.0)),
                i_thresh    = float(req.get("i_thresh",  0.0)),
            )
            return make_ok(result)
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "waveform":
        shape = req.get("shape", "sine")
        try:
            result = w.waveform(
                shape       = str(shape),
                v_center    = float(req.get("v_center",    6.0)),
                v_amplitude = float(req.get("v_amplitude", 5.0)),
                freq_hz     = float(req.get("freq_hz",     1.0)),
                duration_s  = float(req.get("duration_s",  5.0)),
                step_s      = float(req.get("step_s",      0.05)),
            )
            return make_ok(result)
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "inrush_capture":
        try:
            result = w.inrush_capture(
                v_set       = float(req.get("v_set",       12.0)),
                i_limit     = float(req.get("i_limit",     6.0)),
                samples     = int(req.get("samples",       50)),
                interval_ms = int(req.get("interval_ms",   10)),
            )
            return make_ok(result)
        except Exception as e:
            return _exc_to_err(e)

    if cmd == "vsweep":
        try:
            result = w.vsweep(
                v_start = float(req.get("v_start", 0.0)),
                v_end   = float(req.get("v_end",   12.0)),
                v_step  = float(req.get("v_step",  0.5)),
                dwell_s = float(req.get("dwell_s", 0.2)),
            )
            return make_ok(result)
        except Exception as e:
            return _exc_to_err(e)

    return make_err(f"unknown command: {cmd!r}", ERR_INVALID_ARG)


# ---------------------------------------------------------------------------
# Connection handler
# ---------------------------------------------------------------------------

def _handle_client(conn: socket.socket, addr: Any) -> None:
    log.debug("client connected: %s", addr)
    buf = bytearray()
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line.decode())
                except json.JSONDecodeError as e:
                    resp = make_err(f"JSON parse error: {e}", ERR_INVALID_ARG)
                else:
                    try:
                        resp = _dispatch(req)
                    except Exception as e:
                        log.exception("unhandled dispatch error")
                        resp = make_err(str(e), ERR_INTERNAL)
                try:
                    conn.sendall((json.dumps(resp) + "\n").encode())
                except OSError:
                    return
                if _shutdown_event.is_set():
                    return
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
        log.debug("client disconnected: %s", addr)


# ---------------------------------------------------------------------------
# Server main loop
# ---------------------------------------------------------------------------

def _run_server(socket_path: str) -> None:
    # Remove stale socket file
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(socket_path)
    srv.listen(16)
    srv.settimeout(1.0)  # allows _shutdown_event polling
    os.chmod(socket_path, 0o660)

    log.info("listening on %s", socket_path)

    threads: list[threading.Thread] = []

    try:
        while not _shutdown_event.is_set():
            try:
                conn, addr = srv.accept()
            except TimeoutError:
                continue
            t = threading.Thread(
                target=_handle_client,
                args=(conn, addr),
                daemon=True,
                name="client-handler",
            )
            t.start()
            threads.append(t)
            # Prune finished threads
            threads = [t for t in threads if t.is_alive()]
    finally:
        srv.close()
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
        log.info("socket closed")

    # Wait for in-flight client handlers
    for t in threads:
        t.join(timeout=5.0)


# ---------------------------------------------------------------------------
# PID file
# ---------------------------------------------------------------------------

def _write_pid(path: str) -> None:
    with open(path, "w") as f:
        f.write(str(os.getpid()) + "\n")
    log.debug("PID %d written to %s", os.getpid(), path)


def _remove_pid(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

def _on_signal(sig: int, _frame: Any) -> None:
    log.info("received signal %d — shutting down", sig)
    _shutdown_event.set()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="riden_server",
        description="Riden PSU socket server — owns serial access, serves multiple clients.",
    )
    p.add_argument("--port",     default=None,              help="Serial port (auto-detected if omitted)")
    p.add_argument("--baud",     type=int, default=DEFAULT_BAUD,    help="Baud rate")
    p.add_argument("--address",  type=int, default=DEFAULT_ADDRESS, help="Modbus address")
    p.add_argument("--name",     default="default",         help="PSU name")
    p.add_argument("--socket",   default=DEFAULT_SOCKET_PATH, help="Unix socket path")
    p.add_argument("--pid-file", default=DEFAULT_PID_PATH,  help="PID file path")
    p.add_argument("--log-level", default="INFO",            help="Logging level")
    p.add_argument("--psu",      action="append", default=[], metavar="NAME:PORT:BAUD:ADDR",
                   help="Multi-PSU definition (repeatable)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    _setup_logging(args.log_level)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)

    # Build PSU registry
    global _default_psu

    if args.psu:
        for spec in args.psu:
            parts = spec.split(":")
            if len(parts) < 2:
                log.error("bad --psu spec (need NAME:PORT[:BAUD[:ADDR]]): %s", spec)
                sys.exit(1)
            name    = parts[0]
            port    = parts[1]
            baud    = int(parts[2]) if len(parts) > 2 else DEFAULT_BAUD
            address = int(parts[3]) if len(parts) > 3 else DEFAULT_ADDRESS
            _workers[name] = RidenWorker(port=port, baud=baud, address=address, name=name)
            log.info("registered PSU '%s' → %s @ %d addr %d", name, port, baud, address)
        if _workers:
            _default_psu = list(_workers)[0]
    else:
        port = args.port
        if port is None:
            try:
                port = find_riden_port()
                log.info("auto-detected port: %s", port)
            except Exception:
                port = DEFAULT_PORT
                log.warning("port auto-detect failed, defaulting to %s", port)
        w = RidenWorker(port=port, baud=args.baud, address=args.address, name=args.name)
        _workers[args.name] = w
        _default_psu = args.name

    # Connect all workers
    for name, w in _workers.items():
        try:
            w.open()
            log.info("PSU '%s' connected on %s", name, w._port)
        except Exception as exc:
            log.error("PSU '%s' failed to connect: %s", name, exc)

    _write_pid(args.pid_file)
    try:
        _run_server(args.socket)
    finally:
        _remove_pid(args.pid_file)
        for w in _workers.values():
            try:
                w.close()
            except Exception:
                pass
        log.info("server stopped")


if __name__ == "__main__":
    main()
