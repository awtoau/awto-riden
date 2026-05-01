# USB Serial Testing Guide

## Prerequisites

- RD60xx / RD6012 / RD6024 PSU with USB cable connected to system
- Linux system with `pyserial` support
- `awto-mcp-riden` installed (`pip install -e .`)

## Check Connection

```bash
# Verify USB device appears
ls -la /dev/ttyUSB*

# Example output:
# crw-rw---- 1 root dialout 188, 0 May  1 09:45 /dev/ttyUSB0

# If no device, check dmesg for connection log
dmesg | tail -20
```

## Manual Interface Selection (Required)

Do not hardcode `/dev/ttyUSB0` in your workflow. The serial interface can change.

- USB adapters may move between `/dev/ttyUSB0`, `/dev/ttyUSB1`, etc. after reconnects.
- Some devices expose `/dev/ttyACM*` instead of `/dev/ttyUSB*`.
- Bluetooth serial uses `/dev/rfcomm*` and is separate from USB.

Always detect and select the interface manually before running commands:

```bash
# USB/ACM candidates
ls -la /dev/ttyUSB* /dev/ttyACM* 2>/dev/null

# Bluetooth serial candidates
ls -la /dev/rfcomm* 2>/dev/null

# Then pass the chosen interface explicitly
python3 ttu_cli.py --port /dev/ttyUSB1 status
# or
python3 ttu_cli.py --port /dev/rfcomm0 status
```

## Run Daemon

```bash
# Start daemon listening on USB serial
cd /home/dan/git/awto-mcp-riden
python3 riden_daemon.py --port /dev/ttyUSB0 --baud 115200

# Expected output:
# 2026-05-01T09:45:00 INFO     riden.daemon: awto-mcp-riden daemon v0.1
# 2026-05-01T09:45:00 INFO     riden.daemon: free-threaded Python detected (3.14.0t)
# 2026-05-01T09:45:00 INFO     riden.daemon: connected to PSU on /dev/ttyUSB0 (baud=115200 addr=1) id=RD6006
# 2026-05-01T09:45:00 INFO     riden.daemon: listening on /tmp/awto-mcp-riden.sock
```

## Query Status (in another terminal)

```bash
cd /home/dan/git/awto-mcp-riden

# Ping daemon
python3 ttu_cli.py ping
# Output: {"ok": true}

# Get PSU status
python3 ttu_cli.py status
# Output:
# {
#   "voltage_set": 6.0,
#   "current_set": 1.0,
#   "voltage_out": 5.98,
#   "current_out": 0.05,
#   "output": true
# }

# Set voltage
python3 ttu_cli.py set-voltage 12.0

# Enable/disable output
python3 ttu_cli.py output on
python3 ttu_cli.py output off

# Power cycle (5 second off, then on)
python3 ttu_cli.py power-cycle 5

# Set protections
python3 ttu_cli.py set-ovp 15.0
python3 ttu_cli.py set-ocp 2.0

# Start logging status every 100ms to file
python3 ttu_cli.py log-start /tmp/psu-log.txt 100

# Stop logging
python3 ttu_cli.py log-stop

# View daemon health
python3 ttu_cli.py info
# Output: {
#   "pid": 12345,
#   "rss_mb": 45.2,
#   "cpu_percent": 0.1,
#   "threads": 3,
#   "open_files": 5,
#   "uptime_s": 123.45,
#   "_free_threaded": true,
#   "python_version": "3.14.0t"
# }
```

## Verbose Debugging

```bash
# Enable debug logging
python3 ttu_cli.py --verbose status

# Or daemon with debug level
python3 riden_daemon.py --port /dev/ttyUSB0 --level DEBUG
```

## Daemon Shutdown

```bash
# Graceful termination (SIGTERM)
kill $(cat /tmp/awto-mcp-riden.pid)

# Or Ctrl+C in daemon terminal
^C
```

## Expected Error Modes

### No USB device connected

```
FileNotFoundError: [Errno 2] No such file or directory: '/dev/ttyUSB0'
```

**Solution:** Connect PSU via USB cable

If the device is connected but not on USB, use the correct interface (`/dev/rfcomm*` for Bluetooth serial).

### Daemon already running on socket

```
OSError: [Errno 98] Address already in use: '/tmp/awto-mcp-riden.sock'
```

**Solution:** Kill existing daemon or use `--socket /tmp/awto-riden-alt.sock`

### Permission denied on /dev/ttyUSB0

```
PermissionError: [Errno 13] Permission denied: '/dev/ttyUSB0'
```

**Solution:** Add user to dialout group:
```bash
sudo usermod -a -G dialout $USER
# Then logout/login
```

### Timeout / PSU not responding

If daemon connects but queries hang:
- Check baud rate matches PSU setting (usually 115200)
- Check Modbus address (usually 1)
- Try `--level DEBUG` for detailed I/O logs
- Verify PSU Modbus RTU is enabled (check PSU manual)

## Integration with Copilot

Once daemon is running, register MCP server in VS Code:

```json
{
  "mcpServers": {
    "awto-riden": {
      "command": "python3",
      "args": ["/home/dan/git/awto-mcp-riden/mcp_server.py"]
    }
  }
}
```

Then ask Copilot: *"What's the PSU output voltage?"* or *"Set the PSU to 12V 2A"*

## Test Suite (No Hardware Required)

All 31 tests pass without physical PSU:

```bash
python3 test_harness.py
# Output: Ran 31 tests in 1.4s — OK
```

## Next Steps

- ✅ USB serial fully tested and documented
- ⏳ v0.2: Bluetooth Low Energy support (see BLE_ROADMAP.md)
- 🔮 v0.3+: Framework extraction, multi-device support
