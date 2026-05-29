# Architecture: Direct Serial (No Daemon)

**Status:** ✅ Production-ready (v0.1)

## Why No Daemon?

The original daemon added unnecessary complexity:
- **Socket IPC overhead** — extra layer of indirection
- **Startup hangs** — daemon blocks on first PSU read during initialization
- **PID files & signal handlers** — more code to maintain
- **Thread-per-client model** — overkill for occasional CLI commands or MCP tool calls

**Solution:** Clients (CLI, MCP server) open the serial port directly. Modbus RTU protocol handles serialization at the lower level.

## Architecture

```
┌─────────────────┐
│  Copilot/Agent  │
└────────┬────────┘
         │
    ┌────▼────┐
    │MCP Tool │ (mcp_server.py)
    └────┬────┘
         │
    ┌────▼──────────────┐
    │ RidenWorker Class │ (riden_daemon.py)
    │ • open/close      │
    │ • status          │
    │ • set_voltage     │
    │ • set_current     │
    │ • power_cycle     │
    │ • logging         │
    └────┬──────────────┘
         │
    ┌────▼────────────┐
    │ ShayBox/Riden   │
    │ (Modbus RTU)    │
    └────┬────────────┘
         │
    ┌────▼─────────┐
    │ pyserial     │
    │ /dev/ttyUSB0 │
    └──────────────┘
```

## File Roles

### `riden_daemon.py` — Reusable RidenWorker class
- ✅ Thread-safe wrapper around ShayBox/Riden
- ✅ Logging (colorlog + syslog)
- ✅ Process monitoring (psutil)
- ❌ No daemon, no socket server, no IPC
- Used by: CLI, MCP server, tests

### `mcp/mcp_server.py` — FastMCP stdio server (PARKED — see issue #7)
- Parked under `mcp/`; development is CLI-first (`ttu_cli.py`). Kept for when
  AI-agent / Copilot access is wanted again.
- Opens serial port directly
- Registers tools: `rd_status()`, `rd_set_voltage()`, `rd_set_current()`, etc.
- All tool calls share the serial connection
- Usage: `python3 mcp/mcp_server.py --port /dev/ttyUSB0`

### `ttu_cli.py` — One-shot CLI
- Opens serial port, runs one command, closes
- Subcommands: `status`, `set-voltage`, `set-current`, `output`, `power-cycle`, `info`
- Usage: `python3 ttu_cli.py --port /dev/ttyUSB0 status`

### `protocol.py` — Shared helpers (kept for history)
- JSON encoding/decoding for potential future daemon rebuild
- Currently unused by CLI/MCP (they use RidenWorker directly)

### `test_harness.py` — Unit tests
- Mock `Riden` class for testing without hardware
- Tests RidenWorker state machine and dispatch
- CLI subprocess smoke tests
- Run: `python3 test_harness.py`

## Usage

### CLI Command

```bash
python3 ttu_cli.py --port /dev/ttyUSB0 status
python3 ttu_cli.py --port /dev/ttyUSB0 set-voltage 12.0
python3 ttu_cli.py --port /dev/ttyUSB0 set-current 2.0
python3 ttu_cli.py --port /dev/ttyUSB0 output on
python3 ttu_cli.py --port /dev/ttyUSB0 power-cycle --seconds 3
```

### MCP Server (for Copilot)

```bash
# Terminal 1: Start MCP server
python3 mcp_server.py --port /dev/ttyUSB0

# Terminal 2 (or VS Code with .vscode/mcp.json): Copilot asks
# "Set the PSU to 12V 2A and turn it on"
# → Copilot calls rd_set_voltage(12), rd_set_current(2), rd_output(True)
```

### Register in VS Code (.vscode/mcp.json)

```json
{
  "mcpServers": {
    "awto-riden": {
      "command": "python3",
      "args": ["/home/dan/git/awto-mcp-riden/mcp_server.py", "--port", "/dev/ttyUSB0"]
    }
  }
}
```

## Entry Points

```toml
[project.scripts]
awto-riden = "ttu_cli:main"
awto-riden-mcp = "mcp_server:main"
```

## Advantages Over Daemon

| Aspect | Daemon | Direct Serial |
|--------|--------|---------------|
| **Startup time** | Slow (full PSU init) | Fast (lazy init) |
| **Code complexity** | High (IPC, threads, signals) | Low (direct API) |
| **Error handling** | Daemon-level errors hidden | Visible in caller |
| **Concurrent access** | Single daemon + socket | Multiple processes (safe via Modbus RTU) |
| **Debugging** | Remote debugging hard | Direct stack traces |
| **Scalability** | Single daemon bottleneck | Each tool gets own connection |

## Modbus Serialization

No explicit locking needed—Modbus RTU at 115200 baud means:
- Write command (~10ms)
- Read response (~10ms)
- Total: ~20ms per operation

Even if two CLI invocations run simultaneously, Modbus protocol handles packet sequencing at the serial level. Each gets its own request/response cycle.

## Testing Without Hardware

```bash
# Mock Riden driver patches out real PSU
python3 test_harness.py -v

# All 8+ tests pass without /dev/ttyUSB0 connected
```

## Next Steps (v0.2+)

- **BLE Support** — Add native Bluetooth/BLE transport (see BLE_ROADMAP.md)
- **Framework Extraction** — Move RidenWorker → `awto-mcp-python-framework`
- **Multi-PSU** — Run multiple CLI invocations in parallel (natural via Modbus)

## Status

✅ **Production Ready**
- USB serial working (tested with CH341 adapter on /dev/ttyUSB0)
- CLI working (tested with `status` command)
- MCP server working (tested startup)
- Tests pass without hardware
- No external daemon required
