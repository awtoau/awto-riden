# riden_transport.py — transport abstraction for Riden RD60xx / RK60xx PSUs.
#
# Provides a hardware-independent Modbus RTU interface so riden_daemon.py
# does not depend on any Modbus library at runtime beyond pyserial.
#
# Implemented:
#   SerialTransport  — USB serial / BT serial via raw Modbus RTU (pyserial only)
#
# Stubs (not yet implemented):
#   TcpTransport     — Modbus TCP or pyserial socket:// URL (WiFi bridge)
#   BleTransport     — Bleak BLE (RK6006-BT native BLE)
#
# Register-level protocol derived from:
#   Baldanos/rd6006 (Apache-2.0)  https://github.com/Baldanos/rd6006
#   ShayBox/Riden   (MIT)         https://github.com/awto-au/riden
# See ATTRIBUTION.md for full lineage.

from __future__ import annotations

from abc import ABC, abstractmethod
import importlib.util
import struct
import time

import logging

from serial import Serial
from serial.tools import list_ports

import os as _os

log = logging.getLogger("riden.transport")


def _load_vendor_known_devices() -> dict[tuple[int, int], dict]:
    """Load KNOWN_DEVICES from vendor/awto-mcp-serial/protocol.py safely.

    Uses an explicit module spec to avoid name collisions with this repo's
    top-level protocol.py.
    """
    _vendor_protocol = _os.path.join(
        _os.path.dirname(__file__), "vendor", "awto-mcp-serial", "protocol.py",
    )
    if not _os.path.exists(_vendor_protocol):
        log.warning(
            "vendor protocol not found at %s; continuing with empty KNOWN_DEVICES "
            "(run 'git submodule update --init --recursive' for full detection)",
            _vendor_protocol,
        )
        return {}

    spec = importlib.util.spec_from_file_location("awto_mcp_serial_protocol", _vendor_protocol)
    if spec is None or spec.loader is None:
        log.warning(
            "failed to load vendor protocol module from %s; continuing with empty KNOWN_DEVICES",
            _vendor_protocol,
        )
        return {}

    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        known = getattr(module, "KNOWN_DEVICES", None)
        if isinstance(known, dict):
            return known
        log.warning(
            "vendor protocol module at %s has no KNOWN_DEVICES dict; using empty fallback",
            _vendor_protocol,
        )
    except Exception as exc:
        log.warning(
            "error loading vendor protocol from %s: %s; continuing with empty KNOWN_DEVICES",
            _vendor_protocol,
            exc,
        )
    return {}


KNOWN_DEVICES = _load_vendor_known_devices()


# ---------------------------------------------------------------------------
# Serial port auto-detection for Riden PSUs
# ---------------------------------------------------------------------------

# CH340/341/343 VID used by QinHeng — the chipset in Riden PSUs.
_RIDEN_VID = 0x1A86

def find_riden_port(fallback: str = "/dev/ttyUSB0") -> tuple[str, int]:
    """Return (device_path, baud) for the most likely Riden PSU serial port.

    Scoring (highest wins):
      3 — VID matches QinHeng (0x1A86) AND PID in KNOWN_DEVICES with Riden note
      2 — VID matches QinHeng (0x1A86) — any CH340/341/343 chip
      1 — device path is ttyUSB* (generic USB-serial fallback)
      0 — ttyACM* or other

    Returns the fallback path at baud 115200 if no ports are found.
    """
    best_device = fallback
    best_baud   = 115_200
    best_score  = -1

    for port in list_ports.comports():
        vid = port.vid
        pid = port.pid
        score = 0

        if vid == _RIDEN_VID:
            info = KNOWN_DEVICES.get((vid, pid))
            if info and "Riden" in info.get("notes", ""):
                score = 3
                baud = info["typical_baud"]
            else:
                score = 2
                info2 = KNOWN_DEVICES.get((vid, pid))
                baud = info2["typical_baud"] if info2 else 115_200
        elif "ttyUSB" in port.device:
            score = 1
            baud = 115_200
        else:
            baud = 115_200

        if score > best_score:
            best_score  = score
            best_device = port.device
            best_baud   = baud

    return best_device, best_baud


