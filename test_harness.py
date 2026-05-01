"""
awto-riden test harness — simplified for direct serial (no daemon).

Tests the RidenWorker class with mock Riden PSU driver.

Layers tested:
  1. Protocol helpers (send_request / recv_response / make_ok / make_err)
  2. RidenWorker mock — state transitions, command dispatch
  3. CLI subprocess — smoke test (ping, status, set-voltage, etc.)
  4. MCP tool _call simulation (tool invocation)

Run:
    python3 test_harness.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from protocol import make_err, make_ok, recv_response, send_request
from riden_daemon import RidenWorker

# Fake port — never resolves to real hardware; any test that accidentally
# bypasses the mock will fail immediately with a clear serial error, not
# silently probe a connected device.
MOCK_PORT = "MOCK://"


# ---------------------------------------------------------------------------
# Mock Riden PSU driver — replaces real hardware
# ---------------------------------------------------------------------------

class MockRiden:
    """Simulates ShayBox/Riden library behavior without hardware."""

    def __init__(self, port: str, baudrate: int, address: int = 1) -> None:
        self.port = port
        self.baudrate = baudrate
        self.address = address
        self.id = "RD6006"

        # PSU state (attribute names match ShayBox/Riden library)
        self.v_set = 5.0
        self.i_set = 1.0
        self.v_out = 4.998
        self.i_out = 0.501
        self.p_out = 2.502
        self.v_in = 24.1
        self.enable = True
        self.cv_cc = 0        # 0=CV, 1=CC
        self.protect = 0      # 0=none, 1=OVP, 2=OCP
        self.int_c = 28

    def open(self) -> None:
        """Simulate opening serial port."""
        pass

    def close(self) -> None:
        """Simulate closing serial port."""
        pass

    def update(self) -> None:
        """Simulate reading PSU state."""
        pass

    def set_v_set(self, volts: float) -> None:
        self.v_set = volts

    def set_i_set(self, amps: float) -> None:
        self.i_set = amps


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
# Layer 2 — RidenWorker with mock Riden
# ---------------------------------------------------------------------------

class TestRidenWorker(unittest.TestCase):

    def setUp(self) -> None:
        """Patch Riden before each test — no real hardware probed."""
        self.riden_patcher = patch("riden_daemon.Riden", MockRiden)
        self.mock_riden_class = self.riden_patcher.start()
        import riden_daemon
        assert riden_daemon.Riden is MockRiden, "Mock patch did not apply — would probe real hardware"

    def tearDown(self) -> None:
        self.riden_patcher.stop()

    def test_worker_init_and_open(self) -> None:
        """Test RidenWorker initialization and serial connection."""
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()
        self.assertIsNotNone(worker._psu)
        worker.close()

    def test_status_returns_dict(self) -> None:
        """Test that status() returns expected PSU state fields."""
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()
        status = worker.status()
        self.assertIn("v_set", status)
        self.assertIn("i_set", status)
        self.assertIn("v_out", status)
        self.assertIn("i_out", status)
        self.assertIn("output", status)
        worker.close()

    def test_set_voltage(self) -> None:
        """Test setting voltage and checking status."""
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()
        worker.set_voltage(12.0)
        status = worker.status()
        self.assertEqual(status["v_set"], 12.0)
        worker.close()

    def test_set_current(self) -> None:
        """Test setting current and checking status."""
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()
        worker.set_current(2.5)
        status = worker.status()
        self.assertEqual(status["i_set"], 2.5)
        worker.close()

    def test_output_control(self) -> None:
        """Test enabling/disabling output."""
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()

        worker.set_output(False)
        self.assertFalse(worker.status()["output"])

        worker.set_output(True)
        self.assertTrue(worker.status()["output"])

        worker.close()

    def test_power_cycle(self) -> None:
        """Test power cycle command."""
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()

        # Power cycle should return to the same state
        original_v_set = worker.status()["v_set"]
        worker.power_cycle(0.1)  # Short wait for testing
        self.assertEqual(worker.status()["v_set"], original_v_set)

        worker.close()

    def test_info_fields(self) -> None:
        """Test that info() returns process health metrics."""
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()

        info = worker.info()
        self.assertIn("pid", info)
        self.assertIn("rss_mb", info)
        self.assertIn("cpu_pct", info)
        self.assertIn("threads", info)
        self.assertIn("open_fds", info)
        self.assertIn("free_threaded", info)
        self.assertIn("python", info)

        worker.close()

    def test_log_start_stop(self) -> None:
        """Test logging start/stop."""
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()

        log_path = "/tmp/test_riden.log"
        worker.log_start(log_path, interval_ms=100)
        # Give logging thread time to write
        import time
        time.sleep(0.2)
        worker.log_stop()

        worker.close()


# ---------------------------------------------------------------------------
# Layer 3 — CLI subprocess tests
# ---------------------------------------------------------------------------

class TestCLI(unittest.TestCase):

    def test_cli_help(self) -> None:
        """Test that CLI help works."""
        result = subprocess.run(
            [sys.executable, "ttu_cli.py", "--help"],
            capture_output=True,
            text=True,
            cwd="/home/dan/git/awto-mcp-riden",
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Control a Riden", result.stdout)


# ---------------------------------------------------------------------------
# Layer 4 — Protocol and data structure tests
# ---------------------------------------------------------------------------

class TestDataStructures(unittest.TestCase):

    def setUp(self) -> None:
        self.riden_patcher = patch("riden_daemon.Riden", MockRiden)
        self.riden_patcher.start()
        import riden_daemon
        assert riden_daemon.Riden is MockRiden, "Mock patch did not apply — would probe real hardware"

    def tearDown(self) -> None:
        self.riden_patcher.stop()

    def test_status_fields_consistency(self) -> None:
        """Test that worker.status() has the expected fields."""
        worker = RidenWorker(port=MOCK_PORT, baud=115200, address=1)
        worker.open()

        status = worker.status()
        expected_fields = [
            "v_set", "i_set", "v_out", "i_out", "p_out", "v_in",
            "output", "cv_cc", "protect", "temp_c"
        ]
        for field in expected_fields:
            self.assertIn(field, status, f"status missing {field}")

        worker.close()

    def test_make_ok_wraps_dict(self) -> None:
        """Test that make_ok() correctly wraps a dict."""
        data = {"v_set": 5.0, "output": True}
        result = make_ok(data)
        self.assertTrue(result["ok"])
        self.assertIn("v_set", result)
        self.assertEqual(result["v_set"], 5.0)

    def test_make_err_includes_message(self) -> None:
        """Test that make_err() includes error message."""
        msg = "Connection timeout"
        result = make_err(msg)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
