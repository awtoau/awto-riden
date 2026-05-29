# BLE Support Roadmap

**Status:** USB-serial only. BLE support is planned. Tracking issue:
[#6 — Implement native BLE transport](https://github.com/awtoau/awto-riden/issues/6).
See `BT_STATUS.md` for the current verified hardware/pairing state.

## Motivation

Two Bluetooth RK6006 power supplies are available on the system:
- `88:BB:52:09:E5:43` (primary)
- `89:BB:52:09:E5:43` (secondary)

These are paired but not yet integrated with the daemon.

## Current State

- ✅ USB/serial transport working via our own `SerialTransport`
  (`riden_transport.py`) — no upstream library dependency. The ShayBox/Riden
  project is now only the source of the register layout, not a runtime import.
- ✅ Modbus RTU protocol implemented in-house (FC03/FC06/FC16, slave addr 1, 115200 baud)
- ✅ `bleak>=0.22` already in `pyproject.toml` (BLE client library)
- ⏳ Native BLE transport: `BleTransport` exists as a stub in `riden_transport.py`
  that raises `NotImplementedError` — **not yet implemented**

## Discovery Attempt (v0.1.0-alpha)

**File:** `ble_discover.py` (created but incomplete)

```python
# Attempted to connect and enumerate RK6006 BLE GATT profile
async with BleakClient("88:BB:52:09:E5:43") as client:
    services = await client.get_services()
    # enumerate UUIDs...
```

**Result:** Connection initiated but GATT service enumeration timed out after 10s.

**Likely causes:**
1. RK6006 BLE GATT database is large (many services/characteristics)
2. BlueZ stack on this system is slow or has high latency
3. Device may be asleep or requires additional pairing handshake
4. `/dev/rfcomm0` binding not yet established

## Path to v0.2 (BLE Support)

### Phase 1: Profile Discovery
**Goal:** Identify RK6006 BLE GATT services and characteristic UUIDs.

**Steps:**
1. Update `ble_discover.py` to add timeout **per service** (not per scan)
   - Try 5s timeout for discovery, 2s per service enumeration
   - Log which service/characteristic causes delays
2. Cross-reference with RK6006 manufacturer docs or reverse-engineer from firmware
3. Identify:
   - Modbus TX characteristic (client writes to PSU)
   - Modbus RX characteristic (client reads from PSU, or subscribes to notify)
4. Document UUIDs in code comment

**References:**
- ShayBox/Riden library (serial Modbus RTU): https://github.com/ShayBox/Riden
- BLE spec: https://www.bluetooth.com/
- `bleak` docs: https://github.com/hbldh/bleak

### Phase 2: Transport Layer
**Goal:** Implement native Modbus RTU over BLE behind the existing transport interface.

The architecture has changed since this roadmap was first written: there is now
a `RidenTransport` ABC in `riden_transport.py` with `SerialTransport`,
`TcpTransport`, and a `BleTransport` **stub**. `RidenDevice`/`RidenWorker`
(`riden_daemon.py`) talk only to that ABC — they have no knowledge of serial vs
BLE. So BLE support means **filling in `BleTransport`**, not editing the daemon.

`BleTransport` must implement the same surface as `SerialTransport`:
`open()`, `close()`, `address`, and `read(register, count)` /
`write(register, value)` / `write_multiple(register, values)` — building the
same Modbus RTU FC03/FC06/FC16 frames (the framing helpers can be shared with
`SerialTransport`).

1. **Connect + discover characteristics** in `open()`:
   ```python
   def open(self) -> None:
       # bleak is async; bridge to the sync transport API via a private
       # event loop running in a background thread (see Risks below).
       self._client = BleakClient(self._mac)
       self._loop.run(self._client.connect())
       # Discover the Modbus TX (write) and RX (notify) characteristic UUIDs.
       self._loop.run(self._client.start_notify(self._rx_char, self._on_notify))
   ```

2. **Frame a request, await the notify reply** in `read()`/`write()`:
   ```python
   def read(self, register, count=1):
       request = self._build_fc03_request(register, count)   # shared with SerialTransport
       self._loop.run(self._client.write_gatt_char(self._tx_char, request))
       response = self._rx_queue.get(timeout=self._timeout)   # filled by _on_notify
       return self._parse_fc03_response(response, count)
   ```

3. **RX notify callback** pushes reassembled frames onto a queue:
   ```python
   def _on_notify(self, _sender, data: bytearray) -> None:
       self._rx_queue.put(bytes(data))   # may need reassembly across MTU chunks
   ```

### Phase 3: Integration & Testing
**Goal:** Full integration test with real RK6006 device.

**Steps:**
1. Query status directly via the CLI, passing the paired device MAC as `--port`
   (`BleTransport` is selected when the port looks like a MAC, not a `/dev/...`
   path). Replace `<MAC>` with the actual paired device — none is paired today,
   see `BT_STATUS.md`:
   ```bash
   python3 ttu_cli.py --port <MAC> status
   ```

2. Verify response matches the USB serial baseline (`--port /dev/ttyUSB0`)

4. Stress test: Rapid command sequences, concurrent clients, network latency

5. Add unit tests to `test_harness.py` for BLE path (mock `BleakClient`)

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| RK6006 BLE GATT profile unknown | Research manufacturer docs, reverse-engineer from firmware or Wireshark capture |
| BlueZ latency or pairing issues | Add detailed logging, test with `bluetoothctl` first |
| Async/sync bridging complexity | Use `asyncio.run()` wrapper in daemon (single-threaded event loop per BLE client) or run in thread pool |
| Bluetooth range/stability | Test at multiple distances, add automatic reconnect with exponential backoff |

## Deferred (v0.3+)

- BLE bonding & persistent pairing
- Multiple BLE devices in parallel (requires multiple daemons or multiplex via single daemon)
- BLE security (encrypted pairing, PIN verification)
- Over-the-air firmware updates via BLE

## Quick Start (When Ready)

```bash
# Install dependencies
pip install bleak>=0.22

# Run discovery
python3 ble_discover.py

# Update RK6006 GATT UUIDs in BleTransport (riden_transport.py)

# Test directly via the CLI, passing the paired MAC as --port
python3 ttu_cli.py --port <MAC> status
```

## References

- RK6006 specs: https://www.riden.net/
- ShayBox/Riden (Modbus RTU): https://github.com/ShayBox/Riden
- bleak library: https://github.com/hbldh/bleak
- Modbus RTU spec: https://en.wikipedia.org/wiki/Modbus
