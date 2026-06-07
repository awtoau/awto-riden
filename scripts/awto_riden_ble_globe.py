"""
BLE globe turn-on capture — 80 ms polling via BLE UART (FFE0/FFE1).

Sets V=14V / I_limit=6A, enables output, samples V_OUT+I_OUT+CV/CC at ~80ms
for a configurable duration, then disables output.  Records actual RTT per
sample so the latency spikes are visible in post-processing.

Output JSONL (one JSON object per line):
  {"ts": <unix_s>, "v_out": <V>, "i_out": <A>, "cv_cc": "CV"|"CC",
   "protect": "none"|"OVP"|"OCP", "output": true,
   "rtt_ms": <measured BLE RTT for this sample>}

Usage:
    python3 scripts/awto_riden_ble_globe.py [--mac AA:BB:CC:DD:EE:FF]
        [--v-set 14.0] [--i-limit 6.0] [--duration 6] [--interval-ms 80]
        [--out docs/data/globe_turnon_14v_ble80ms.jsonl]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import time
from pathlib import Path

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    raise SystemExit("bleak is required: pip install bleak")

DEFAULT_MAC = "88:BB:52:09:E5:43"
FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"

REG_V_SET   = 8
REG_I_SET   = 9
REG_V_OUT   = 10   # also start of 9-reg read block
REG_OUTPUT  = 18


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def mb_read(start: int, count: int) -> bytes:
    pl = bytes([0x01, 0x03, start >> 8, start & 0xFF, count >> 8, count & 0xFF])
    return pl + crc16(pl)


def mb_write(reg: int, value: int) -> bytes:
    pl = bytes([0x01, 0x06, reg >> 8, reg & 0xFF, value >> 8, value & 0xFF])
    return pl + crc16(pl)


async def run(mac: str, v_set: float, i_limit: float, duration_s: float,
              interval_ms: int, out_path: Path) -> None:
    print(f"Scanning for {mac} ...", flush=True)
    device = await BleakScanner.find_device_by_address(mac, timeout=15)
    if not device:
        raise SystemExit(f"Device {mac} not found")

    print(f"Found: {device.name or mac}. Connecting...", flush=True)

    v_raw = round(v_set * 100)
    i_raw = round(i_limit * 100)

    # 9-register read: regs 10-18
    READ_REQ   = mb_read(REG_V_OUT, 9)   # response = 5 + 9*2 = 23 bytes
    READ_RESP  = 23
    WRITE_RESP = 8                         # fn=06 echo response is always 8 bytes

    async with BleakClient(device) as client:
        print(f"Connected.", flush=True)

        # Keep notify permanently subscribed — avoid per-transaction CCCD write overhead.
        # Use a queue so no notification fragment is ever lost.
        rx_queue: asyncio.Queue[bytes] = asyncio.Queue()

        def on_notify(_handle, data: bytearray) -> None:
            rx_queue.put_nowait(bytes(data))

        await client.start_notify(FFE1, on_notify)

        async def transact(request: bytes, expected_bytes: int,
                           timeout_s: float = 3.0) -> tuple[bytearray, float]:
            """Send a Modbus request, wait for ≥expected_bytes, return (data, rtt_ms)."""
            # Drain any stale fragments from a previous transaction
            while not rx_queue.empty():
                rx_queue.get_nowait()
            buf = bytearray()
            t0 = time.perf_counter()
            await client.write_gatt_char(FFE1, request, response=False)
            deadline = t0 + timeout_s
            while len(buf) < expected_bytes:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                try:
                    chunk = await asyncio.wait_for(rx_queue.get(), timeout=remaining)
                    buf.extend(chunk)
                except asyncio.TimeoutError:
                    break
            return buf, (time.perf_counter() - t0) * 1000.0

        # --- Pre-set: ensure output is OFF, then write V/I setpoints ---
        print(f"Setting output OFF...", flush=True)
        await transact(mb_write(REG_OUTPUT, 0), WRITE_RESP)

        print(f"Setting V={v_set:.2f}V I_limit={i_limit:.2f}A ...", flush=True)
        await transact(mb_write(REG_V_SET, v_raw), WRITE_RESP)
        await transact(mb_write(REG_I_SET, i_raw), WRITE_RESP)

        # --- Warm-up: two reads to flush stale BLE stack buffers on fresh connect ---
        await transact(READ_REQ, READ_RESP)
        await transact(READ_REQ, READ_RESP)

        rows: list[dict] = []
        interval_s = interval_ms / 1000.0

        # --- Capture pre-on snapshot ---
        data, rtt = await transact(READ_REQ, READ_RESP)
        regs = struct.unpack_from(">9H", data, 3)
        rows.append({
            "ts": time.time(), "v_out": regs[0] / 100, "i_out": regs[1] / 100,
            "cv_cc": "CC" if regs[7] else "CV",
            "protect": ["none", "OVP", "OCP"][min(regs[6], 2)],
            "output": bool(regs[8]), "rtt_ms": round(rtt, 2),
        })

        # --- Enable output ---
        print(f"Enabling output, capturing {duration_s}s at ~{interval_ms}ms ...", flush=True)
        await transact(mb_write(REG_OUTPUT, 1), WRITE_RESP)
        t_start = time.perf_counter()

        # --- Sample loop ---
        while time.perf_counter() - t_start < duration_s:
            loop_t0 = time.perf_counter()
            data, rtt = await transact(READ_REQ, READ_RESP)
            if len(data) < READ_RESP:
                print(f"  Short response ({len(data)} bytes), skipping", flush=True)
                elapsed = (time.perf_counter() - loop_t0)
                sleep = max(0.0, interval_s - elapsed)
                if sleep > 0:
                    await asyncio.sleep(sleep)
                continue

            regs = struct.unpack_from(">9H", data, 3)
            row = {
                "ts": time.time(),
                "v_out": regs[0] / 100,
                "i_out": regs[1] / 100,
                "cv_cc": "CC" if regs[7] else "CV",
                "protect": ["none", "OVP", "OCP"][min(regs[6], 2)],
                "output": bool(regs[8]),
                "rtt_ms": round(rtt, 2),
            }
            rows.append(row)
            elapsed_total = time.perf_counter() - t_start
            print(
                f"  {elapsed_total:5.2f}s  V={row['v_out']:5.2f}V  "
                f"I={row['i_out']:5.3f}A  {row['cv_cc']}  rtt={rtt:.0f}ms",
                flush=True,
            )
            elapsed = time.perf_counter() - loop_t0
            sleep = max(0.0, interval_s - elapsed)
            if sleep > 0:
                await asyncio.sleep(sleep)

        # --- Disable output ---
        print("Disabling output...", flush=True)
        await transact(mb_write(REG_OUTPUT, 0), WRITE_RESP)
        await client.stop_notify(FFE1)

    # --- Save ---
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    rtts = [r["rtt_ms"] for r in rows]
    print(f"\nSaved {len(rows)} rows → {out_path}")
    print(f"RTT:  min={min(rtts):.0f}ms  median={sorted(rtts)[len(rtts)//2]:.0f}ms  max={max(rtts):.0f}ms")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mac",         default=DEFAULT_MAC)
    p.add_argument("--v-set",       type=float, default=14.0)
    p.add_argument("--i-limit",     type=float, default=6.0)
    p.add_argument("--duration",    type=float, default=6.0)
    p.add_argument("--interval-ms", type=int,   default=80)
    p.add_argument("--out",         default="docs/data/globe_turnon_14v_ble80ms.jsonl")
    args = p.parse_args()
    asyncio.run(run(args.mac, args.v_set, args.i_limit, args.duration,
                    args.interval_ms, Path(args.out)))


if __name__ == "__main__":
    main()