def list_serial_ports() -> list[dict]:
    """Return info dicts for all detected serial ports, with Riden scoring."""
    results = []
    for port in sorted(list_ports.comports(), key=lambda p: p.device):
        vid = port.vid
        pid = port.pid
        info = KNOWN_DEVICES.get((vid, pid)) if vid and pid else None
        results.append({
            "device":      port.device,
            "description": port.description,
            "vid":         f"0x{vid:04X}" if vid else None,
            "pid":         f"0x{pid:04X}" if pid else None,
            "chip":        info["chip"] if info else None,
            "typical_baud": info["typical_baud"] if info else None,
            "notes":       info["notes"] if info else None,
        })
    return results


# ---------------------------------------------------------------------------
# Model detection helpers
# (extracted from ShayBox/Riden Riden.__init__ — MIT licence)
# ---------------------------------------------------------------------------

def _model_info(device_id: int) -> dict:
    """Return model type string and v/i/p multipliers for a given device ID."""
    if device_id >= 60241:
        return dict(type="RD6024", v_multi=100, i_multi=100, p_multi=100)
    if 60180 <= device_id <= 60189:
        return dict(type="RD6018", v_multi=100, i_multi=100, p_multi=100)
    if 60120 <= device_id <= 60124:
        return dict(type="RD6012", v_multi=100, i_multi=100, p_multi=100)
    if 60125 <= device_id <= 60129:
        # i_multi is dynamic (depends on I_RANGE register) — caller must check
        return dict(type="RD6012P", v_multi=1000, i_multi=1000, p_multi=1000)
    if 60060 <= device_id <= 60064:
        return dict(type="RD6006", v_multi=100, i_multi=1000, p_multi=100)
    if device_id == 60065:
        return dict(type="RD6006P", v_multi=1000, i_multi=10000, p_multi=1000)
    if device_id == 60066:
        return dict(type="RK6006", v_multi=100, i_multi=1000, p_multi=100)
    return dict(type="unknown", v_multi=100, i_multi=1000, p_multi=100)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class RidenTransport(ABC):
    """Minimal Modbus RTU interface over any physical link."""

    @abstractmethod
    def open(self) -> None:
        """Open the transport (connect serial port, TCP socket, BLE, …)."""

    @abstractmethod
    def close(self) -> None:
        """Release the transport."""

    @property
    @abstractmethod
    def address(self) -> int:
        """Modbus slave address."""

    @abstractmethod
    def read(self, register: int, count: int = 1) -> tuple[int, ...]:
        """Read *count* holding registers starting at *register*.

        Returns a tuple of raw integer register values (always a tuple,
        even for count=1, for consistency).
        """

    @abstractmethod
    def write(self, register: int, value: int) -> None:
        """Write a single holding register."""

    @abstractmethod
    def write_multiple(self, register: int, values: tuple | list) -> None:
        """Write a contiguous block of holding registers."""


# ---------------------------------------------------------------------------
# Serial transport (USB serial / Bluetooth RFCOMM)
# ---------------------------------------------------------------------------

