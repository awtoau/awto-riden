# Riden RD60xx/RK60xx Register Map

This page documents the register map currently used by this repo and tracks known vs unknown regions.

Canonical code source:
- `riden_register.py`

Additional attribution and evidence:
- Baldanos/rd6006 register map
- ShayBox/Riden constants
- rssdev10/riden-flashtool (CAL_COMMIT, reboot magic)
- Live scan evidence: `docs/data/register_scan_20260503-011913Z.json`

## Device/Firmware compatibility notes

Do not assume every register/range is identical across RD and RK firmware.

Observed in this repo:

| Device | Firmware | Evidence | Notes |
|---|---|---|---|
| RD6024 (id 60241) | v1.39 (`fw_raw=139`) | MCP `rd_firmware`, `rd_status`, `rd_register_scan` on `/dev/ttyUSB1` | Extended bank and empirical clusters are verified on this target. |
| RK6006 (id 60066) | v1.09 (`fw=109`) | Existing report artifacts under `docs/reports/rk6006-id60066-fw109/` | RK firmware may differ; treat RD6024-only empirical ranges as non-portable until scanned on RK. |

Practical rule:
- Registers in the upstream/common map (`0..119`, `256`) are generally reliable cross-model.
- Empirical ranges (`182..195`, `208..239`) should be treated as firmware-specific until re-verified per device.

Recommended verification workflow (existing interfaces only):

```bash
source .venv/bin/activate
python3 ttu_cli.py --port /dev/ttyUSBX --baud 115200 --address 1 register-scan --start 0 --end 260 --batch 50 --report-only
python3 ttu_cli.py --port /dev/ttyUSBX --baud 115200 --address 1 diff-scan --start 0 --end 260 --batch 50 --settle-ms 300 --unknown-only
```

If a unit does not answer at address 1, retry with its configured Modbus address.

## Address-space coverage

This project accounts for addresses `0..256` as follows.

| Range | Status | Notes |
|---|---|---|
| 0-20 | Known | Core identity, measurements, output state |
| 21-31 | Unknown | Reserved/undocumented |
| 32-41 | Known | Battery/temp/energy counters |
| 42-47 | Unknown | Reserved/undocumented |
| 48-53 | Known | RTC date/time |
| 54 | Known | Calibration commit register |
| 55-62 | Known | Calibration trim registers |
| 63-65 | Unknown | Reserved/undocumented |
| 66-72 | Known | UI/options |
| 73-79 | Unknown | Reserved/undocumented |
| 80-119 | Known | Presets M0-M9 |
| 120-181 | Unknown | Reserved/undocumented |
| 182-195 | Empirical | Metadata/calibration mirror cluster on RD6024 fw109 |
| 196-207 | Unknown | Reserved/undocumented |
| 208-239 | Empirical | Extended presets M10-M17 (V/I/OVP/OCP) |
| 240-255 | Unknown | Reserved/undocumented |
| 256 | Known | System control (bootloader trigger) |

## Register details

