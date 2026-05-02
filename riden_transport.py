# riden_transport.py — transport abstraction for Riden RD60xx / RK60xx PSUs.
#
# Provides a hardware-independent Modbus RTU interface so riden_daemon.py
# does not depend on any Modbus library at runtime beyond pymodbus + pyserial.
#
# Implemented:
#   SerialTransport  — USB serial / BT serial via pymodbus + pyserial
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
import struct
import time
from pathlib import Path

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException
from serial import Serial


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

    Uses pymodbus ModbusSerialClient (actively maintained, Python 3.10-3.14).
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
        use_raw_serial: bool = False,
    ) -> None:
        self._port    = port
        self._baud    = baud
        self._address = address
        self._retries = retries
        self._timeout = timeout
        self._use_raw_serial = use_raw_serial
        self._client: ModbusSerialClient | None = None
        self._serial: Serial | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _is_ch341(port: str) -> bool:
        """Return True if port is backed by a CH341 USB serial chip (VID 1a86)."""
        try:
            # Resolve the sysfs path from the tty device name.
            devname = Path(port).name  # e.g. ttyUSB0
            sysfs = Path(f"/sys/bus/usb-serial/devices/{devname}")
            if not sysfs.exists():
                return False
            # Walk up to find the USB device directory with idVendor.
            usb_dev = sysfs.resolve().parents[2]  # .../1-7.4:1.0 -> .../1-7.4
            vendor_file = usb_dev / "idVendor"
            if vendor_file.exists():
                return vendor_file.read_text().strip().lower() == "1a86"
        except Exception:
            pass
        return False

    def open(self) -> None:
        # Auto-prefer raw serial for CH341 chips — they don't support TIOCEXCL
        # and pymodbus will always fail with EAGAIN before we can even connect.
        if self._use_raw_serial or self._is_ch341(self._port):
            self._open_raw_serial()
        else:
            self._open_pymodbus()
    
    def _open_raw_serial(self) -> None:
        """Open raw serial connection (for ch341 adapters without exclusive lock support)."""
        try:
            self._serial = Serial(
                self._port,
                self._baud,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=self._timeout,
            )
            # CH341 sends a spurious line-status frame on open; flush it.
            time.sleep(0.05)
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
        except Exception as e:
            raise IOError(f"failed to open raw serial {self._port}: {e}")
    
    def _open_pymodbus(self) -> None:
        """Open pymodbus connection, fall back to raw serial on exclusive lock error."""
        try:
            self._client = ModbusSerialClient(
                port=self._port,
                baudrate=self._baud,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=self._timeout,
                retries=self._retries,
            )
            if not self._client.connect():
                raise IOError(f"pymodbus failed to connect to {self._port}")
        except Exception as e:
            # Ch341 USB adapters may not support exclusive locking — fall back to raw serial
            error_msg = str(e).lower()
            # Check exception chain as well
            exc_chain = []
            curr = e
            while curr:
                exc_chain.append(str(curr).lower())
                curr = getattr(curr, '__cause__', None)
            all_msgs = " | ".join(exc_chain)
            
            if ("exclusive" in all_msgs or "errno 11" in all_msgs or 
                "resource temporarily unavailable" in all_msgs):
                self._client = None
                self._open_raw_serial()
            else:
                raise IOError(f"failed to connect to {self._port}: {e}")

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
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
        if self._client is not None:
            return self._read_pymodbus(register, count)
        elif self._serial is not None:
            return self._read_raw_serial(register, count)
        else:
            raise IOError("transport not open")
    
    def _read_pymodbus(self, register: int, count: int) -> tuple[int, ...]:
        last_exc: Exception | None = None
        for _ in range(self._retries):
            try:
                resp = self._client.read_holding_registers(
                    address=register, count=count, device_id=self._address
                )
                if resp.isError():
                    raise ModbusException(f"read error at reg={register}: {resp}")
                return tuple(resp.registers)
            except Exception as exc:
                last_exc = exc
        raise TimeoutError(
            f"modbus read reg={register} count={count} failed after {self._retries} retries"
        ) from last_exc
    
    def _read_raw_serial(self, register: int, count: int) -> tuple[int, ...]:
        """Raw Modbus RTU FC03 read (handles ch341 exclusive lock issue)."""
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                # Flush any stale bytes before each attempt (CH341 can leave
                # line-status bytes in the buffer between transactions).
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
                # Brief pause before retry so CH341 settles.
                time.sleep(0.02)
        raise TimeoutError(
            f"raw serial read reg={register} count={count} failed after {self._retries} retries"
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
        if self._client is not None:
            last_exc: Exception | None = None
            for _ in range(self._retries):
                try:
                    resp = self._client.write_register(
                        address=register, value=value, device_id=self._address
                    )
                    if resp.isError():
                        raise ModbusException(f"write error at reg={register}: {resp}")
                    return
                except Exception as exc:
                    last_exc = exc
            raise TimeoutError(
                f"modbus write reg={register} value={value} failed after {self._retries} retries"
            ) from last_exc

        if self._serial is not None:
            last_exc: Exception | None = None
            for _ in range(self._retries):
                try:
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
                f"raw serial write reg={register} value={value} failed after {self._retries} retries"
            ) from last_exc

        raise IOError("transport not open")

    def write_multiple(self, register: int, values: tuple | list) -> None:
        if self._client is not None:
            last_exc: Exception | None = None
            for _ in range(self._retries):
                try:
                    resp = self._client.write_registers(
                        address=register, values=list(values), device_id=self._address
                    )
                    if resp.isError():
                        raise ModbusException(f"write_multiple error at reg={register}: {resp}")
                    return
                except Exception as exc:
                    last_exc = exc
            raise TimeoutError(
                f"modbus write_multiple reg={register} count={len(values)} failed after {self._retries} retries"
            ) from last_exc

        if self._serial is not None:
            vals = list(values)
            if not vals:
                return
            if len(vals) == 1:
                self.write(register, int(vals[0]))
                return

            last_exc: Exception | None = None
            qty = len(vals)
            byte_count = qty * 2
            payload = struct.pack(">BBHHB", self._address, 0x10, register, qty, byte_count)
            payload += b"".join(struct.pack(">H", int(v) & 0xFFFF) for v in vals)
            req = payload + struct.pack("<H", self._crc16(payload))
            for _ in range(self._retries):
                try:
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
                f"raw serial write_multiple reg={register} count={len(vals)} failed after {self._retries} retries"
            ) from last_exc

        raise IOError("transport not open")


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
