# awto-mcp-riden

Provide MCP framework for controlling RuiDeng / Riden power supplies, specifically the RD60xx series (RD6006 / RD6012 / RD6018 / RD6024).

> Source notes: ChatGPT design discussion — kept verbatim below as the v0 spec.

---

## Question

> Do I use stock firmware or enhanced by ?? soft. Can I use BT serial?

I'll treat this as two parts: what transport/protocol is practical for RD60xx supplies, and what an MCP server should expose so Copilot/agents can control it cleanly.

## Answer (summary)

Yes. For MCP, use **stock firmware first** and talk to the RD60xx over **Modbus RTU serial**. The RD6006/RD60xx family is already controllable over serial Modbus, and Python libraries exist for USB serial control.

## Recommended stack

```
Copilot / Claude / agent
        |
      MCP server
        |
 Python RD60xx driver
        |
 USB serial / BT serial / WiFi bridge
        |
 Riden RD6006 / RD6012 / RD6018 / RD6024
```

## MCP tools to expose

```
rd_status()
rd_set_voltage(volts)
rd_set_current(amps)
rd_output(on: bool)
rd_set_ovp(volts)
rd_set_ocp(amps)
rd_power_cycle(seconds)
rd_log_status(interval_ms)
```

## Transport choice

```
Best:       USB serial
Good:       WiFi module with bridge/MQTT
Possible:   Bluetooth serial
Avoid:      flashing PSU firmware unless you need features
```

- **USB serial** is the cleanest. The official manual describes USB PC control via serial port, baud rate and slave address; community Python code uses Modbus over serial.
- **Bluetooth serial** should work if the BT module exposes a normal serial port (e.g. `/dev/rfcomm0` on Linux). Treat it exactly like USB serial, but expect more latency and occasional disconnects. Good enough for set/read/power-cycle, not ideal for tight logging.
- **WiFi stock firmware** is awkward but usable. The Riden WiFi board behaves like a UART bridge, not normal Modbus TCP; one project bridges it to MQTT without modifying PSU or WiFi firmware.

## Enhanced firmware options

```
UniSoft firmware:
  Alternative firmware for RD6006/RD6012/RD6018 style units.
  Useful, but check exact model support carefully.

Riden-dongle firmware:
  Flashes the WiFi module, not the PSU.
  Adds Modbus TCP, SCPI and web UI.
  More lab-automation friendly.
```

The **Riden-dongle** firmware looks more useful for MCP because SCPI/Modbus TCP is cleaner than the stock WiFi behaviour. It replaces the WiFi module firmware and is not compatible with the original mobile app. **UniSoft** is an alternative PSU firmware, but only use it after proving control with stock firmware.

## Minimal MCP direction

```python
# MCP server wraps a persistent serial connection.
# Do NOT spawn a new Python process per command.

from mcp.server.fastmcp import FastMCP
from rd6006 import RD6006

mcp = FastMCP("riden-rd60xx")

psu = RD6006("/dev/ttyUSB0")   # or "/dev/rfcomm0" for BT serial

@mcp.tool()
def rd_status():
    return {
        "voltage_set": psu.voltage,
        "current_set": psu.current,
        "voltage_out": psu.output_voltage,
        "current_out": psu.output_current,
        "output": psu.enable,
    }

@mcp.tool()
def rd_set_voltage(volts: float):
    psu.voltage = volts
    return rd_status()

@mcp.tool()
def rd_set_current(amps: float):
    psu.current = amps
    return rd_status()

@mcp.tool()
def rd_output(on: bool):
    psu.enable = on
    return rd_status()
```

## Plan for this repo

1. Start with **stock firmware + USB serial**. ✅ **Done (v0.1)**
2. Then try **BT serial** if you want wireless bench use. ⏳ **Planned (v0.2)**
3. For serious automated testing, use MCP over USB serial — or flash only the **WiFi dongle** with `riden-dongle` for SCPI / Modbus TCP. 🔮 **Future**
4. Do **not** start with UniSoft firmware unless stock firmware blocks something we actually need.

---

## Implementation Status

### v0.1 (Current)

**Transport:** USB serial only (via `pyserial` + ShayBox/Riden library)

**Architecture:**
- `riden_daemon.py`: Houses `RidenWorker` — thread-safe PSU wrapper (no daemon, no socket)
- `ttu_cli.py`: One-shot CLI — opens serial, runs one command, closes
- `mcp_server.py`: 9 FastMCP tools for Copilot/agents — opens serial at startup
- `test_harness.py`: 14 unit tests, all passing (MockRiden, no hardware required)

**Quick start:**
```bash
# Install
pip install -e .

# Query status (requires RD60xx connected to /dev/ttyUSB0)
python3 ttu_cli.py --port /dev/ttyUSB0 status

# Or use MCP with Copilot:
# - Register .vscode/mcp.json in VS Code settings
# - Ask Copilot: "What's the current PSU output?"
```

**Manual interface selection (important):**
- Do not assume `/dev/ttyUSB0` is always correct.
- On reconnect, Linux may re-enumerate the adapter as `/dev/ttyUSB1`, `/dev/ttyUSB2`, etc.
- Bluetooth serial devices usually appear as `/dev/rfcomm0` (or another `rfcomm` index), not `ttyUSB`.
- Always list available ports first, then pass the exact one with `--port`.

```bash
# Check USB serial candidates
ls -la /dev/ttyUSB* /dev/ttyACM* 2>/dev/null

# Check Bluetooth serial candidates
ls -la /dev/rfcomm* 2>/dev/null

# Use the selected interface explicitly
python3 ttu_cli.py --port /dev/ttyUSB1 status
# or
python3 ttu_cli.py --port /dev/rfcomm0 status
```

**Full USB testing guide:** See [USB_TESTING.md](USB_TESTING.md) for all commands and error modes

**Test suite (no hardware required):**
```bash
python3 test_harness.py
# Output: Ran 14 tests in 0.4s — OK
```

### v0.2 (Planned: BLE Support)

**Goal:** Native Bluetooth Low Energy support for RK6006 devices

**Status:** Discovery in progress (see [BLE_ROADMAP.md](BLE_ROADMAP.md))

**Available hardware:** Two RK6006 devices paired to system
- `88:BB:52:09:E5:43` (primary)
- `89:BB:52:09:E5:43` (secondary)

**Blocking:** Need to identify RK6006 BLE GATT service/characteristic UUIDs for Modbus communication

**Path forward:** See [BLE_ROADMAP.md](BLE_ROADMAP.md) for discovery steps, phase breakdown, and integration plan.

### v0.3+ (Future)

- Multi-device support (daemon per device or multiplex via socket)
- BLE bonding & security
- Framework extraction → `awto-mcp-python-framework` (reusable across all instruments)
- Companion repos: `awto-mcp-wit` (WitMotion BLE IMU), `awto-mcp-stlink` (ST-Link flash)