| Addr | Name | Description |
|---|---|---|
| 0 | ID | Device ID/model code |
| 1 | SN_H | Serial number high word |
| 2 | SN_L | Serial number low word |
| 3 | FW | Firmware version code |
| 4 | INT_C_S | Internal temperature sign (C) |
| 5 | INT_C | Internal temperature value (C) |
| 6 | INT_F_S | Internal temperature sign (F) |
| 7 | INT_F | Internal temperature value (F) |
| 8 | V_SET | Voltage setpoint raw |
| 9 | I_SET | Current setpoint raw |
| 10 | V_OUT | Output voltage raw |
| 11 | I_OUT | Output current raw |
| 12 | AH | Amp-hour counter raw |
| 13 | P_OUT | Output power raw |
| 14 | V_IN | Input voltage raw |
| 15 | KEYPAD | Keypad lock state |
| 16 | OVP_OCP | Protection state (none/OVP/OCP) |
| 17 | CV_CC | Regulation mode (CV/CC) |
| 18 | OUTPUT | Output enabled state |
| 19 | PRESET | Active preset index |
| 20 | I_RANGE | Current range selector (RD6012P) |
| 32 | BAT_MODE | Battery mode |
| 33 | V_BAT | Battery voltage |
| 34 | EXT_C_S | External temperature sign (C) |
| 35 | EXT_C | External temperature value (C) |
| 36 | EXT_F_S | External temperature sign (F) |
| 37 | EXT_F | External temperature value (F) |
| 38 | AH_H | Amp-hour counter high word |
| 39 | AH_L | Amp-hour counter low word |
| 40 | WH_H | Watt-hour counter high word |
| 41 | WH_L | Watt-hour counter low word |
| 48 | YEAR | RTC year |
| 49 | MONTH | RTC month |
| 50 | DAY | RTC day |
| 51 | HOUR | RTC hour |
| 52 | MINUTE | RTC minute |
| 53 | SECOND | RTC second |
| 54 | CAL_COMMIT | Write `0x1501` to commit calibration |
| 55 | V_OUT_ZERO | Target voltage offset trim (DAC) |
| 56 | V_OUT_SCALE | Target voltage scale trim (DAC) |
| 57 | V_BACK_ZERO | Display voltage offset trim (ADC) |
| 58 | V_BACK_SCALE | Display voltage scale trim (ADC) |
| 59 | I_OUT_ZERO | Target current offset trim (DAC) |
| 60 | I_OUT_SCALE | Target current scale trim (DAC) |
| 61 | I_BACK_ZERO | Display current offset trim (ADC) |
| 62 | I_BACK_SCALE | Display current scale trim (ADC) |
| 66 | OPT_TAKE_OK | Option: take OK behavior |
| 67 | OPT_TAKE_OUT | Option: take output behavior |
| 68 | OPT_BOOT_POW | Option: boot power state |
| 69 | OPT_BUZZ | Option: buzzer |
| 70 | OPT_LOGO | Option: startup logo |
| 71 | OPT_LANG | Option: language |
| 72 | OPT_LIGHT | Option: display brightness |
| 80-83 | M0_V..M0_OCP | Preset bank M0 |
| 84-87 | M1_V..M1_OCP | Preset bank M1 |
| 88-91 | M2_V..M2_OCP | Preset bank M2 |
| 92-95 | M3_V..M3_OCP | Preset bank M3 |
| 96-99 | M4_V..M4_OCP | Preset bank M4 |
| 100-103 | M5_V..M5_OCP | Preset bank M5 |
| 104-107 | M6_V..M6_OCP | Preset bank M6 |
| 108-111 | M7_V..M7_OCP | Preset bank M7 |
| 112-115 | M8_V..M8_OCP | Preset bank M8 |
| 116-119 | M9_V..M9_OCP | Preset bank M9 |
| 208-211 | M10_V..M10_OCP | Extended preset bank M10 (empirical) |
| 212-215 | M11_V..M11_OCP | Extended preset bank M11 (empirical) |
| 216-219 | M12_V..M12_OCP | Extended preset bank M12 (empirical) |
| 220-223 | M13_V..M13_OCP | Extended preset bank M13 (empirical) |
| 224-227 | M14_V..M14_OCP | Extended preset bank M14 (empirical) |
| 228-231 | M15_V..M15_OCP | Extended preset bank M15 (empirical) |
| 232-235 | M16_V..M16_OCP | Extended preset bank M16 (empirical) |
| 236-239 | M17_V..M17_OCP | Extended preset bank M17 (empirical) |
| 256 | SYSTEM | System control; write `0x1601` to enter bootloader |

## Magic values

| Value | Use |
|---|---|
| `0x1501` (5377) | Write to `CAL_COMMIT` (54) to persist calibration |
| `0x1601` (5633) | Write to `SYSTEM` (256) to enter bootloader |

## Raw-to-engineering multipliers

Scaling depends on model family.

| Model family | V multiplier | I multiplier | P multiplier |
|---|---:|---:|---:|
| RD6006 / RK6006 | 100 | 1000 | 100 |
| RD6006P | 1000 | 10000 | 1000 |
| RD6012 / RD6018 / RD6024 | 100 | 100 | 100 |

Example:
- Raw `V_OUT = 1200` with `V multiplier = 100` means `12.00 V`.

## Notes

- `182-195` and `208-239` are based on empirical scans on RD6024 fw109 and should be treated as validated for that target, not universally guaranteed across all firmware.
- Unknown ranges are intentionally listed to keep reverse-engineering scope explicit.
- RK6006 uses different firmware lineage from RD6024 and may not expose all empirical RD ranges with identical semantics.
