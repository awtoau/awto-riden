# riden_flash.py — Riden RD/RK60xx serial *bootloader* firmware loader.
#
# Lets firmware be flashed from Linux without the vendor PC software. Flashing is
# NOT Modbus register I/O — it speaks the unit's text-based bootloader protocol —
# so this module owns its own serial port rather than going through
# SerialTransport/RidenDevice (one owner, one port).
#
# LICENCE: MIT, same as the rest of this project. This is an INDEPENDENT
# implementation of the publicly-documented bootloader protocol below; it copies
# no third-party code. The protocol behaviour was cross-checked against the
# (GPLv3) tjko/riden-flashtool and bdd/riden-flashtool projects — credited in
# ATTRIBUTION.md as protocol references only.
#
# Bootloader serial protocol (8N1, default 115200 baud):
#   - b"queryd\r\n"   -> b"boot"   (4 bytes)  when already in bootloader mode
#   - Modbus FC06 write REBOOT_MAGIC (0x1601) to the SYSTEM register (0x100)
#                     -> b"\xfc", after which the unit reboots into the bootloader
#   - b"getinf\r\n"   -> 13-byte info block: b"inf" + serial(LE32) + model(LE16)
#                        + pad + fw(byte, version*100)
#   - b"upfirm\r\n"   -> b"upredy" (6 bytes), then the firmware image in 64-byte
#                        chunks, each acknowledged with b"OK"
#
# SAFETY: flashing a wrong/mismatched image can brick the unit. flash_firmware()
# refuses unless the bootloader-reported model is in SUPPORTED_MODELS and (when a
# model can be parsed from the firmware filename) matches it — overridable only
# with force=True.

from __future__ import annotations

import logging
import re
import struct
import time

import serial

from riden_register import Register as R

log = logging.getLogger("riden.flash")

# Device ids (= Riden model numbers) known to speak this bootloader protocol.
# Sources: riden-flashtool supported_models list + Riden model numbering.
SUPPORTED_MODELS: frozenset[int] = frozenset({
    60062,  # RD6006
    60065,  # RD6006P
    60066,  # RK6006
    60121,  # RD6012
    60125,  # RD6012P
    60181,  # RD6018
    60241,  # RD6024
    60301,  # RD6030
})

CHUNK_SIZE = 64          # bootloader consumes the image in 64-byte blocks
BOOTLOADER_QUERY = b"queryd\r\n"
BOOTLOADER_OK = b"boot"
UPFIRM_CMD = b"upfirm\r\n"
UPFIRM_READY = b"upredy"
GETINF_CMD = b"getinf\r\n"
CHUNK_ACK = b"OK"
REBOOT_ACK = b"\xfc"

# The unit drops off the bus and re-enumerates after the reboot command; it is
# unresponsive for a few seconds. 3 s matches the vendor updater's settle.
REBOOT_WAIT_S = 3.0


class FlashError(RuntimeError):
    """Raised when the bootloader protocol does not behave as expected."""


def _modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 0x0001) else (crc >> 1)
    return crc


def _fc06_frame(address: int, register: int, value: int) -> bytes:
    """Build a Modbus-RTU FC06 (write single register) frame."""
    pdu = struct.pack(">BBHH", address, 0x06, register, value)
    return pdu + struct.pack("<H", _modbus_crc16(pdu))


def model_from_filename(path: str) -> int | None:
    """Best-effort: parse the Riden model id from a firmware filename.

    Vendor images are named like ``RD60062_V1.32.bin`` / ``RD60066_V1.09.bin``;
    the 5-digit group is the model id. Returns None if nothing plausible found.
    """
    for m in re.findall(r"(\d{5})", path):
        if int(m) in SUPPORTED_MODELS:
            return int(m)
    return None


