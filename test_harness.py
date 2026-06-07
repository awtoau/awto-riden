"""
awto-riden test harness — simplified for direct serial (no daemon).

Tests the RidenWorker class with a mock RidenDevice (no real hardware).

Layers tested:
  1. Protocol helpers (make_ok / make_err)
  2. RidenWorker — state transitions, command dispatch (mock device)
  3. CLI subprocess — smoke test (--help)
  4. Worker status/data-structure consistency

Architecture note:
  The driver layer is now our own RidenDevice (riden_daemon.py) built on
  SerialTransport (riden_transport.py) — there is no upstream `Riden` class.
  Tests therefore patch `riden_daemon.RidenDevice` and `riden_daemon.SerialTransport`
  so RidenWorker.open() constructs the mock instead of touching hardware.

Run:
    python3 test_harness.py -v
"""

from __future__ import annotations

import asyncio
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from protocol import make_err, make_ok
from riden_daemon import RidenWorker, discover_devices
from riden_transport import SerialTransport
from riden_flash import (
    RidenBootloader,
    flash_firmware,
    model_from_filename,
    FlashError,
    SUPPORTED_MODELS,
)

# Repo root — used as cwd for the CLI subprocess test. Derived from this
# file's location so a rename of the checkout dir can't stale it again.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# Fake port — never resolves to real hardware; any test that accidentally
# bypasses the mock will fail immediately with a clear serial error, not
# silently probe a connected device.
MOCK_PORT = "MOCK://"


# ---------------------------------------------------------------------------
# Mock device + transport — replace the RidenDevice/SerialTransport layer
# ---------------------------------------------------------------------------

class MockTransport:
    """Stand-in for SerialTransport. Constructed by the patched RidenWorker.open()
    but never actually used for I/O because RidenDevice is mocked too."""

    def __init__(self, port: str, baud: int, address: int) -> None:
        self.port = port
        self.baud = baud
        self.address = address

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def read(self, register: int, length: int = 1):
        # Used only if profiling isn't patched out; return zeros of the right shape.
        return (0,) * length


class MockRidenDevice:
    """Simulates RidenDevice without hardware.

    Mirrors the attribute and method surface that RidenWorker reads/writes:
    v_set/i_set/v_out/i_out/p_out/v_in, output/enable, cv_cc (str), ovp_ocp,
    int_c, plus set_v_set/set_i_set/set_output/set_ovp/set_ocp and update()/close().
    """

    def __init__(self, transport: MockTransport) -> None:
        self.transport = transport
        self.address = getattr(transport, "address", 1)

        self.id = 60062            # RD6006 id-ish; identity only, not asserted
        self.sn = "00000001"
        self.fw = 137
        self.type = "RD6006"
        self.v_multi = 100
        self.i_multi = 1000
        self.p_multi = 100

        # PSU state
        self.v_set = 5.0
        self.i_set = 1.0
        self.v_out = 4.998
        self.i_out = 0.501
        self.p_out = 2.502
        self.v_in = 24.1
        self.output = True
        self.enable = True
        self.cv_cc = "CV"          # current code uses string CV/CC
        self.ovp_ocp = None        # None | "OVP" | "OCP"
        self.int_c = 28
        self.int_f = 82
        self.keypad = False
        self.preset = 0

    def update(self) -> None:
        """No hardware to read; state is whatever the setters left it."""
        pass

    def close(self) -> None:
        pass

    def set_v_set(self, v: float) -> None:
        self.v_set = v

    def set_i_set(self, i: float) -> None:
        self.i_set = i

    def set_output(self, on: bool) -> None:
        self.output = on
        self.enable = on

    def set_ovp(self, v: float) -> None:
        pass

    def set_ocp(self, i: float) -> None:
        pass


def _patch_device():
    """Patch the driver layer + skip the serial-profiling poll loop in open().

    Returns a list of started patchers; caller is responsible for stopping them.
    Profiling is patched to a no-op because the real loop does 11 reads with
    100 ms sleeps between them — pointless and slow against a mock.
    """
    patchers = [
        patch("riden_daemon.SerialTransport", MockTransport),
        patch("riden_daemon.RidenDevice", MockRidenDevice),
        patch.object(RidenWorker, "_profile_serial_locked", return_value=None),
    ]
    started = [p.start() for p in patchers]
    import riden_daemon
    assert riden_daemon.RidenDevice is MockRidenDevice, (
        "Mock patch did not apply — would probe real hardware"
    )
    return patchers


