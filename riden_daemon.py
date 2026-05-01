"""
awto-riden RidenWorker — thread-safe wrapper for Riden RD60xx PSU control.

Used by both the CLI (ttu_cli.py) and MCP server (mcp_server.py).
Each caller opens the serial port independently; Modbus serializes at protocol level.

Transport: USB serial (/dev/ttyUSB0), Bluetooth serial (/dev/rfcomm0), or native BLE.
Driver: ShayBox/Riden library (Modbus RTU via pymodbus).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import threading
import time
from typing import Any

import colorlog
import psutil
from modbus_tk.exceptions import ModbusInvalidResponseError
from riden import Riden
from riden.register import Register as R

from protocol import (
    DEFAULT_ADDRESS,
    DEFAULT_BAUD,
    DEFAULT_PORT,
    ERR_INTERNAL,
    ERR_INVALID_ARG,
    ERR_IO,
    ERR_NOT_CONNECTED,
    ERR_TIMEOUT,
)

log = logging.getLogger("riden.daemon")

_start_time   = time.monotonic()
_FREE_THREADED = sys.version_info >= (3, 13) and not sys._is_gil_enabled()


def _patch_riden_for_stability() -> None:
    """Patch upstream driver methods that otherwise retry forever.

    ShayBox/Riden's default read/write methods recurse without a retry cap on
    ModbusInvalidResponseError. On flaky links this can hang forever. We patch
    methods once at import time with bounded retries.
    """
    if getattr(Riden, "_awto_patched", False):
        return

    def _safe_read(self, register: int, length: int = 1):
        last_exc: Exception | None = None
        for _ in range(3):
            try:
                response = self.master.execute(
                    self.address,
                    3,  # READ_HOLDING_REGISTERS
                    register,
                    length,
                )
                return response if length > 1 else response[0]
            except ModbusInvalidResponseError as exc:
                last_exc = exc
        raise TimeoutError(f"modbus read failed after retries: reg={register} len={length}") from last_exc

    def _safe_write(self, register: int, value: int) -> int:
        last_exc: Exception | None = None
        for _ in range(3):
            try:
                return self.master.execute(
                    self.address,
                    6,  # WRITE_SINGLE_REGISTER
                    register,
                    1,
                    value,
                )[0]
            except ModbusInvalidResponseError as exc:
                last_exc = exc
        raise TimeoutError(f"modbus write failed after retries: reg={register}") from last_exc

    def _safe_write_multiple(self, register: int, values: tuple | list) -> tuple:
        last_exc: Exception | None = None
        for _ in range(3):
            try:
                return self.master.execute(
                    self.address,
                    16,  # WRITE_MULTIPLE_REGISTERS
                    register,
                    1,
                    values,
                )
            except ModbusInvalidResponseError as exc:
                last_exc = exc
        raise TimeoutError(f"modbus write-multiple failed after retries: reg={register}") from last_exc

    def _safe_update(self) -> None:
        # Read only core status bank (registers 4..19). The upstream update also
        # reads BAT/WH banks, which is unreliable on some RK/RD firmwares.
        data = (None,) * 4
        data += self.read(R.INT_C_S, (R.PRESET - R.INT_C_S) + 1)
        if self.type == "RD6012P":
            self.i_multi = 10000 if data[R.I_RANGE] == 0 else 1000
        self.get_int_c(data[R.INT_C_S], data[R.INT_C])
        self.get_int_f(data[R.INT_F_S], data[R.INT_F])
        self.get_v_set(data[R.V_SET])
        self.get_i_set(data[R.I_SET])
        self.get_v_out(data[R.V_OUT])
        self.get_i_out(data[R.I_OUT])
        self.get_p_out(data[R.P_OUT])
        self.get_v_in(data[R.V_IN])
        self.is_keypad(data[R.KEYPAD])
        self.get_ovp_ocp(data[R.OVP_OCP])
        self.get_cv_cc(data[R.CV_CC])
        self.is_output(data[R.OUTPUT])
        self.get_preset(data[R.PRESET])
        # Worker code expects `enable`; upstream driver sets `output`.
        self.enable = bool(getattr(self, "output", False))

    Riden.read = _safe_read
    Riden.write = _safe_write
    Riden.write_multiple = _safe_write_multiple
    Riden.update = _safe_update
    Riden._awto_patched = True


_patch_riden_for_stability()


def _infer_v_scale(psu: Riden) -> int:
    return int(getattr(psu, "v_multi", 100) or 100)


def _infer_i_scale(psu: Riden) -> int:
    return int(getattr(psu, "i_multi", 1000) or 1000)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    root  = logging.getLogger()
    root.setLevel(level)

    # syslog — plain text, no ANSI (journald / /var/log/syslog)
    try:
        syslog = logging.handlers.SysLogHandler(
            address="/dev/log",
            facility=logging.handlers.SysLogHandler.LOG_DAEMON,
        )
        syslog.ident = "awto-riden-daemon: "
        syslog.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(syslog)
    except OSError:
        pass  # /dev/log absent (container, minimal install) — stderr only

    # stderr — colored, Go-style ISO timestamp
    _LOG_COLORS = {
        "DEBUG":    "cyan",
        "INFO":     "green",
        "WARNING":  "yellow",
        "ERROR":    "red",
        "CRITICAL": "bold_red",
    }
    handler = colorlog.StreamHandler(sys.stderr)
    handler.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s %(levelname)-8s%(reset)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        log_colors=_LOG_COLORS,
    ))
    root.addHandler(handler)


# ---------------------------------------------------------------------------
# RidenWorker — thread-safe PSU wrapper
# ---------------------------------------------------------------------------

class RidenWorker:
    """Thread-safe wrapper around the ShayBox/Riden driver.

    All Modbus I/O is serialised through _lock. The log loop runs in a
    daemon thread and also acquires _lock for each poll.
    """

    def __init__(self, port: str, baud: int, address: int) -> None:
        self._port    = port
        self._baud    = baud
        self._address = address
        self._psu: Riden | None = None
        self._lock    = threading.Lock()

        self._log_path:   str | None         = None
        self._log_file                       = None
        self._log_lock    = threading.Lock()
        self._log_thread: threading.Thread | None = None
        self._log_stop    = threading.Event()

        # Observability counters (pattern from sibling awto-mcp-* repos)
        self._ops_total = 0
        self._ops_ok = 0
        self._ops_err = 0
        self._ops_by_cmd: dict[str, int] = {}
        self._last_error: dict[str, Any] | None = None

    def _error_code(self, exc: Exception) -> str:
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

    def _execute(self, cmd: str, fn):
        self._ops_total += 1
        self._ops_by_cmd[cmd] = self._ops_by_cmd.get(cmd, 0) + 1
        try:
            out = fn()
            self._ops_ok += 1
            return out
        except Exception as exc:
            self._ops_err += 1
            self._last_error = {
                "cmd": cmd,
                "error": str(exc),
                "code": self._error_code(exc),
                "ts": time.time(),
            }
            raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        with self._lock:
            self._psu = Riden(
                port=self._port,
                baudrate=self._baud,
                address=self._address,
            )
            # Try to read PSU state with a timeout to avoid hanging
            try:
                # Set serial timeout for reads
                if hasattr(self._psu, 'serial') and self._psu.serial:
                    self._psu.serial.timeout = 2.0
                self._psu.update()
                log.info(
                    "connected to PSU on %s (baud=%d addr=%d) id=%s",
                    self._port, self._baud, self._address,
                    getattr(self._psu, "id", "?"),
                )
            except Exception as e:
                log.warning(
                    "PSU not responding yet (%s); will retry on first command",
                    e,
                )
                # State will be read lazily on first query

    def close(self) -> None:
        self._stop_log()
        with self._lock:
            if self._psu is not None:
                # pymodbus client lives at _psu._client
                try:
                    client = getattr(self._psu, "_client", None)
                    if client is not None:
                        client.close()
                except Exception:
                    pass
                self._psu = None
        log.info("PSU disconnected")

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _assert_connected(self) -> Riden:
        if self._psu is None:
            raise IOError("PSU not connected")
        return self._psu

    @staticmethod
    def _protection_str(psu: Riden) -> str:
        # Upstream can expose either numeric `protect` or string `ovp_ocp`.
        val = getattr(psu, "protect", None)
        if val is not None:
            try:
                return {1: "OVP", 2: "OCP"}.get(int(val), "none")
            except (TypeError, ValueError):
                pass
        txt = str(getattr(psu, "ovp_ocp", "") or "").upper()
        if txt in {"OVP", "OCP"}:
            return txt
        return "none"

    @staticmethod
    def _output_on(psu: Riden) -> bool:
        if hasattr(psu, "enable"):
            return bool(getattr(psu, "enable"))
        return bool(getattr(psu, "output", False))

    @staticmethod
    def _set_output(psu: Riden, on: bool) -> None:
        if hasattr(psu, "set_output"):
            psu.set_output(on)
        if hasattr(psu, "enable"):
            psu.enable = on
        if hasattr(psu, "output"):
            psu.output = on

    @staticmethod
    def _cv_cc_str(psu: Riden) -> str:
        cv = getattr(psu, "cv_cc", 0)
        if isinstance(cv, str):
            cv_upper = cv.upper()
            if cv_upper in {"CV", "CC"}:
                return cv_upper
        return "CC" if int(cv) == 1 else "CV"

    def _read_status(self, psu: Riden) -> dict[str, Any]:
        psu.update()
        return {
            "v_set":   round(float(psu.v_set),  3),
            "i_set":   round(float(psu.i_set),  4),
            "v_out":   round(float(psu.v_out),  3),
            "i_out":   round(float(psu.i_out),  4),
            "p_out":   round(float(psu.p_out),  3),
            "v_in":    round(float(psu.v_in),   2),
            "output":  self._output_on(psu),
            "cv_cc":   self._cv_cc_str(psu),
            "protect": self._protection_str(psu),
            "temp_c":  getattr(psu, "int_c", None),
        }

    # ------------------------------------------------------------------
    # Commands — all acquire _lock
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            with self._lock:
                return self._read_status(self._assert_connected())

        return self._execute("status", _run)

    def set_voltage(self, volts: float) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                psu.set_v_set(volts)
                return self._read_status(psu)

        return self._execute("set_voltage", _run)

    def set_current(self, amps: float) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                psu.set_i_set(amps)
                return self._read_status(psu)

        return self._execute("set_current", _run)

    def set_output(self, on: bool) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                self._set_output(psu, on)
                return self._read_status(psu)

        return self._execute("set_output", _run)

    def set_ovp(self, volts: float) -> dict[str, Any]:
        """Set over-voltage protection via M0 OVP register (register 82)."""
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                if hasattr(psu, "set_ovp"):
                    psu.set_ovp(volts)
                else:
                    # Direct Modbus write — scale matches v_set (×100 for RD60xx)
                    scale = _infer_v_scale(psu)
                    psu._client.write_register(82, int(volts * scale), unit=self._address)
                return self._read_status(psu)

        return self._execute("set_ovp", _run)

    def set_ocp(self, amps: float) -> dict[str, Any]:
        """Set over-current protection via M0 OCP register (register 83)."""
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                if hasattr(psu, "set_ocp"):
                    psu.set_ocp(amps)
                else:
                    # Direct Modbus write — scale matches i_set (×1000 for RD60xx)
                    scale = _infer_i_scale(psu)
                    psu._client.write_register(83, int(amps * scale), unit=self._address)
                return self._read_status(psu)

        return self._execute("set_ocp", _run)

    def power_cycle(self, seconds: float) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                self._set_output(psu, False)
            time.sleep(max(0.1, seconds))
            with self._lock:
                psu = self._assert_connected()
                self._set_output(psu, True)
                return self._read_status(psu)

        return self._execute("power_cycle", _run)

    # ------------------------------------------------------------------
    # Status logging
    # ------------------------------------------------------------------

    def log_start(self, path: str, interval_ms: int) -> None:
        def _run() -> None:
            self._stop_log()
            self._log_stop.clear()
            self._log_path = path
            with self._log_lock:
                self._log_file = open(path, "a")
            self._log_thread = threading.Thread(
                target=self._log_loop,
                args=(interval_ms,),
                daemon=True,
                name="riden-log",
            )
            self._log_thread.start()
            log.info("logging started → %s every %d ms", path, interval_ms)

        self._execute("log_start", _run)

    def _log_loop(self, interval_ms: int) -> None:
        while not self._log_stop.wait(interval_ms / 1000.0):
            try:
                st = self.status()
                line = json.dumps({"ts": time.time(), **st}) + "\n"
                with self._log_lock:
                    if self._log_file:
                        self._log_file.write(line)
                        self._log_file.flush()
            except Exception as exc:
                log.warning("log loop error: %s", exc)

    def log_stop(self) -> None:
        def _run() -> None:
            self._stop_log()
            log.info("logging stopped")

        self._execute("log_stop", _run)

    def capabilities(self) -> dict[str, Any]:
        return {
            "tools": [
                "status",
                "set_voltage",
                "set_current",
                "set_output",
                "set_ovp",
                "set_ocp",
                "power_cycle",
                "log_start",
                "log_stop",
                "info",
            ],
            "error_codes": [
                ERR_NOT_CONNECTED,
                ERR_TIMEOUT,
                ERR_IO,
                ERR_INVALID_ARG,
                ERR_INTERNAL,
            ],
            "transport": ["usb-serial", "bluetooth-serial"],
        }

    def _stop_log(self) -> None:
        self._log_stop.set()
        if self._log_thread and self._log_thread.is_alive():
            self._log_thread.join(timeout=3.0)
        with self._log_lock:
            if self._log_file:
                try:
                    self._log_file.close()
                except Exception:
                    pass
                self._log_file = None
        self._log_path = None

    # ------------------------------------------------------------------
    # Process health
    # ------------------------------------------------------------------

    def info(self) -> dict[str, Any]:
        proc = psutil.Process()
        with proc.oneshot():
            return {
                "pid":          proc.pid,
                "rss_mb":       round(proc.memory_info().rss / 1024 / 1024, 1),
                "cpu_pct":      proc.cpu_percent(interval=None),
                "threads":      proc.num_threads(),
                "open_fds":     proc.num_fds(),
                "uptime_s":     round(time.monotonic() - _start_time, 1),
                "port":         self._port,
                "baud":         self._baud,
                "address":      self._address,
                "connected":    self._psu is not None,
                "logging":      self._log_path,
                "free_threaded": _FREE_THREADED,
                "python":       sys.version.split()[0],
                "ops_total":    self._ops_total,
                "ops_ok":       self._ops_ok,
                "ops_err":      self._ops_err,
                "ops_by_cmd":   dict(self._ops_by_cmd),
                "last_error":   self._last_error,
            }

