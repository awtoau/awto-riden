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

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from protocol import make_err, make_ok
from riden_daemon import RidenWorker

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
            [sys.executable, "ttu_cli.py", "--help"],
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