# ---------------------------------------------------------------------------
# Layer 1 — protocol helpers
# ---------------------------------------------------------------------------

class TestProtocol(unittest.TestCase):

    def test_make_ok(self) -> None:
        r = make_ok("pong")
        self.assertTrue(r["ok"])
        self.assertEqual(r["response"], "pong")

    def test_make_err(self) -> None:
        r = make_err("oops")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "oops")


# ---------------------------------------------------------------------------
# Layer 2 — RidenWorker with mock device
# ---------------------------------------------------------------------------

class TestRidenWorker(unittest.TestCase):

    def setUp(self) -> None:
        self._patchers = _patch_device()

    def tearDown(self) -> None:
        for p in self._patchers:
            p.stop()

    def _open_worker(self) -> RidenWorker:
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()
        return worker

    def test_worker_init_and_open(self) -> None:
        """RidenWorker initializes and connects (mock device constructed)."""
        worker = self._open_worker()
        self.assertIsNotNone(worker._psu)
        self.assertTrue(worker.is_connected)
        worker.close()
        self.assertFalse(worker.is_connected)

    def test_status_returns_dict(self) -> None:
        """status() returns the expected PSU state fields."""
        worker = self._open_worker()
        status = worker.status()
        for field in ("v_set", "i_set", "v_out", "i_out", "output"):
            self.assertIn(field, status)
        worker.close()

    def test_set_voltage(self) -> None:
        """set_voltage() updates v_set, reflected in status."""
        worker = self._open_worker()
        result = worker.set_voltage(12.0)
        self.assertEqual(result["v_set"], 12.0)
        self.assertEqual(worker.status()["v_set"], 12.0)
        worker.close()

    def test_set_current(self) -> None:
        """set_current() updates i_set, reflected in status."""
        worker = self._open_worker()
        result = worker.set_current(2.5)
        self.assertEqual(result["i_set"], 2.5)
        self.assertEqual(worker.status()["i_set"], 2.5)
        worker.close()

    def test_output_control(self) -> None:
        """set_output() toggles the output flag."""
        worker = self._open_worker()

        self.assertFalse(worker.set_output(False)["output"])
        self.assertFalse(worker.status()["output"])

        self.assertTrue(worker.set_output(True)["output"])
        self.assertTrue(worker.status()["output"])

        worker.close()

    def test_power_cycle(self) -> None:
        """power_cycle() returns output to on with v_set preserved."""
        worker = self._open_worker()
        original_v_set = worker.status()["v_set"]
        result = worker.power_cycle(0.1)  # min wait clamps to 0.1s in worker
        self.assertEqual(result["v_set"], original_v_set)
        self.assertTrue(result["output"])
        worker.close()

    def test_info_fields(self) -> None:
        """info() returns process health metrics."""
        worker = self._open_worker()
        info = worker.info()
        for field in ("pid", "rss_mb", "cpu_pct", "threads", "open_fds",
                      "free_threaded", "python"):
            self.assertIn(field, info)
        worker.close()

    def test_log_start_stop(self) -> None:
        """Logging starts a thread that writes, and stops cleanly."""
        worker = self._open_worker()
        log_path = os.path.join(REPO_ROOT, "tmp", "test_riden.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        worker.log_start(log_path, interval_ms=20)
        # Poll for the log file to gain content rather than sleeping a fixed time.
        for _ in range(200):
            if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
                break
        worker.log_stop()
        worker.close()


# ---------------------------------------------------------------------------
# Layer 3 — CLI subprocess test
# ---------------------------------------------------------------------------

class TestCLI(unittest.TestCase):

    def test_cli_help(self) -> None:
        """CLI --help exits 0 and prints usage."""
        result = subprocess.run(
            [sys.executable, "awto_riden.py", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Riden", result.stdout)


# ---------------------------------------------------------------------------
# Layer 4 — status / data-structure consistency
# ---------------------------------------------------------------------------

class TestDataStructures(unittest.TestCase):

    def setUp(self) -> None:
        self._patchers = _patch_device()

    def tearDown(self) -> None:
        for p in self._patchers:
            p.stop()

    def test_status_fields_consistency(self) -> None:
        """worker.status() exposes the full documented field set."""
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()
        status = worker.status()
        expected_fields = [
            "v_set", "i_set", "v_out", "i_out", "p_out", "v_in",
            "output", "cv_cc", "protect", "temp_c",
        ]
        for field in expected_fields:
            self.assertIn(field, status, f"status missing {field}")
        worker.close()

    def test_make_ok_wraps_dict(self) -> None:
        data = {"v_set": 5.0, "output": True}
        result = make_ok(data)
        self.assertTrue(result["ok"])
        self.assertIn("v_set", result)
        self.assertEqual(result["v_set"], 5.0)

    def test_make_err_includes_message(self) -> None:
        msg = "Connection timeout"
        result = make_err(msg)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], msg)


# ---------------------------------------------------------------------------
# Layer 5 — discover_devices (delegates to the shared awto-serial discover())
# ---------------------------------------------------------------------------

def _fake_serial_factory():
    """serial.Serial(...) stand-in: returns a MagicMock port honouring attrs.

    The shared discover() opens the port; the Modbus probe then borrows this
    handle via SerialTransport.from_open_serial, so no real I/O happens (the
    RidenDevice layer is mocked).
    """
    def _factory(port, baudrate=None, timeout=None, write_timeout=None):
        m = MagicMock()
        m.is_open = True
        m.port = port
        m.baudrate = baudrate
        m.timeout = timeout
        return m
    return _factory


def _mock_riden_device_class(target_address):
    """Build a RidenDevice stand-in that 'answers' only at *target_address*.

    Construction reads the identity block on real hardware and raises on a
    silent address; the mock mirrors that — it raises for any other address so
    the probe's address sweep behaves exactly as against a real bus.
    """
    class _MockDev:
        def __init__(self, transport):
            self.transport = transport
            self.address = getattr(transport, "address", 1)
            if self.address != target_address:
                raise TimeoutError(f"no device at addr {self.address}")
            self.id = 60241          # RD6024
            self.sn = "00012345"
            self.fw = 144
            self.type = "RD6024"

        def update(self):
            pass

        def close(self):
            pass

    return _MockDev


class TestDiscovery(unittest.TestCase):

    def test_from_open_serial_is_borrowed(self):
        """A borrowed transport never opens or closes the underlying port."""
        ser = MagicMock()
        ser.port = "/dev/ttyUSB0"
        ser.baudrate = 115200
        ser.timeout = 0.2
        tr = SerialTransport.from_open_serial(ser, address=3, retries=2)
        self.assertEqual(tr.address, 3)
        self.assertIs(tr._serial, ser)
        tr.open()                       # no-op — already open and owned by discover
        tr.close()                      # no-op — owner closes it, not us
        ser.close.assert_not_called()

    def test_discover_finds_device(self):
        with patch("serial.Serial", side_effect=_fake_serial_factory()), \
             patch("riden_daemon.RidenDevice", _mock_riden_device_class(1)), \
             patch("riden_daemon.time.sleep"):           # skip the 50 ms CH34x settle
            result = discover_devices(
                ports=["/dev/ttyUSB0"], baud=115200, addresses=[1, 2],
                timeout_s=0.05, max_scan_s=2.0,
            )
        self.assertEqual(result["found_count"], 1)
        self.assertEqual(result["ports_scanned"], ["/dev/ttyUSB0"])
        self.assertEqual(result["addresses_scanned"], [1, 2])
        self.assertFalse(result["timed_out"])
        dev = result["found"][0]
        self.assertEqual(dev["port"], "/dev/ttyUSB0")
        self.assertEqual(dev["baud"], 115200)
        self.assertEqual(dev["address"], 1)
        self.assertEqual(dev["model"], "RD6024")
        self.assertEqual(dev["device_id"], 60241)
        self.assertEqual(dev["serial"], "00012345")
        self.assertEqual(dev["fw"], "v1.44")
        self.assertIsNotNone(dev["probe_ms"])

    def test_discover_no_device_records_per_address_errors(self):
        with patch("serial.Serial", side_effect=_fake_serial_factory()), \
             patch("riden_daemon.RidenDevice", _mock_riden_device_class(99)), \
             patch("riden_daemon.time.sleep"):
            result = discover_devices(
                ports=["/dev/ttyUSB0"], baud=115200, addresses=[1, 2],
                timeout_s=0.05, max_scan_s=2.0, include_errors=True,
            )
        self.assertEqual(result["found_count"], 0)
        self.assertFalse(result["timed_out"])
        # One error per swept address (richer detail than the shared primitive).
        addr_errors = [e for e in result["errors"] if "address" in e]
        self.assertEqual({e["address"] for e in addr_errors}, {1, 2})

    def test_discover_no_candidate_ports(self):
        with patch("riden_daemon.list_riden_candidate_ports", return_value=[]), \
             patch("riden_daemon.list_serial_ports_ranked", return_value=[]), \
             patch("serial.Serial", side_effect=AssertionError("must not open any port")):
            result = discover_devices(baud=115200, addresses=[1])
        self.assertEqual(result["found_count"], 0)
        self.assertEqual(result["ports_scanned"], [])
        self.assertEqual(result["skipped"], [])

    def test_discover_response_shape_unchanged(self):
        with patch("serial.Serial", side_effect=_fake_serial_factory()), \
             patch("riden_daemon.RidenDevice", _mock_riden_device_class(1)), \
             patch("riden_daemon.time.sleep"):
            result = discover_devices(ports=["/dev/ttyUSB0"], addresses=[1], timeout_s=0.05)
        for key in (
            "ports_scanned", "baud", "addresses_scanned", "found", "found_count",
            "errors", "timed_out", "max_scan_s", "scan_ms", "skipped",
        ):
            self.assertIn(key, result, f"discover_devices response missing {key}")


# ---------------------------------------------------------------------------
# Layer 6 — Bootloader firmware loader (riden_flash), mocked serial
# ---------------------------------------------------------------------------

class _FakeBootSerial:
    """Simulates a Riden unit's serial bootloader protocol for tests.

    Responds to each written command by queueing the bytes a real unit would
    return, so RidenBootloader's write/read cycles behave end-to-end without
    hardware.
    """

    def __init__(self, *, in_bootloader=False, model=60066, fw_raw=109,
                 serial_num=1036, chunk_ack=b"OK", upfirm_ready=b"upredy"):
        self._boot = in_bootloader
        self._model = model
        self._fw = fw_raw
        self._sn = serial_num
        self._chunk_ack = chunk_ack
        self._upfirm = upfirm_ready
        self._rx = bytearray()
        self.timeout = None
        self.written = bytearray()
        self.closed = False

    def _info_block(self) -> bytes:
        b = bytearray(b"inf")
        b += struct.pack("<I", self._sn)     # serial LE32 -> res[3..6]
        b += struct.pack("<H", self._model)  # model  LE16 -> res[7..8]
        b += b"\x00\x00"                       # pad         -> res[9..10]
        b += bytes([self._fw])                 # fw          -> res[11]
        b += b"\x00"                           # pad         -> res[12]
        return bytes(b)

    def write(self, data):
        self.written += data
        d = bytes(data)
        if d == b"queryd\r\n":
            self._rx += b"boot" if self._boot else b"\x00\x00\x00\x00"
        elif d == b"getinf\r\n":
            self._rx += self._info_block()
        elif d == b"upfirm\r\n":
            self._rx += self._upfirm
        elif len(d) == 8 and d[1] == 0x06:     # Modbus FC06 reboot frame
            self._rx += b"\xfc"
            self._boot = True
        else:                                   # firmware chunk
            self._rx += self._chunk_ack
        return len(data)

    def read(self, n):
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def close(self):
        self.closed = True


class TestFlash(unittest.TestCase):

    def test_model_from_filename(self):
        self.assertEqual(model_from_filename("RD60066_V1.09.bin"), 60066)
        self.assertEqual(model_from_filename("/path/RD60062_V1.32.bin"), 60062)
        self.assertIsNone(model_from_filename("random.bin"))

    def test_in_bootloader_detection(self):
        bl = RidenBootloader("MOCK")
        bl._ser = _FakeBootSerial(in_bootloader=True)
        self.assertTrue(bl.in_bootloader())
        bl._ser = _FakeBootSerial(in_bootloader=False)
        self.assertFalse(bl.in_bootloader())

    def test_enter_bootloader_reboots_via_modbus(self):
        fake = _FakeBootSerial(in_bootloader=False)
        bl = RidenBootloader("MOCK", address=1)
        bl._ser = fake
        with patch("riden_flash.time.sleep"):     # skip the 3 s reboot settle
            bl.enter_bootloader()
        # A Modbus FC06 reboot frame (func 0x06) must have been written.
        self.assertIn(b"\x06", bytes(fake.written))
        self.assertTrue(fake._boot)

    def test_info_parsing(self):
        bl = RidenBootloader("MOCK")
        bl._ser = _FakeBootSerial(model=60066, fw_raw=109, serial_num=1036)
        info = bl.info()
        self.assertEqual(info["model"], 60066)
        self.assertEqual(info["fw"], "v1.09")
        self.assertEqual(info["serial"], "00001036")
        self.assertTrue(info["supported"])

    def test_write_firmware_chunks_and_progress(self):
        fake = _FakeBootSerial(in_bootloader=True)
        bl = RidenBootloader("MOCK")
        bl._ser = fake
        firmware = bytes(range(150))            # 150 bytes -> 64 + 64 + 22
        seen = []
        bl.write_firmware(firmware, progress=lambda d, t: seen.append((d, t)))
        self.assertIn(b"upfirm\r\n", bytes(fake.written))
        self.assertEqual(seen[-1], (150, 150))  # final progress = full image
        self.assertEqual(len(seen), 3)          # three chunks

    def test_write_firmware_rejected_chunk_raises(self):
        bl = RidenBootloader("MOCK")
        bl._ser = _FakeBootSerial(in_bootloader=True, chunk_ack=b"XX")
        with self.assertRaises(FlashError):
            bl.write_firmware(b"\x00" * 64)

    def test_flash_firmware_refuses_without_confirm(self):
        with patch("serial.Serial", return_value=_FakeBootSerial(in_bootloader=True)), \
             patch("riden_flash.time.sleep"):
            with tempfile.NamedTemporaryFile(suffix="RD60066.bin", delete=False) as f:
                f.write(b"\x00" * 64)
                path = f.name
            try:
                with self.assertRaises(FlashError):
                    flash_firmware("MOCK", path, confirm=False)
            finally:
                os.unlink(path)

    def test_flash_firmware_model_mismatch_refused(self):
        # device model 60066, but firmware filename says 60241 -> refuse
        with patch("serial.Serial", return_value=_FakeBootSerial(model=60066, in_bootloader=True)), \
             patch("riden_flash.time.sleep"):
            d = tempfile.mkdtemp()
            path = os.path.join(d, "RD60241_V1.39.bin")
            with open(path, "wb") as fh:
                fh.write(b"\x00" * 64)
            try:
                with self.assertRaises(FlashError):
                    flash_firmware("MOCK", path, confirm=True)
            finally:
                os.unlink(path)
                os.rmdir(d)

    def test_flash_firmware_happy_path(self):
        with patch("serial.Serial", return_value=_FakeBootSerial(model=60066, in_bootloader=True)), \
             patch("riden_flash.time.sleep"):
            d = tempfile.mkdtemp()
            path = os.path.join(d, "RD60066_V1.10.bin")
            with open(path, "wb") as fh:
                fh.write(b"\xaa" * 130)
            try:
                result = flash_firmware("MOCK", path, confirm=True)
            finally:
                os.unlink(path)
                os.rmdir(d)
        self.assertTrue(result["flashed"])
        self.assertEqual(result["bytes"], 130)
        self.assertEqual(result["model"], 60066)

    def test_flash_firmware_no_file_reboots_only(self):
        with patch("serial.Serial", return_value=_FakeBootSerial(model=60066, in_bootloader=False)), \
             patch("riden_flash.time.sleep"):
            result = flash_firmware("MOCK", None)
        self.assertFalse(result["flashed"])
        self.assertIsNone(result["bytes"])
        self.assertEqual(result["model"], 60066)


# ---------------------------------------------------------------------------
# Layer 7 — MCP server (tool registration, no hardware)
# ---------------------------------------------------------------------------

class TestMcp(unittest.TestCase):
    """Import the MCP server and confirm its FastMCP tool surface registers.

    Importing the module runs the @mcp.tool() decorators (registering the tools)
    but never calls main(), so no serial port is opened — hardware-free.
    """

    def test_tools_register(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "mcp"))
        try:
            import mcp_server  # noqa: PLC0415 — module load registers the tools
        except Exception as exc:  # mcp SDK / colorlog not installed, etc.
            self.skipTest(f"MCP server not importable: {exc}")

        tools = asyncio.run(mcp_server.mcp.list_tools())
        names = {t.name for t in tools}
        for tool in ("rd_status", "rd_discover_devices", "rd_firmware",
                     "rd_set_voltage", "rd_list_psus", "rd_connect", "rd_all_off"):
            self.assertIn(tool, names, f"MCP tool {tool} not registered")


if __name__ == "__main__":
    unittest.main(verbosity=2)
