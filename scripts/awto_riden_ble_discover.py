#!/usr/bin/env python3
"""
BLE discovery script — find RK6006 devices and their Modbus characteristics.
"""

import asyncio
import sys

from bleak import BleakClient


async def main():
    # Known RK6006 MACs from bluetoothctl
    mac = "88:BB:52:09:E5:43"
    
    print(f"Connecting to {mac}...")
    try:
        async with BleakClient(mac, timeout=5.0) as client:
            print(f"✓ Connected")
            
            # List all services and characteristics
            svcs = await client.get_services()
            for svc in svcs:
                print(f"\nService: {svc.uuid}")
                for char in svc.characteristics:
                    props = ", ".join(char.properties)
                    print(f"  {char.uuid} [{props}]")
                    if "notify" in char.properties:
                        print(f"    → Found notify characteristic: {char.uuid}")
                    if "write" in char.properties or "write-without-response" in char.properties:
                        print(f"    → Found writable characteristic: {char.uuid}")
    
    except asyncio.TimeoutError:
        print(f"✗ Connection timeout — device may be out of range or not responding")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
