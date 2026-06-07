"""
BLE transport latency profiler — mirrors profile-serial metrics.

Connects to the RK6006 BLE UART bridge (service FFE0, char FFE1),
sends repeated Modbus RTU reads, measures round-trip latency, and
prints the same JSON structure as profile-serial for easy comparison.

Usage:
    python3 scripts/awto_riden_ble_profile.py [--mac AA:BB:CC:DD:EE:FF] [--count 30] [--sleep-ms 200]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import struct
import time
from pathlib import Path

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    raise SystemExit("bleak is required: pip install bleak")

DEFAULT_MAC = "88:BB:52:09:E5:43"
FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"

# Modbus RTU read: addr=1, fn=03, start=10, count=9
REG_START = 10
REG_COUNT = 9
RESP_BYTES = 5 + REG_COUNT * 2  # addr(1)+fn(1)+byte_count(1)+data(18)+crc(2) = 23


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def build_request() -> bytes:
    payload = bytes([0x01, 0x03, 0x00, REG_START, 0x00, REG_COUNT])
    return payload + crc16(payload)


async def run(mac: str, count: int, sleep_ms: int, save_json: "Path | None" = None) -> None:
    req = build_request()
    print(f"Scanning for {mac}...", flush=True)

    device = await BleakScanner.find_device_by_address(mac, timeout=15)
    if not device:
        raise SystemExit(f"Device {mac} not found (is it powered on and BLE enabled?)")

    print(f"Found: {device.name or mac}. Connecting...", flush=True)

    async with BleakClient(device) as client:
        # Keep notify permanently subscribed — avoid per-poll CCCD write overhead.
        # Use a queue so no notification fragment is ever lost.
        rx_queue: asyncio.Queue[bytes] = asyncio.Queue()

        def on_notify(_handle, data: bytearray) -> None:
            rx_queue.put_nowait(bytes(data))

        await client.start_notify(FFE1, on_notify)

        async def single_poll() -> float:
            # Drain any stale data from previous transactions
            while not rx_queue.empty():
                rx_queue.get_nowait()
            buf = bytearray()
            t0 = time.perf_counter()
            await client.write_gatt_char(FFE1, req, response=False)
            deadline = t0 + 5.0
            while len(buf) < RESP_BYTES:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                try:
                    chunk = await asyncio.wait_for(rx_queue.get(), timeout=remaining)
                    buf.extend(chunk)
                except asyncio.TimeoutError:
                    break
            return (time.perf_counter() - t0) * 1000.0

        print(f"Connected. Warming up...", flush=True)
        # Two warm-up polls: first absorbs any stale buffered notifications from
        # a previous session; second gives the BLE connection interval time to stabilise.
        await single_poll()
        await asyncio.sleep(sleep_ms / 1000.0 if sleep_ms > 0 else 0.15)
        await single_poll()
        if sleep_ms > 0:
            await asyncio.sleep(sleep_ms / 1000.0)

        print(f"Profiling {count} polls (sleep={sleep_ms}ms between each)...", flush=True)
        times_ms: list[float] = []
        for i in range(count):
            rtt = await single_poll()
            times_ms.append(rtt)
            print(f"  [{i+1:3d}/{count}] {rtt:.1f} ms", flush=True)
            if sleep_ms > 0 and i < count - 1:
                await asyncio.sleep(sleep_ms / 1000.0)

        await client.stop_notify(FFE1)

    samples = sorted(times_ms)
    p50 = statistics.median(samples)
    p90 = samples[int(0.9 * (len(samples) - 1))]
    p95 = samples[int(0.95 * (len(samples) - 1))]
    jitter = max(0.0, p95 - p50)

    raw_recommended_ms = p95 + max(3.0, p95 * 0.10)
    quantization_ms = 50 if raw_recommended_ms >= 250.0 else 20
    recommended_poll_ms = int(math.ceil(raw_recommended_ms / quantization_ms) * quantization_ms)

    result = {
        "recommended_poll_ms": recommended_poll_ms,
        "strategy": "stable-cadence",
        "raw_recommended_poll_ms": round(raw_recommended_ms, 2),
        "quantization_ms": quantization_ms,
        "transport": "ble-uart",
        "mac": mac,
        "wire_theory_ms": None,  # BLE has no simple wire-time calc
        "register_read": {"start": REG_START, "count": REG_COUNT},
        "timing": {
            "count": count,
            "sleep_ms": sleep_ms,
            "ok": len(times_ms),
            "min_ms": round(min(samples), 2),
            "median_ms": round(p50, 2),
            "p90_ms": round(p90, 2),
            "p95_ms": round(p95, 2),
            "max_ms": round(max(samples), 2),
            "mean_ms": round(statistics.mean(samples), 2),
            "jitter_p95_minus_p50_ms": round(jitter, 2),
        },
        "raw_rtt_ms": [round(t, 2) for t in times_ms],
        "notes": [
            "BLE RTT dominated by connection interval (typically 30-100 ms) + notify latency.",
            "write_gatt_char uses write-without-response; notify carries the reply.",
            "USB-serial reference: median~137ms, p95~138ms, recommended_poll=160ms.",
        ],
    }
    print()
    print(json.dumps(result, indent=2))

    if save_json:
        save_json.parent.mkdir(parents=True, exist_ok=True)
        save_json.write_text(json.dumps(result, indent=2))
        print(f"\nSaved → {save_json}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mac", default=DEFAULT_MAC, help="BLE device MAC address")
    p.add_argument("--count", type=int, default=30, help="number of polls (default: 30)")
    p.add_argument("--sleep-ms", type=int, default=200, help="sleep between polls in ms (default: 200)")
    p.add_argument("--save-json", type=Path, default=None,
                   help="save full result JSON (including raw_rtt_ms) to this file")
    args = p.parse_args()
    asyncio.run(run(args.mac, args.count, args.sleep_ms, args.save_json))


if __name__ == "__main__":
    main()