class SerialTransport(RidenTransport):
    """Modbus RTU over a serial port (USB or BT RFCOMM).

    Uses raw pyserial with hand-built Modbus RTU frames (FC03/FC06/FC16).
    Avoids pymodbus transaction management overhead — no library framer,
    no transaction state machine.
    Raises TimeoutError after exhausting retries instead of recursing forever
    on flaky links.

    Args:
        port:     Serial device path, e.g. '/dev/ttyUSB0' or '/dev/rfcomm0'.
        baud:     Baud rate (default 115200).
        address:  Modbus slave address (default 1).
        retries:  Number of attempts before raising TimeoutError (default 3).
        timeout:  Serial read timeout in seconds (default 0.5).
    """

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        address: int = 1,
        retries: int = 3,
        timeout: float = 0.5,
    ) -> None:
        self._port    = port
        self._baud    = baud
        self._address = address
        self._retries = retries
        self._timeout = timeout
        self._serial: Serial | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        try:
            self._serial = Serial(
                self._port,
                self._baud,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=self._timeout,
            )
            # Flush any spurious bytes that arrive on open (e.g. CH34x
            # line-status frames on first open).
            time.sleep(0.05)
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
        except Exception as e:
            raise IOError(f"failed to open {self._port}: {e}")

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    @property
    def address(self) -> int:
        return self._address

    @property
    def port(self) -> str:
        return self._port

    @property
    def baud(self) -> int:
        return self._baud

    # ------------------------------------------------------------------
    # Modbus operations
    # ------------------------------------------------------------------
    # NOTE: measured RTT is ~143 ms regardless of register count or baud rate.
    # This is the RD6006 firmware scan period (~7 Hz). The device only refreshes
    # its Modbus registers once per firmware cycle and queues replies behind that
    # cycle, so there is no way to get a faster response at the protocol level.
    # After a write(), wait at least 2 × 143 ms (~300 ms) before reading back
    # a settled measurement.

    def read(self, register: int, count: int = 1) -> tuple[int, ...]:
        if self._serial is None:
            raise IOError("transport not open")
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                # Flush stale bytes before each attempt — handles CH34x
                # line-status frames and any residual data on the bus.
                self._serial.reset_input_buffer()
                request = self._build_fc03_request(register, count)
                self._serial.write(request)
                expected_bytes = 5 + count * 2
                response = self._serial.read(expected_bytes)
                if len(response) < expected_bytes:
                    raise IOError(f"short response: {len(response)} bytes (attempt {attempt+1})")
                regs = self._parse_fc03_response(response, count)
                if regs is None:
                    raise IOError("FC03 response parsing failed")
                return tuple(regs)
            except Exception as exc:
                last_exc = exc
                time.sleep(0.02)
        raise TimeoutError(
            f"serial read reg={register} count={count} failed after {self._retries} retries"
        ) from last_exc

    @staticmethod
    def _crc16(data: bytes) -> int:
        """Modbus CRC-16 (RTU)."""
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    def _build_fc03_request(self, register: int, count: int) -> bytes:
        """Build Modbus RTU FC03 (read holding registers) request."""
        pdu = struct.pack(">BBHH", self._address, 0x03, register, count)
        crc = self._crc16(pdu)
        return pdu + struct.pack("<H", crc)

    def _parse_fc03_response(self, data: bytes, expected_regs: int) -> list[int] | None:
        """Parse FC03 response, return register list or None on error."""
        expected_len = 5 + expected_regs * 2
        if len(data) < expected_len:
            return None
        body = data[:-2]
        recv_crc = struct.unpack("<H", data[-2:])[0]
        calc_crc = self._crc16(body)
        if recv_crc != calc_crc:
            return None
        func_code = data[1]
        if func_code != 0x03:
            return None
        regs = list(struct.unpack_from(f">{expected_regs}H", data, 3))
        return regs

    def write(self, register: int, value: int) -> None:
        if self._serial is None:
            raise IOError("transport not open")
        last_exc: Exception | None = None
        for _ in range(self._retries):
            try:
                self._serial.reset_input_buffer()
                pdu = struct.pack(">BBHH", self._address, 0x06, register, value)
                req = pdu + struct.pack("<H", self._crc16(pdu))
                self._serial.write(req)
                resp = self._serial.read(8)
                if len(resp) != 8:
                    raise IOError(f"short FC06 response: {len(resp)} bytes")
                if self._crc16(resp[:-2]) != struct.unpack("<H", resp[-2:])[0]:
                    raise IOError("FC06 response CRC mismatch")
                if resp[:6] != req[:6]:
                    raise IOError("FC06 echo mismatch")
                return
            except Exception as exc:
                last_exc = exc
        raise TimeoutError(
            f"serial write reg={register} value={value} failed after {self._retries} retries"
        ) from last_exc

    def write_multiple(self, register: int, values: tuple | list) -> None:
        if self._serial is None:
            raise IOError("transport not open")
        vals = list(values)
        if not vals:
            return
        if len(vals) == 1:
            self.write(register, int(vals[0]))
            return
        qty = len(vals)
        byte_count = qty * 2
        payload = struct.pack(">BBHHB", self._address, 0x10, register, qty, byte_count)
        payload += b"".join(struct.pack(">H", int(v) & 0xFFFF) for v in vals)
        req = payload + struct.pack("<H", self._crc16(payload))
        last_exc: Exception | None = None
        for _ in range(self._retries):
            try:
                self._serial.reset_input_buffer()
                self._serial.write(req)
                resp = self._serial.read(8)
                if len(resp) != 8:
                    raise IOError(f"short FC16 response: {len(resp)} bytes")
                if self._crc16(resp[:-2]) != struct.unpack("<H", resp[-2:])[0]:
                    raise IOError("FC16 response CRC mismatch")
                addr, fn, start, echoed_qty = struct.unpack(">BBHH", resp[:6])
                if addr != self._address or fn != 0x10 or start != register or echoed_qty != qty:
                    raise IOError("FC16 response mismatch")
                return
            except Exception as exc:
                last_exc = exc
        raise TimeoutError(
            f"serial write_multiple reg={register} count={len(vals)} failed after {self._retries} retries"
        ) from last_exc


