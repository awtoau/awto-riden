"""
awto-riden RidenWorker — thread-safe wrapper for Riden RD60xx PSU control.

Used by both the CLI (ttu_cli.py) and MCP server (mcp_server.py).
Each caller opens the serial port independently; Modbus serializes at protocol level.

Transport: USB serial (/dev/ttyUSB0), Bluetooth serial (/dev/rfcomm0), or native BLE.
Driver: own RidenTransport layer (riden_transport.py) — no upstream Riden class dependency.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import math
import statistics
import sys
import threading
import time
from typing import Any

import colorlog
import psutil

from riden_register import Register as R
from riden_transport import SerialTransport, _model_info

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


# ---------------------------------------------------------------------------
# RidenDevice — thin device wrapper over RidenTransport
#
# Replaces the ShayBox/Riden `Riden` class. Exposes the same attribute and
# method names used throughout RidenWorker so the rest of the code is
# unchanged. Bounded retries live in SerialTransport (riden_transport.py).
# ---------------------------------------------------------------------------

class RidenDevice:
    """Device-level Modbus abstraction for Riden RD60xx / RK60xx PSUs.

    Built on RidenTransport — no upstream Riden class dependency.
    Attributes mirror the ShayBox/Riden Riden class for drop-in compatibility.
    """

    def __init__(self, transport: SerialTransport) -> None:
        self.transport = transport
        self.address   = transport.address

        # Read identity block (registers 0-3) in one call
        data = transport.read(R.ID, 4)
        self.id  = int(data[0])
        self.sn  = "%08d" % (int(data[1]) << 16 | int(data[2]))
        self.fw  = int(data[3])

        # Model detection (v/i/p multipliers)
        info = _model_info(self.id)
        self.type     = info["type"]
        self.v_multi  = info["v_multi"]
        self.i_multi  = info["i_multi"]
        self.p_multi  = info["p_multi"]
        self.v_in_multi = 100  # constant across all models

        # Status attributes — populated by update()
        self.v_set   = 0.0
        self.i_set   = 0.0
        self.v_out   = 0.0
        self.i_out   = 0.0
        self.p_out   = 0.0
        self.v_in    = 0.0
        self.output  = False
        self.enable  = False
        self.cv_cc   = "CV"
        self.ovp_ocp: str | None = None
        self.int_c   = 0
        self.int_f   = 0
        self.keypad  = False
        self.preset  = 0

        self.update()

    # ------------------------------------------------------------------
    # Modbus pass-through (compatibility shim — same signature as Riden)
    # ------------------------------------------------------------------

    def read(self, register: int, length: int = 1):
        result = self.transport.read(register, length)
        return result if length > 1 else result[0]

    def write(self, register: int, value: int) -> None:
        self.transport.write(register, value)

    def write_multiple(self, register: int, values: tuple | list) -> None:
        self.transport.write_multiple(register, values)

    def close(self) -> None:
        self.transport.close()

    # ------------------------------------------------------------------
    # Status poll
    # ------------------------------------------------------------------

    def update(self) -> None:
        """Read core status bank (regs 4-20) in one FC03 call."""
        data = (None,) * 4
        data += self.transport.read(R.INT_C_S, (R.PRESET - R.INT_C_S) + 1)

        if self.type == "RD6012P":
            self.i_multi = 10000 if data[R.I_RANGE] == 0 else 1000

        self.int_c   = data[R.INT_C]  * (-1 if data[R.INT_C_S] else 1)
        self.int_f   = data[R.INT_F]  * (-1 if data[R.INT_F_S] else 1)
        self.v_set   = data[R.V_SET]  / self.v_multi
        self.i_set   = data[R.I_SET]  / self.i_multi
        self.v_out   = data[R.V_OUT]  / self.v_multi
        self.i_out   = data[R.I_OUT]  / self.i_multi
        self.p_out   = data[R.P_OUT]  / self.p_multi
        self.v_in    = data[R.V_IN]   / self.v_in_multi
        self.keypad  = bool(data[R.KEYPAD])
        ovp_raw      = data[R.OVP_OCP]
        self.ovp_ocp = "OVP" if ovp_raw == 1 else "OCP" if ovp_raw == 2 else None
        self.cv_cc   = "CV" if data[R.CV_CC] == 0 else "CC"
        self.output  = bool(data[R.OUTPUT])
        self.enable  = self.output
        self.preset  = data[R.PRESET]

    # ------------------------------------------------------------------
    # Setters
    # ------------------------------------------------------------------

    def set_v_set(self, v: float) -> None:
        self.v_set = v
        self.transport.write(R.V_SET, int(round(v * self.v_multi)))

    def set_i_set(self, i: float) -> None:
        self.i_set = i
        self.transport.write(R.I_SET, int(round(i * self.i_multi)))

    def set_output(self, on: bool) -> None:
        self.output = on
        self.enable = on
        self.transport.write(R.OUTPUT, int(on))

    def set_ovp(self, v: float) -> None:
        self.transport.write(R.M0_OVP, int(round(v * self.v_multi)))

    def set_ocp(self, i: float) -> None:
        self.transport.write(R.M0_OCP, int(round(i * self.i_multi)))

    # ------------------------------------------------------------------
    # Getters (accept optional raw int for bulk-read callers)
    # ------------------------------------------------------------------

    def get_v_out(self, raw: int | None = None) -> float:
        if raw is None:
            raw = self.transport.read(R.V_OUT)[0]
        self.v_out = raw / self.v_multi
        return self.v_out

    def get_i_out(self, raw: int | None = None) -> float:
        if raw is None:
            raw = self.transport.read(R.I_OUT)[0]
        self.i_out = raw / self.i_multi
        return self.i_out

    def get_v_set(self, raw: int | None = None) -> float:
        if raw is None:
            raw = self.transport.read(R.V_SET)[0]
        self.v_set = raw / self.v_multi
        return self.v_set

    def get_i_set(self, raw: int | None = None) -> float:
        if raw is None:
            raw = self.transport.read(R.I_SET)[0]
        self.i_set = raw / self.i_multi
        return self.i_set


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
    """Thread-safe wrapper around the RidenDevice + SerialTransport layer.

    All Modbus I/O is serialised through _lock. The log loop runs in a
    daemon thread and also acquires _lock for each poll.
    """

    def __init__(self, port: str, baud: int, address: int, name: str = "default") -> None:
        self.name     = name
        self._port    = port
        self._baud    = baud
        self._address = address
        self._psu: RidenDevice | None = None
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
        self._serial_profile: dict[str, Any] | None = None

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
            transport = SerialTransport(self._port, self._baud, self._address)
            transport.open()
            try:
                self._psu = RidenDevice(transport)
                self._serial_profile = None
                try:
                    self._serial_profile = self._profile_serial_locked(self._psu, count=10, sleep_ms=100)
                except Exception as profile_exc:
                    log.warning("serial auto-profile failed (continuing without pacing hint): %s", profile_exc)
                log.info(
                    "connected to PSU on %s (baud=%d addr=%d) type=%s id=%s fw=%s",
                    self._port, self._baud, self._address,
                    self._psu.type, self._psu.id, self._psu.fw,
                )
                if self._serial_profile is not None:
                    log.info(
                        "serial pacing profile: recommended_poll_ms=%d median_ms=%.2f jitter_ms=%.2f",
                        self._serial_profile["recommended_poll_ms"],
                        self._serial_profile["timing"]["median_ms"],
                        self._serial_profile["timing"]["jitter_p95_minus_p50_ms"],
                    )
            except Exception as e:
                transport.close()
                log.warning(
                    "PSU not responding yet (%s); will retry on first command",
                    e,
                )

    def close(self) -> None:
        self._stop_log()
        with self._lock:
            if self._psu is not None:
                try:
                    self._psu.close()
                except Exception:
                    pass
                self._psu = None
        log.info("PSU disconnected")

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _assert_connected(self) -> RidenDevice:
        if self._psu is None:
            raise IOError("PSU not connected")
        return self._psu

    @property
    def is_connected(self) -> bool:
        return self._psu is not None

    @staticmethod
    def _protection_str(psu: RidenDevice) -> str:
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
    def _output_on(psu: RidenDevice) -> bool:
        if hasattr(psu, "enable"):
            return bool(getattr(psu, "enable"))
        return bool(getattr(psu, "output", False))

    @staticmethod
    def _set_output(psu: RidenDevice, on: bool) -> None:
        if hasattr(psu, "set_output"):
            psu.set_output(on)
        if hasattr(psu, "enable"):
            psu.enable = on
        if hasattr(psu, "output"):
            psu.output = on

    @staticmethod
    def _cv_cc_str(psu: RidenDevice) -> str:
        cv = getattr(psu, "cv_cc", 0)
        if isinstance(cv, str):
            cv_upper = cv.upper()
            if cv_upper in {"CV", "CC"}:
                return cv_upper
        return "CC" if int(cv) == 1 else "CV"

    def _read_status(self, psu: RidenDevice) -> dict[str, Any]:
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

    def _device_info(self, psu: RidenDevice) -> dict[str, Any]:
        """Best-effort device identity fields across Riden model/firmware variants."""
        info: dict[str, Any] = {}
        keys = (
            "type",
            "id",
            "model",
            "sn",
            "serial_number",
            "fw",
            "firmware",
            "firmware_version",
            "sw",
            "sw_version",
            "hw",
            "hw_version",
        )
        for key in keys:
            val = getattr(psu, key, None)
            if val is not None:
                info[key] = val
        info["port"] = self._port
        info["baud"] = self._baud
        info["address"] = self._address
        info["transport"] = "bluetooth-serial" if "rfcomm" in self._port else "usb-serial"
        return info

    def _profile_serial_locked(
        self,
        psu: RidenDevice,
        count: int = 20,
        sleep_ms: int = 100,
        register: int = 10,
        reg_count: int = 9,
    ) -> dict[str, Any]:
        """Profile serial poll timing using current transport/session.

        Runs a fixed-cadence read loop to estimate a stable poll interval rather
        than chasing minimum RTT.
        """
        if count < 3:
            raise ValueError("count must be >= 3")
        if sleep_ms < 0:
            raise ValueError("sleep_ms must be >= 0")
        if reg_count < 1:
            raise ValueError("reg_count must be >= 1")

        times_ms: list[float] = []
        ok = 0

        # Warm-up avoids one-time first-read artifacts.
        psu.transport.read(register, reg_count)
        for _ in range(count):
            t0 = time.perf_counter()
            psu.transport.read(register, reg_count)
            dt_ms = (time.perf_counter() - t0) * 1000
            times_ms.append(dt_ms)
            ok += 1
            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)

        samples = sorted(times_ms)
        p50 = statistics.median(samples)
        p90 = samples[int(0.9 * (len(samples) - 1))]
        p95 = samples[int(0.95 * (len(samples) - 1))]
        jitter = max(0.0, p95 - p50)

        bytes_per_sec = self._baud / 10.0
        us_per_byte = 1_000_000.0 / bytes_per_sec
        wire_ms = ((8 + (5 + reg_count * 2)) * us_per_byte) / 1000.0

        # Data-driven stable cadence: keep interval above p95 with a small headroom,
        # then quantize to practical scheduling buckets.
        raw_recommended_ms = p95 + max(3.0, p95 * 0.10)
        quantization_ms = 50 if raw_recommended_ms >= 250.0 else 20
        recommended_poll_ms = int(math.ceil(raw_recommended_ms / quantization_ms) * quantization_ms)

        transport_name = "bluetooth-serial" if "rfcomm" in self._port else "usb-serial"
        return {
            "recommended_poll_ms": recommended_poll_ms,
            "strategy": "stable-cadence",
            "raw_recommended_poll_ms": round(raw_recommended_ms, 2),
            "quantization_ms": quantization_ms,
            "transport": transport_name,
            "wire_theory_ms": round(wire_ms, 3),
            "register_read": {
                "start": register,
                "count": reg_count,
            },
            "timing": {
                "count": count,
                "sleep_ms": sleep_ms,
                "ok": ok,
                "min_ms": round(min(samples), 2),
                "median_ms": round(p50, 2),
                "p90_ms": round(p90, 2),
                "p95_ms": round(p95, 2),
                "max_ms": round(max(samples), 2),
                "mean_ms": round(statistics.mean(samples), 2),
                "jitter_p95_minus_p50_ms": round(jitter, 2),
            },
            "notes": [
                "Recommendation is tuned for stable timestamp spacing, not minimum RTT.",
                "Recommendation is derived from measured p95 latency plus headroom.",
                "Poll interval is quantized to 20 ms or 50 ms buckets for practical schedulers.",
            ],
        }

    def profile_serial(self, count: int = 20, sleep_ms: int = 100) -> dict[str, Any]:
        """Profile link timing and compute a stable recommended polling cadence."""
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                self._serial_profile = self._profile_serial_locked(psu, count=count, sleep_ms=sleep_ms)
                return dict(self._serial_profile)

        return self._execute("profile_serial", _run)

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"1", "true", "on", "yes", "enable", "enabled"}:
                return True
            if v in {"0", "false", "off", "no", "disable", "disabled"}:
                return False
        raise ValueError(f"cannot coerce to bool: {value!r}")

    def _list_parameters_locked(self, psu: RidenDevice) -> list[dict[str, Any]]:
        """Advertise parameters supported by this connected model/firmware."""
        params: list[dict[str, Any]] = []

        def add(name: str, access: str, typ: str, unit: str | None, source: str) -> None:
            params.append({
                "name": name,
                "access": access,
                "type": typ,
                "unit": unit,
                "source": source,
            })

        if hasattr(psu, "v_set"):
            add("voltage_setpoint", "read-write", "float", "V", "v_set/set_v_set")
        if hasattr(psu, "i_set"):
            add("current_limit", "read-write", "float", "A", "i_set/set_i_set")
        if hasattr(psu, "output") or hasattr(psu, "enable"):
            add("output_enabled", "read-write", "bool", None, "output/set_output")
        if hasattr(psu, "ovp") or hasattr(psu, "set_ovp") or hasattr(psu, "_client"):
            add("ovp", "read-write", "float", "V", "ovp/set_ovp or reg82")
        if hasattr(psu, "ocp") or hasattr(psu, "set_ocp") or hasattr(psu, "_client"):
            add("ocp", "read-write", "float", "A", "ocp/set_ocp or reg83")
        if hasattr(psu, "preset"):
            add("preset", "read-only", "int", None, "preset")
        if hasattr(psu, "cv_cc"):
            add("cv_cc", "read-only", "string", None, "cv_cc")
        if hasattr(psu, "protect") or hasattr(psu, "ovp_ocp"):
            add("protect", "read-only", "string", None, "protect/ovp_ocp")
        if hasattr(psu, "v_out"):
            add("voltage_out", "read-only", "float", "V", "v_out")
        if hasattr(psu, "i_out"):
            add("current_out", "read-only", "float", "A", "i_out")
        if hasattr(psu, "p_out"):
            add("power_out", "read-only", "float", "W", "p_out")
        if hasattr(psu, "v_in"):
            add("voltage_in", "read-only", "float", "V", "v_in")
        if hasattr(psu, "int_c"):
            add("temperature_c", "read-only", "float", "C", "int_c")
        if hasattr(psu, "keypad"):
            add("keypad_locked", "read-only", "bool", None, "keypad")
        if hasattr(psu, "set_beep") or hasattr(psu, "set_buzzer") or hasattr(psu, "beep") or hasattr(psu, "buzzer"):
            add("buzzer_enabled", "read-write", "bool", None, "set_beep/set_buzzer/beep")

        return params

    def _get_parameter_locked(self, psu: RidenDevice, name: str) -> Any:
        n = name.strip().lower()
        if n == "voltage_setpoint":
            return float(getattr(psu, "v_set"))
        if n == "current_limit":
            return float(getattr(psu, "i_set"))
        if n == "output_enabled":
            return self._output_on(psu)
        if n == "ovp":
            if hasattr(psu, "ovp"):
                return float(getattr(psu, "ovp"))
            raise ValueError("ovp readback not available on this model/firmware")
        if n == "ocp":
            if hasattr(psu, "ocp"):
                return float(getattr(psu, "ocp"))
            raise ValueError("ocp readback not available on this model/firmware")
        if n == "preset":
            return int(getattr(psu, "preset"))
        if n == "cv_cc":
            return self._cv_cc_str(psu)
        if n == "protect":
            return self._protection_str(psu)
        if n == "voltage_out":
            return float(getattr(psu, "v_out"))
        if n == "current_out":
            return float(getattr(psu, "i_out"))
        if n == "power_out":
            return float(getattr(psu, "p_out"))
        if n == "voltage_in":
            return float(getattr(psu, "v_in"))
        if n == "temperature_c":
            return float(getattr(psu, "int_c"))
        if n == "keypad_locked":
            return bool(getattr(psu, "keypad"))
        if n == "buzzer_enabled":
            if hasattr(psu, "beep"):
                return bool(getattr(psu, "beep"))
            if hasattr(psu, "buzzer"):
                return bool(getattr(psu, "buzzer"))
            raise ValueError("buzzer readback not available on this model/firmware")
        if hasattr(psu, n):
            return getattr(psu, n)
        raise ValueError(f"unknown parameter: {name}")

    def _set_parameter_locked(self, psu: RidenDevice, name: str, value: Any) -> None:
        n = name.strip().lower()
        if n == "voltage_setpoint":
            psu.set_v_set(float(value))
            return
        if n == "current_limit":
            psu.set_i_set(float(value))
            return
        if n == "output_enabled":
            self._set_output(psu, self._coerce_bool(value))
            return
        if n == "ovp":
            psu.set_ovp(float(value))
            return
        if n == "ocp":
            psu.set_ocp(float(value))
            return
        if n == "buzzer_enabled":
            on = self._coerce_bool(value)
            if hasattr(psu, "set_beep"):
                psu.set_beep(on)
                return
            if hasattr(psu, "set_buzzer"):
                psu.set_buzzer(on)
                return
            if hasattr(psu, "beep"):
                setattr(psu, "beep", on)
                return
            if hasattr(psu, "buzzer"):
                setattr(psu, "buzzer", on)
                return
            raise ValueError("buzzer control not supported on this model/firmware")
        raise ValueError(f"parameter is not writable or unsupported: {name}")

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
                psu.set_ovp(volts)
                return self._read_status(psu)

        return self._execute("set_ovp", _run)

    def set_ocp(self, amps: float) -> dict[str, Any]:
        """Set over-current protection via M0 OCP register (register 83)."""
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                psu.set_ocp(amps)
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

    def log_stop(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": True}

        def _run() -> None:
            path = getattr(self, "_log_path", None)
            self._stop_log()
            log.info("logging stopped")
            if path:
                result.update(self._log_summary(path))

        self._execute("log_stop", _run)
        return result

    def log_retrieve(self, path: str, max_rows: int = 0) -> dict[str, Any]:
        """Return summary stats (and optionally downsampled rows) from a JSONL log file.

        Args:
            path:     Path to JSONL file written by log_current_start.
            max_rows: If > 0, return a columnar-format downsampled slice.
                      Default 0 = stats only (safe for large files).
        """
        summary = self._log_summary(path)
        if max_rows > 0 and summary.get("samples", 0) > 0:
            import json as _json
            lines = []
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        lines.append(_json.loads(line))
            # Downsample to max_rows evenly
            n = len(lines)
            if n > max_rows:
                step = n / max_rows
                lines = [lines[int(i * step)] for i in range(max_rows)]
            summary["rows"] = {
                "ts":    [r["ts"]    for r in lines],
                "v_out": [r["v_out"] for r in lines],
                "i_out": [r["i_out"] for r in lines],
            }
        return summary

    def _log_summary(self, path: str) -> dict[str, Any]:
        """Read a JSONL log and return summary stats without loading all rows."""
        import json as _json
        from pathlib import Path as _Path
        p = _Path(path)
        if not p.exists():
            return {"path": path, "samples": 0}
        rows: list[dict] = []
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(_json.loads(line))
                except Exception:
                    pass
        if not rows:
            return {"path": path, "samples": 0}
        ts = [r["ts"]    for r in rows]
        vs = [r["v_out"] for r in rows]
        is_ = [r["i_out"] for r in rows]
        duration_s = ts[-1] - ts[0] if len(ts) > 1 else 0.0
        ps = [v * i for v, i in zip(vs, is_)]
        avg_p = sum(ps) / len(ps) if ps else 0.0
        wh = avg_p * duration_s / 3600.0
        return {
            "path":       path,
            "samples":    len(rows),
            "duration_s": round(duration_s, 2),
            "peak_i_a":   round(max(is_), 4),
            "avg_i_a":    round(sum(is_) / len(is_), 4),
            "avg_v_v":    round(sum(vs)  / len(vs),  3),
            "total_wh":   round(wh, 6),
        }

    # Register addresses for fast single-call reads (FC03)
    # One FC03 call reads regs 10-18: V_OUT, I_OUT, AH, P_OUT, V_IN, KEYPAD, OVP_OCP, CV_CC, OUTPUT
    _REG_V_OUT   = 10
    _REG_I_OUT   = 11
    _REG_OVP_OCP = 16  # 0=none, 1=OVP, 2=OCP
    _REG_CV_CC   = 17  # 0=CV, 1=CC
    _REG_OUTPUT  = 18  # 0=off, 1=on
    _REG_BLOCK_COUNT = 9  # regs 10..18 inclusive

    def log_current_start(
        self,
        path: str,
        interval_ms: int = 100,
        v_thresh: float = 0.01,
        i_thresh: float = 0.005,
    ) -> None:
        """Start a fast current+voltage logging loop.

        Reads only V_OUT and I_OUT registers in a single FC03 call (2 regs at
        reg 10). Much faster than a full psu.update() status poll — suitable
        for graphing inrush / CC transitions at 10–100 ms resolution.

        Only writes a row when V or I changes by more than the threshold,
        keeping files small during steady-state operation.

        Writes JSONL to *path*:
          {"ts": <unix float>, "v_out": <V>, "i_out": <A>,
           "cv_cc": "CV"|"CC", "protect": "none"|"OVP"|"OCP", "output": true|false}
        Call log_stop() to stop.
        """
        def _run() -> None:
            profile = self._serial_profile
            if profile is not None:
                recommended = int(profile.get("recommended_poll_ms", interval_ms))
                if interval_ms < recommended:
                    log.info(
                        "requested interval %d ms is below recommended stable pacing %d ms; clamping",
                        interval_ms,
                        recommended,
                    )
                    interval_ms_local = recommended
                else:
                    interval_ms_local = interval_ms
            else:
                interval_ms_local = interval_ms

            self._stop_log()
            self._log_stop.clear()
            self._log_path = path
            with self._log_lock:
                self._log_file = open(path, "a")
            self._log_thread = threading.Thread(
                target=self._current_log_loop,
                args=(interval_ms_local, v_thresh, i_thresh),
                daemon=True,
                name="riden-ilog",
            )
            self._log_thread.start()
            log.info(
                "current logging started → %s every %d ms (v_thresh=%.3f i_thresh=%.4f)",
                path, interval_ms_local, v_thresh, i_thresh,
            )

        self._execute("log_current_start", _run)

    def _current_log_loop(self, interval_ms: int, v_thresh: float, i_thresh: float) -> None:
        """Read regs 10-18 in one FC03 call; write JSONL only on change exceeding thresholds.

        Each row includes: v_out, i_out, cv_cc (CV/CC), protect (none/OVP/OCP), output (bool).
        State changes in cv_cc, protect, or output are always written regardless of thresholds.
        """
        interval_s = interval_ms / 1000.0
        last_v: float | None = None
        last_i: float | None = None
        last_state: tuple | None = None  # (cv_cc, protect, output)
        _prot_map = {0: "none", 1: "OVP", 2: "OCP"}
        while not self._log_stop.wait(interval_s):
            try:
                with self._lock:
                    psu = self._assert_connected()
                    raw = psu.transport.read(self._REG_V_OUT, self._REG_BLOCK_COUNT)
                v_out   = round(psu.get_v_out(raw[0]), 3)
                i_out   = round(psu.get_i_out(raw[1]), 4)
                cv_cc   = "CV" if raw[7] == 0 else "CC"   # reg 17 = offset 7
                protect = _prot_map.get(raw[6], "none")    # reg 16 = offset 6
                output  = bool(raw[8])                      # reg 18 = offset 8
                state   = (cv_cc, protect, output)
                vi_changed = (
                    last_v is None
                    or abs(v_out - last_v) >= v_thresh
                    or abs(i_out - last_i) >= i_thresh
                )
                state_changed = (state != last_state)
                if vi_changed or state_changed:
                    last_v, last_i, last_state = v_out, i_out, state
                    row = {
                        "ts":      time.time(),
                        "v_out":   v_out,
                        "i_out":   i_out,
                        "cv_cc":   cv_cc,
                        "protect": protect,
                        "output":  output,
                    }
                    line = json.dumps(row) + "\n"
                    with self._log_lock:
                        if self._log_file:
                            self._log_file.write(line)
                            self._log_file.flush()
            except Exception as exc:
                log.warning("current log loop error: %s", exc)

    # Waveform shapes supported by waveform() / sine_wave()
    WAVEFORMS = ("sine", "triangle", "sawtooth", "square")

    def waveform(
        self,
        shape: str = "sine",
        v_center: float = 6.0,
        v_amplitude: float = 6.0,
        freq_hz: float = 0.1,
        duration_s: float = 60.0,
        step_s: float = 0.5,
        duty_cycle: float = 0.5,
    ) -> dict[str, Any]:
        """Drive a periodic voltage waveform through the existing serial connection.

        Shapes:
          sine      - smooth sinusoid
          triangle  - linear ramp up then down (symmetric)
          sawtooth  - linear ramp up, instant reset each period
          square    - high/low levels using duty_cycle fraction high
        """
        import math

        shape = shape.lower().strip()
        if shape not in self.WAVEFORMS:
            raise ValueError(f"unknown waveform shape '{shape}'; choose from {self.WAVEFORMS}")
        if freq_hz <= 0:
            raise ValueError("freq_hz must be > 0")
        if duration_s <= 0:
            raise ValueError("duration_s must be > 0")
        if step_s <= 0:
            raise ValueError("step_s must be > 0")
        if not 0.0 < duty_cycle < 1.0:
            raise ValueError("duty_cycle must be between 0 and 1")

        v_max = v_center + v_amplitude
        n = 0
        vmin: float = 9999.0
        vmax: float = -9999.0
        errs = 0
        t0 = time.perf_counter()

        def _sample(elapsed: float) -> float:
            phase = (elapsed * freq_hz) % 1.0  # 0..1 within one period
            if shape == "sine":
                raw = math.sin(2 * math.pi * phase)
            elif shape == "triangle":
                raw = 4.0 * phase - 1.0 if phase < 0.5 else 3.0 - 4.0 * phase
            elif shape == "sawtooth":
                raw = 2.0 * phase - 1.0
            else:  # square
                raw = 1.0 if phase < duty_cycle else -1.0
            return max(0.0, min(v_max, v_center + v_amplitude * raw))

        def _start() -> None:
            with self._lock:
                psu = self._assert_connected()
                self._set_output(psu, True)

        def _set_v(v: float) -> None:
            with self._lock:
                psu = self._assert_connected()
                psu.set_v_set(v)

        def _finish() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                psu.set_v_set(v_center + v_amplitude)
                self._set_output(psu, False)
                return self._read_status(psu)

        def _run() -> dict[str, Any]:
            nonlocal n, vmin, vmax, errs
            _start()
            try:
                while True:
                    elapsed = time.perf_counter() - t0
                    if elapsed >= duration_s:
                        break
                    v = _sample(elapsed)
                    try:
                        _set_v(v)
                        n += 1
                        vmin = min(vmin, v)
                        vmax = max(vmax, v)
                    except Exception as exc:
                        errs += 1
                        log.warning("waveform write error (%d): %s", errs, exc)
                        if errs > 5:
                            raise
                    target = t0 + n * step_s
                    sl = target - time.perf_counter()
                    if sl > 0:
                        time.sleep(sl)
            finally:
                final = _finish()

            rt = time.perf_counter() - t0
            return {
                "ok": True,
                "shape": shape,
                "samples": n,
                "duration_s": round(rt, 2),
                "rate_hz": round(n / rt, 3) if rt > 0 else 0.0,
                "min_v": round(vmin, 3),
                "max_v": round(vmax, 3),
                "write_errs": errs,
                "final_state": final,
            }

        return self._execute("waveform", _run)

    # ------------------------------------------------------------------
    # Lamp / load characterisation
    # ------------------------------------------------------------------

    def inrush_capture(
        self,
        voltage: float = 12.0,
        max_current: float = 6.0,
        duration_s: float = 4.0,
        path: str = "/tmp/mr11_inrush.jsonl",
    ) -> dict[str, Any]:
        """Capture start-up inrush current for a lamp or capacitive load.

        Turns output OFF, sets VSET/ISET, turns output ON, then samples
        V_OUT + I_OUT as fast as the Modbus link allows (one FC03 per loop)
        for *duration_s* seconds. Writes JSONL to *path*, turns output OFF.

        Returns summary stats: sample count, rate, peak current + time.
        """
        if voltage <= 0 or voltage > 60:
            raise ValueError(f"voltage must be 0–60 V, got {voltage}")
        if max_current <= 0 or max_current > 6:
            raise ValueError(f"max_current must be 0–6 A, got {max_current}")
        if duration_s <= 0:
            raise ValueError("duration_s must be > 0")

        def _run() -> dict[str, Any]:
            # --- setup: output off, set V/I ---
            with self._lock:
                psu = self._assert_connected()
                self._set_output(psu, False)
                psu.set_v_set(voltage)
                psu.set_i_set(max_current)

            time.sleep(0.05)

            samples: list[dict] = []
            peak_i = 0.0
            peak_t = 0.0

            with open(path, "w") as f:
                # Turn on then tight-loop sample
                with self._lock:
                    psu = self._assert_connected()
                    self._set_output(psu, True)

                t0 = time.monotonic()
                while True:
                    t_rel = time.monotonic() - t0
                    if t_rel >= duration_s:
                        break
                    try:
                        with self._lock:
                            psu = self._assert_connected()
                            raw = psu.transport.read(self._REG_V_OUT, 2)
                        v_out = round(psu.get_v_out(raw[0]), 3)
                        i_out = round(psu.get_i_out(raw[1]), 4)
                        row = {
                            "ts": time.time(),
                            "t": round(t_rel, 4),
                            "v_out": v_out,
                            "i_out": i_out,
                        }
                        samples.append(row)
                        f.write(json.dumps(row) + "\n")
                        f.flush()
                        if i_out > peak_i:
                            peak_i = i_out
                            peak_t = t_rel
                    except Exception as exc:
                        log.warning("inrush sample error: %s", exc)

            with self._lock:
                psu = self._assert_connected()
                self._set_output(psu, False)

            elapsed = samples[-1]["t"] if samples else 0.0
            rate = len(samples) / elapsed if elapsed > 0 else 0.0
            log.info(
                "inrush capture done: %d samples in %.2f s (%.1f Hz), "
                "peak %.3f A @ %.1f ms",
                len(samples), elapsed, rate, peak_i, peak_t * 1000,
            )
            return {
                "ok": True,
                "path": path,
                "samples": len(samples),
                "duration_s": round(elapsed, 3),
                "rate_hz": round(rate, 1),
                "peak_i_a": round(peak_i, 4),
                "peak_i_ms": round(peak_t * 1000, 1),
                "voltage": voltage,
                "max_current": max_current,
            }

        return self._execute("inrush_capture", _run)

    def vsweep(
        self,
        max_current: float = 6.0,
        v_max: float = 15.0,
        v_step: float = 0.25,
        dwell_ms: int = 300,
        path: str = "/tmp/mr11_vsweep.jsonl",
    ) -> dict[str, Any]:
        """Sweep output voltage 0 → v_max and record V+I at each step.

        Produces the load's VI characteristic curve. Writes JSONL to *path*.
        Returns summary and per-step data.
        """
        import math

        if max_current <= 0 or max_current > 6:
            raise ValueError(f"max_current must be 0–6 A, got {max_current}")
        if v_max <= 0 or v_max > 60:
            raise ValueError(f"v_max must be 0–60 V, got {v_max}")
        if v_step <= 0:
            raise ValueError("v_step must be > 0")
        if dwell_ms < 50:
            raise ValueError("dwell_ms must be ≥ 50")

        steps = []
        v = 0.0
        while v <= v_max + v_step / 2:
            steps.append(round(v, 4))
            v += v_step

        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                self._set_output(psu, False)
                psu.set_v_set(0.0)
                psu.set_i_set(max_current)

            time.sleep(0.05)

            with self._lock:
                psu = self._assert_connected()
                self._set_output(psu, True)

            rows: list[dict] = []

            with open(path, "w") as f:
                for v_set in steps:
                    with self._lock:
                        psu = self._assert_connected()
                        psu.set_v_set(v_set)
                    time.sleep(dwell_ms / 1000.0)
                    with self._lock:
                        psu = self._assert_connected()
                        raw = psu.transport.read(self._REG_V_OUT, 2)
                    v_out = round(psu.get_v_out(raw[0]), 3)
                    i_out = round(psu.get_i_out(raw[1]), 4)
                    row = {
                        "ts": time.time(),
                        "v_set": v_set,
                        "v_out": v_out,
                        "i_out": i_out,
                        "p_out": round(v_out * i_out, 3),
                    }
                    rows.append(row)
                    f.write(json.dumps(row) + "\n")
                    f.flush()

            with self._lock:
                psu = self._assert_connected()
                psu.set_v_set(0.0)
                self._set_output(psu, False)

            log.info("vsweep done: %d steps, max I=%.3f A", len(rows),
                     max((r["i_out"] for r in rows), default=0))
            return {
                "ok": True,
                "path": path,
                "steps": len(rows),
                "v_max": v_max,
                "max_current": max_current,
                "peak_i_a": max((r["i_out"] for r in rows), default=0),
                "peak_p_w": max((r["p_out"] for r in rows), default=0),
            }

        return self._execute("vsweep", _run)

    def sine_wave(
        self,
        v_center: float = 6.0,
        v_amplitude: float = 6.0,
        freq_hz: float = 0.1,
        duration_s: float = 60.0,
        step_s: float = 0.5,
    ) -> dict[str, Any]:
        """Convenience alias for waveform(shape='sine', ...)."""
        return self.waveform(
            shape="sine",
            v_center=v_center,
            v_amplitude=v_amplitude,
            freq_hz=freq_hz,
            duration_s=duration_s,
            step_s=step_s,
        )

    def list_parameters(self) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                psu.update()
                return {
                    "device": self._device_info(psu),
                    "parameters": self._list_parameters_locked(psu),
                }

        return self._execute("list_parameters", _run)

    def get_parameter(self, name: str) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                psu.update()
                value = self._get_parameter_locked(psu, name)
                return {
                    "name": name,
                    "value": value,
                    "status": self._read_status(psu),
                }

        return self._execute("get_parameter", _run)

    def set_parameter(self, name: str, value: Any) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                self._set_parameter_locked(psu, name, value)
                return {
                    "ok": True,
                    "name": name,
                    "value": self._get_parameter_locked(psu, name),
                    "status": self._read_status(psu),
                }

        return self._execute("set_parameter", _run)

    def beep(self, on: bool = True) -> dict[str, Any]:
        """Best-effort buzzer control when exposed by model/driver."""
        def _run() -> dict[str, Any]:
            with self._lock:
                psu = self._assert_connected()
                self._set_parameter_locked(psu, "buzzer_enabled", on)
                return {
                    "ok": True,
                    "supported": True,
                    "buzzer_enabled": self._get_parameter_locked(psu, "buzzer_enabled"),
                }

        try:
            return self._execute("beep", _run)
        except ValueError:
            return {"ok": False, "supported": False, "reason": "buzzer control not supported by this model/firmware"}

    def modbus_read_holding(self, start_register: int, count: int = 1) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            if count < 1 or count > 125:
                raise ValueError("count must be 1..125")
            with self._lock:
                psu = self._assert_connected()
                values = psu.read(int(start_register), int(count))
                if count == 1:
                    values = [int(values)]
                else:
                    values = [int(v) for v in values]
                return {
                    "start_register": int(start_register),
                    "count": int(count),
                    "values": values,
                }

        return self._execute("modbus_read_holding", _run)

    def modbus_write_register(self, register: int, value: int) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            if register < 0 or register > 0xFFFF:
                raise ValueError("register must be 0..65535")
            if value < 0 or value > 0xFFFF:
                raise ValueError("value must be 0..65535")
            with self._lock:
                psu = self._assert_connected()
                echo = int(psu.write(int(register), int(value)))
                return {
                    "register": int(register),
                    "value": int(value),
                    "echo": echo,
                }

        return self._execute("modbus_write_register", _run)

    def capabilities(self) -> dict[str, Any]:
        with self._lock:
            psu = self._assert_connected()
            psu.update()
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
                "sine_wave",
                "waveform",
                "list_parameters",
                "get_parameter",
                "set_parameter",
                "beep",
                "modbus_read_holding",
                "modbus_write_register",
                "profile_serial",
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
            "transport_active": "bluetooth-serial" if "rfcomm" in self._port else "usb-serial",
            "device": self._device_info(psu),
            "mcp_properties": {
                "serial_profile_available": True,
                "recommended_poll_ms": (
                    None if self._serial_profile is None else self._serial_profile.get("recommended_poll_ms")
                ),
            },
            "serial_profile": self._serial_profile,
            "waveforms": list(self.WAVEFORMS),
            "parameters": self._list_parameters_locked(psu),
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
                "serial_profile": self._serial_profile,
            }

    def speed_test(self, count: int = 30) -> dict[str, Any]:
        """Benchmark Modbus RTU round-trip latency (FC03 read of 9 registers).

        Performs *count* back-to-back reads with no sleep between them,
        measures wall-clock time for each, and returns summary statistics.
        Uses raw serial to bypass pymodbus/ch341 exclusive lock issues.
        """
        import statistics as _stats
        from riden_transport import SerialTransport

        REG_START = 10
        REG_COUNT = 9

        tr = SerialTransport(self._port, self._baud, self._address, use_raw_serial=True)
        try:
            tr.open()
        except IOError:
            # If raw serial fails, try pymodbus
            tr._use_raw_serial = False
            tr.open()
        
        try:
            times_ms: list[float] = []
            for _ in range(count):
                t0 = time.perf_counter()
                tr.read(REG_START, REG_COUNT)
                times_ms.append((time.perf_counter() - t0) * 1000)
        finally:
            tr.close()

        return {
            "transport":    "raw_serial" if tr._use_raw_serial else "pymodbus",
            "port":         self._port,
            "baud":         self._baud,
            "count":        count,
            "reg_start":    REG_START,
            "reg_count":    REG_COUNT,
            "min_ms":       round(min(times_ms), 2),
            "median_ms":    round(_stats.median(times_ms), 2),
            "mean_ms":      round(_stats.mean(times_ms), 2),
            "max_ms":       round(max(times_ms), 2),
            "stdev_ms":     round(_stats.stdev(times_ms), 2) if count > 1 else 0.0,
            "poll_hz":      round(1000 / _stats.median(times_ms), 2),
        }