class RidenBootloader:
    """Client for the Riden serial bootloader (firmware flashing)."""

    def __init__(self, port: str, baud: int = 115200, address: int = 1,
                 timeout: float = 5.0) -> None:
        self._port_name = port
        self._baud = baud
        self._address = address
        self._timeout = timeout
        self._ser: serial.Serial | None = None

    # -- lifecycle --------------------------------------------------------
    def open(self) -> None:
        self._ser = serial.Serial(self._port_name, self._baud, timeout=self._timeout)

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def __enter__(self) -> "RidenBootloader":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low level --------------------------------------------------------
    def _io(self) -> serial.Serial:
        if self._ser is None:
            raise FlashError("bootloader port not open")
        return self._ser

    def _write(self, data: bytes) -> None:
        self._io().write(data)

    def _read(self, count: int) -> bytes:
        return self._io().read(count)

    # -- protocol ---------------------------------------------------------
    def in_bootloader(self) -> bool:
        """Return True if the unit is already in bootloader mode."""
        self._write(BOOTLOADER_QUERY)
        return self._read(len(BOOTLOADER_OK)) == BOOTLOADER_OK

    def enter_bootloader(self) -> None:
        """Ensure the unit is in bootloader mode, rebooting it via Modbus if needed."""
        if self.in_bootloader():
            log.info("device already in bootloader mode")
            return
        # Normal mode: ask it to reboot into the bootloader by writing
        # REBOOT_MAGIC to the SYSTEM register (see riden_register.py).
        frame = _fc06_frame(self._address, R.SYSTEM, R.REBOOT_MAGIC)
        log.info("rebooting into bootloader (SYSTEM <- 0x%04X)", R.REBOOT_MAGIC)
        self._write(frame)
        ack = self._read(1)
        if ack != REBOOT_ACK:
            raise FlashError(f"reboot-to-bootloader not acknowledged (got {ack!r})")
        # Unit re-enumerates; unresponsive for a few seconds.
        time.sleep(REBOOT_WAIT_S)
        if not self.in_bootloader():
            raise FlashError("device did not enter bootloader after reboot")

    def info(self) -> dict:
        """Query model / firmware / serial from the bootloader (``getinf``)."""
        self._write(GETINF_CMD)
        res = self._read(13)
        if len(res) != 13 or res[0:3] != b"inf":
            raise FlashError(f"invalid bootloader info response: {res!r}")
        snum = res[6] << 24 | res[5] << 16 | res[4] << 8 | res[3]
        model = res[8] << 8 | res[7]
        fw_raw = res[11]
        return {
            "model": model,
            "fw_raw": fw_raw,
            "fw": f"v{fw_raw / 100:.2f}",
            "serial": f"{snum:08d}",
            "supported": model in SUPPORTED_MODELS,
        }

    def write_firmware(self, firmware: bytes, progress=None) -> None:
        """Write *firmware* to the unit (it must already be in bootloader mode).

        *progress*, if given, is called as ``progress(done_bytes, total_bytes)``
        after each acknowledged chunk.
        """
        if not firmware:
            raise FlashError("empty firmware image")
        self._write(UPFIRM_CMD)
        res = self._read(len(UPFIRM_READY))
        if res != UPFIRM_READY:
            raise FlashError(f"bootloader did not accept upfirm (got {res!r})")
        total = len(firmware)
        pos = 0
        while pos < total:
            self._write(firmware[pos:pos + CHUNK_SIZE])
            ack = self._read(len(CHUNK_ACK))
            if ack != CHUNK_ACK:
                raise FlashError(f"firmware chunk at offset {pos} rejected (got {ack!r})")
            pos += CHUNK_SIZE
            if progress is not None:
                progress(min(pos, total), total)


def flash_firmware(
    port: str,
    firmware_path: str | None = None,
    *,
    baud: int = 115200,
    address: int = 1,
    confirm: bool = False,
    force: bool = False,
    progress=None,
) -> dict:
    """Reboot the unit to its bootloader and (optionally) flash a firmware image.

    With *firmware_path* None this only reboots to the bootloader and returns its
    reported info — useful to verify the path before committing to a flash. NOTE
    that even this leaves the unit IN the bootloader; power-cycle it (or complete
    a flash) to return to normal operation.

    Actually writing firmware requires *confirm=True*. Safety: the
    bootloader-reported model must be in SUPPORTED_MODELS, and (when the firmware
    filename encodes a model) must match it — unless *force=True*.

    Returns a dict: {bootloader info..., "flashed": bool, "bytes": int|None}.
    """
    file_model = model_from_filename(firmware_path) if firmware_path else None
    firmware: bytes | None = None
    if firmware_path is not None:
        with open(firmware_path, "rb") as fh:
            firmware = fh.read()

    with RidenBootloader(port, baud=baud, address=address) as bl:
        bl.enter_bootloader()
        meta = bl.info()
        log.info("bootloader: model=%s fw=%s sn=%s supported=%s",
                 meta["model"], meta["fw"], meta["serial"], meta["supported"])

        result = dict(meta)
        result["flashed"] = False
        result["bytes"] = None

        if firmware is None:
            return result  # reboot-to-bootloader + info only

        if not confirm:
            raise FlashError(
                "refusing to flash without confirm=True (this overwrites device firmware)"
            )
        if not meta["supported"] and not force:
            raise FlashError(
                f"device model {meta['model']} not in SUPPORTED_MODELS; pass force=True to override"
            )
        if file_model is not None and file_model != meta["model"] and not force:
            raise FlashError(
                f"firmware file model {file_model} != device model {meta['model']}; "
                "pass force=True to override (DANGEROUS)"
            )

        bl.write_firmware(firmware, progress=progress)
        result["flashed"] = True
        result["bytes"] = len(firmware)
        return result