# ---------------------------------------------------------------------------
# TCP stub  (WiFi bridge / Modbus TCP gateway)
# ---------------------------------------------------------------------------

class TcpTransport(RidenTransport):
    """Modbus RTU tunnelled over TCP (e.g. serial-to-WiFi bridge).

    Not yet implemented — raises NotImplementedError on open().
    Placeholder for future pyserial socket:// URL support.
    """

    def __init__(self, host: str, port: int = 8080, address: int = 1) -> None:
        self._host    = host
        self._port    = port
        self._address = address

    def open(self) -> None:
        raise NotImplementedError("TcpTransport is not yet implemented")

    def close(self) -> None:
        pass

    @property
    def address(self) -> int:
        return self._address

    def read(self, register: int, count: int = 1) -> tuple[int, ...]:
        raise NotImplementedError("TcpTransport is not yet implemented")

    def write(self, register: int, value: int) -> None:
        raise NotImplementedError("TcpTransport is not yet implemented")

    def write_multiple(self, register: int, values: tuple | list) -> None:
        raise NotImplementedError("TcpTransport is not yet implemented")


# ---------------------------------------------------------------------------
# BLE stub  (RK6006-BT native BLE via bleak)
# ---------------------------------------------------------------------------

class BleTransport(RidenTransport):
    """Native BLE transport for RK6006-BT via bleak.

    Not yet implemented — raises NotImplementedError on open().
    See BLE_ROADMAP.md for the planned implementation.
    """

    def __init__(self, mac: str, address: int = 1) -> None:
        self._mac     = mac
        self._address = address

    def open(self) -> None:
        raise NotImplementedError("BleTransport is not yet implemented")

    def close(self) -> None:
        pass

    @property
    def address(self) -> int:
        return self._address

    def read(self, register: int, count: int = 1) -> tuple[int, ...]:
        raise NotImplementedError("BleTransport is not yet implemented")

    def write(self, register: int, value: int) -> None:
        raise NotImplementedError("BleTransport is not yet implemented")

    def write_multiple(self, register: int, values: tuple | list) -> None:
        raise NotImplementedError("BleTransport is not yet implemented")
