# Bluetooth Status

> **Verified 2026-05-29.** Tracking issue: [#6 — Implement native BLE transport](https://github.com/awtoau/awto-riden/issues/6)

## TL;DR

**There is no working Bluetooth path yet.** Use USB serial. Native BLE
(`BleTransport`) is a stub that raises `NotImplementedError`; no Riden device
is currently paired with BlueZ.

## BT Adapters (this host)

- `90:DE:80:34:14:BA` — default controller, powered on
- `F8:3D:C6:37:F1:FD` — secondary controller, powered on
- BlueZ 5.86

## Paired Riden devices

**None currently.** Earlier notes listed two RK6006 MACs
(`88:BB:52:09:E5:43`, `89:BB:52:09:E5:43`) as paired, but neither is known to
BlueZ on either controller now — they must be re-discovered and paired before
any BT connection can be attempted.

## Connection methods

### USB Serial (working — use this)

When the Riden is plugged in via USB it enumerates as `/dev/ttyUSB0`. The CLI
talks directly over serial (no daemon required) via our own `SerialTransport`
+ `RidenDevice` (`riden_transport.py` / `riden_daemon.py`):

```bash
python3 awto_riden.py --port /dev/ttyUSB0 ping
python3 awto_riden.py --port /dev/ttyUSB0 status
```

Measured round-trip latency is ~131 ms median — this is the RD6006 firmware
register-scan period (~7 Hz), not a transport limitation.

### Bluetooth (not yet working)

Two candidate paths, both tracked in [#6](https://github.com/awtoau/awto-riden/issues/6):

1. **rfcomm → SerialTransport** — pair the device, bind `/dev/rfcomm0`, then
   reuse `SerialTransport` (it already accepts rfcomm paths). No new transport
   code needed; closest to working today.
2. **Native BLE** — implement the `BleTransport` stub in `riden_transport.py`
   using `bleak` (already a dependency). See `BLE_ROADMAP.md`.
