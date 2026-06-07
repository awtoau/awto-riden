"""awto_riden_raw_read.py — bypass modbus-tk entirely, talk raw bytes to the PSU.

Sends a Modbus RTU FC03 read-holding-registers request manually,
reads the raw response, verifies CRC, and decodes register values.

Usage:
    python3 scripts/awto_riden_raw_read.py [/dev/ttyUSB0]
"""
import struct
import sys
import time
import serial


PORT  = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
BAUD  = 115200
SLAVE = 1

# Registers to read (same block as riden_daemon.py)
REG_START = 10   # V_OUT
REG_COUNT = 9    # V_OUT, I_OUT, POWER_H, POWER_L, V_IN, PROTECT, CV_CC, UNKNOWN, OUTPUT


def crc16(data: bytes) -> int:
    """Modbus CRC-16."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def build_fc03(slave: int, reg: int, count: int) -> bytes:
    pdu = struct.pack(">BBHH", slave, 0x03, reg, count)
    crc = crc16(pdu)
    return pdu + struct.pack("<H", crc)  # CRC appended little-endian


def parse_fc03_response(data: bytes, expected_regs: int) -> list[int] | None:
    """Return list of register values, or None on error."""
    expected_len = 5 + expected_regs * 2  # addr + fc + byte_count + regs*2 + crc*2
    if len(data) < expected_len:
        print(f"  Short response: got {len(data)} bytes, expected {expected_len}")
        return None
    # Verify CRC over everything except last 2 bytes
    body = data[:-2]
    recv_crc = struct.unpack("<H", data[-2:])[0]
    calc_crc = crc16(body)
    if recv_crc != calc_crc:
        print(f"  CRC MISMATCH: received 0x{recv_crc:04X}, calculated 0x{calc_crc:04X}")
        return None
    slave_addr = data[0]
    func_code  = data[1]
    byte_count = data[2]
    if func_code != 0x03:
        print(f"  Unexpected function code: 0x{func_code:02X}")
        return None
    regs = list(struct.unpack_from(f">{expected_regs}H", data, 3))
    return regs


def main():
    print(f"Opening {PORT} at {BAUD} baud (raw, no modbus-tk)")
    ser = serial.Serial(PORT, BAUD, timeout=1.0)
    time.sleep(0.1)
    ser.reset_input_buffer()

    request = build_fc03(SLAVE, REG_START, REG_COUNT)
    print(f"\nRequest  ({len(request)} bytes): {request.hex(' ').upper()}")

    t0 = time.perf_counter()
    ser.write(request)
    # FC03 response: 1 addr + 1 fc + 1 byte_count + REG_COUNT*2 bytes + 2 CRC
    expected_bytes = 5 + REG_COUNT * 2
    response = ser.read(expected_bytes)
    dt = (time.perf_counter() - t0) * 1000

    print(f"Response ({len(response)} bytes, {dt:.1f} ms): {response.hex(' ').upper()}")

    regs = parse_fc03_response(response, REG_COUNT)
    if regs is None:
        print("FAILED — raw bytes above, no interpretation")
        ser.close()
        return

    # Decode (RD6006: v_multi=100, i_multi=1000)
    v_out  = regs[0] / 100
    i_out  = regs[1] / 1000
    power  = (regs[2] << 16 | regs[3]) / 100
    v_in   = regs[4] / 100
    prot   = {0: "none", 1: "OVP", 2: "OCP"}.get(regs[5], f"0x{regs[5]:X}")
    cv_cc  = "CV" if regs[6] == 0 else "CC"
    output = bool(regs[8])

    print(f"\nDecoded (RD6006 multipliers):")
    print(f"  V_out  = {v_out:.2f} V")
    print(f"  I_out  = {i_out:.3f} A")
    print(f"  P_out  = {power:.2f} W")
    print(f"  V_in   = {v_in:.2f} V")
    print(f"  Prot   = {prot}")
    print(f"  Mode   = {cv_cc}")
    print(f"  Output = {'ON' if output else 'OFF'}")
    print(f"  Raw regs: {regs}")

    ser.close()


if __name__ == "__main__":
    main()
