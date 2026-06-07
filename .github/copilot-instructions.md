# Copilot Instructions — awto-riden

> **Now CLI-first (see issue #7).** The primary interface is the direct CLI
> `awto_riden.py` (`python3 awto_riden.py --port /dev/ttyUSB0 status`), which talks to
> `RidenWorker` over serial with no daemon. The **MCP server is parked** under
> `mcp/` and is off the active path; the section below applies only if you
> deliberately re-enable it.

This repo controls a Riden RD60xx / RK60xx bench PSU over USB-serial Modbus RTU.
It also ships a parked MCP server (`mcp/mcp_server.py`) exposing the PSU as tools
to VS Code Copilot / any MCP client.

---

## MCP server — restart procedure (PARKED — only if re-enabled)

The MCP server is a stdio process managed by VS Code. It **cannot restart itself** —
VS Code owns the process lifecycle. To restart it:

1. Run `bash mcp/mcp_restart.sh` — this kills any stale `mcp_server.py` process.
2. In VS Code: **Ctrl+Shift+P** → **MCP: Restart Server** → select **awto-riden**.

The server will auto-detect the first available serial device (`/dev/ttyUSB*` →
`/dev/ttyACM*` → `/dev/rfcomm*`). The `--port` argument in `.vscode/mcp.json` has been
removed so auto-detect is always used.

**When a restart is needed:**
- After plugging/unplugging the USB cable (device path may change)
- After `mcp_server.py` is edited
- After `.vscode/mcp.json` is changed
- If `rd_status` returns a connection error

---

## Serial device

- Current device: `/dev/ttyUSB1` (auto-detected; may differ on your machine)
- Baud: 115200, Modbus address: 1
- RTT: ~93 ms on ttyUSB1 (latency timer reduced). Recommended poll interval: 120 ms.
- To check: `ls /dev/ttyUSB* /dev/ttyACM* /dev/rfcomm* 2>/dev/null`
- To quick-test the transport: `python3 scripts/transport_test.py`

---

## MCP tools — quick reference

| Tool | What it does |
|---|---|
| `rd_list_psus` | List all configured PSUs and their connection state |
| `rd_connect(psu)` | Open serial connection to a PSU |
| `rd_disconnect(psu)` | Close serial connection |
| `rd_status(psu)` | Read full status: V/I/P out, setpoints, mode, temp |
| `rd_capabilities(psu)` | Model limits (max V, max I, multipliers) |
| `rd_firmware(psu)` | Device ID, serial number, firmware version |
| `rd_set_voltage(volts, psu)` | Set output voltage |
| `rd_set_current(amps, psu)` | Set current limit |
| `rd_output(on, psu)` | Enable/disable output (`on=True` / `on=False`) |
| `rd_set_ovp(volts, psu)` | Set over-voltage protection threshold |
| `rd_set_ocp(amps, psu)` | Set over-current protection threshold |
| `rd_power_cycle(seconds, psu)` | Turn output off, wait, turn back on |
| `rd_all_off()` | Emergency: disable output on ALL PSUs |
| `rd_log_status(interval_ms, duration_s, path, psu)` | Poll and log V/I/P to JSONL file |
| `rd_log_current(samples, interval_ms, psu)` | Fast current capture, returns inline |
| `rd_log_stop(psu)` | Stop an in-progress log |
| `rd_log_retrieve(path, max_rows)` | Read rows back from a JSONL log |
| `rd_sine_wave(v_center, v_amplitude, freq_hz, duration_s, step_s, psu)` | Run a sine waveform (alias for `rd_waveform shape=sine`) |
| `rd_waveform(shape, v_center, v_amplitude, freq_hz, duration_s, step_s, psu)` | Run a shaped waveform: `sine`, `triangle`, `sawtooth`, `square` |
| `rd_inrush_capture(v_set, i_limit, samples, interval_ms, psu)` | Capture turn-on inrush current |
| `rd_vsweep(v_start, v_end, v_step, dwell_s, psu)` | Voltage sweep with per-step measurement |
| `profile_serial(count, sleep_ms, psu)` | Measure RTT and recommend poll interval |
| `rd_register_scan(start, end, batch, skip_zero, psu)` | Scan a register range; annotates known names, highlights undocumented non-zero registers |

**Safety rule:** always call `rd_status` first to confirm the PSU state before any write.
Disable output (`rd_output(on=False)`) before large voltage changes.

---

## Modbus register map (riden_register.py)

All offsets are holding register addresses (FC03/FC06/FC16 at Modbus address 1).

| Register | Address | Notes |
|---|---|---|
| `ID` | 0 | Device ID (e.g. 60066 = RK6006) |
| `SN_H` / `SN_L` | 1–2 | Serial number high/low words |
| `FW` | 3 | Firmware version |
| `INT_C` | 5 | Internal temperature °C (sign in reg 4) |
| `INT_F` | 7 | Internal temperature °F (sign in reg 6) |
| `V_SET` | 8 | Voltage setpoint (raw; divide by v_multi) |
| `I_SET` | 9 | Current setpoint (raw; divide by i_multi) |
| `V_OUT` | 10 | Measured output voltage |
| `I_OUT` | 11 | Measured output current |
| `AH` | 12 | Amp-hours (Ah) |
| `P_OUT` | 13 | Output power (raw; divide by p_multi) |
| `V_IN` | 14 | Input voltage |
| `KEYPAD` | 15 | Keypad lock state |
| `OVP_OCP` | 16 | Protection state: 0=none, 1=OVP, 2=OCP |
| `CV_CC` | 17 | Mode: 0=CV, 1=CC |
| `OUTPUT` | 18 | Output state: 0=off, 1=on |
| `PRESET` | 19 | Active preset slot (0–9) |
| `I_RANGE` | 20 | Current range (RD6012P only) |
| `BAT_MODE` | 32 | Battery charge mode |
| `V_BAT` | 33 | Battery voltage |
| `EXT_C` | 35 | External temperature °C (sign in reg 34) |
| `AH_H` / `AH_L` | 38–39 | Amp-hours high/low (32-bit) |
| `WH_H` / `WH_L` | 40–41 | Watt-hours high/low (32-bit) |
| `YEAR/MONTH/DAY` | 48–50 | RTC date |
| `HOUR/MINUTE/SECOND` | 51–53 | RTC time |
| `V_OUT_ZERO/SCALE` | 55–56 | Calibration — do not change |
| `I_OUT_ZERO/SCALE` | 59–60 | Calibration — do not change |
| `OPT_BOOT_POW` | 68 | Boot with output on (1) or off (0) |
| `OPT_BUZZ` | 69 | Buzzer enable |
| `OPT_LIGHT` | 72 | Display brightness |
| `M0_V … M9_OCP` | 80–119 | Preset slots 0–9 (V, I, OVP, OCP each) |
| `SYSTEM` | 256 | System control (write 5633 = enter bootloader) |

**Multipliers by model:**

| Model | v_multi | i_multi | p_multi |
|---|---|---|---|
| RK6006 (id=60066) | 100 | 1000 | 100 |
| RD6006 (id=60060–64) | 100 | 1000 | 100 |
| RD6006P (id=60065) | 1000 | 10000 | 1000 |
| RD6012 (id=60120–124) | 100 | 100 | 100 |
| RD6018 (id=60180–189) | 100 | 100 | 100 |
| RD6024 (id≥60241) | 100 | 100 | 100 |

Example: raw `V_OUT = 1200` with `v_multi = 100` → `12.00 V`.

---

## Key files

| File | Purpose |
|---|---|
| `awto_riden.py` | **Primary interface** — direct-serial CLI (status, set, benchmark, discovery) |
| `riden_daemon.py` | `RidenWorker` — high-level PSU API (waveform, status, logging, …) |
| `riden_transport.py` | `SerialTransport` — raw Modbus RTU over pyserial (FC03/FC06/FC16) |
| `riden_register.py` | Modbus register address constants |
| `protocol.py` | `make_ok`/`make_err`, error codes, wire-format docs |
| `mcp/mcp_server.py` | MCP server — FastMCP tools, PSU registry (**parked**, see #7) |
| `mcp/mcp.json` | VS Code MCP launch config (**parked** — copy to `.vscode/` to enable) |
| `mcp/mcp_restart.sh` | Kill stale MCP server process before VS Code restart (**parked**) |
| `scripts/transport_test.py` | Quick serial sanity check — reads status, no PSU changes |

---

## Running scripts

```bash
# Activate venv first
source .venv/bin/activate

# Quick transport sanity check
python3 scripts/transport_test.py

# Kill stale MCP server (then restart from VS Code MCP panel)
bash mcp/mcp_restart.sh

# Profile serial timing
python3 scripts/transport_test.py --port /dev/ttyUSB1
```
