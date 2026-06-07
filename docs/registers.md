# Riden RD60xx/RK60xx Register Map

> **GENERATED FILE — do not edit by hand.**
> Regenerate with: `scripts/awto-riden-dev.py gen-docs`
> Single source of truth: `register_map.py` (built on `riden_register.py`).

## Address-space coverage (0–256)

| Range | Status | Notes |
|---|---|---|
| 0–20 | Known | Core identity, measurements, output state |
| 21–31 | Unknown | Reserved/undocumented |
| 32–41 | Known | Battery/temp/energy counters |
| 42–47 | Unknown | Reserved/undocumented |
| 48–53 | Known | RTC date/time |
| 54 | Known | Calibration commit register |
| 55–62 | Known | Calibration trim registers |
| 63–65 | Unknown | Reserved/undocumented |
| 66–72 | Known | UI/options |
| 73–79 | Unknown | Reserved/undocumented |
| 80–119 | Known | Presets M0-M9 |
| 120–181 | Unknown | Reserved/undocumented |
| 182–195 | Empirical | Metadata/calibration mirror cluster on RD6024 fw109 |
| 196–207 | Unknown | Reserved/undocumented |
| 208–239 | Empirical | Extended presets M10-M17 (V/I/OVP/OCP) |
| 240–255 | Unknown | Reserved/undocumented |
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
| 54 | CAL_COMMIT | Write 0x1501 to commit calibration |
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
| 80 | M0_V | Preset M0 voltage setpoint |
| 81 | M0_I | Preset M0 current setpoint |
| 82 | M0_OVP | Preset M0 OVP threshold |
| 83 | M0_OCP | Preset M0 OCP threshold |
| 84 | M1_V | Preset M1 voltage setpoint |
| 85 | M1_I | Preset M1 current setpoint |
| 86 | M1_OVP | Preset M1 OVP threshold |
| 87 | M1_OCP | Preset M1 OCP threshold |
| 88 | M2_V | Preset M2 voltage setpoint |
| 89 | M2_I | Preset M2 current setpoint |
| 90 | M2_OVP | Preset M2 OVP threshold |
| 91 | M2_OCP | Preset M2 OCP threshold |
| 92 | M3_V | Preset M3 voltage setpoint |
| 93 | M3_I | Preset M3 current setpoint |
| 94 | M3_OVP | Preset M3 OVP threshold |
| 95 | M3_OCP | Preset M3 OCP threshold |
| 96 | M4_V | Preset M4 voltage setpoint |
| 97 | M4_I | Preset M4 current setpoint |
| 98 | M4_OVP | Preset M4 OVP threshold |
| 99 | M4_OCP | Preset M4 OCP threshold |
| 100 | M5_V | Preset M5 voltage setpoint |
| 101 | M5_I | Preset M5 current setpoint |
| 102 | M5_OVP | Preset M5 OVP threshold |
| 103 | M5_OCP | Preset M5 OCP threshold |
| 104 | M6_V | Preset M6 voltage setpoint |
| 105 | M6_I | Preset M6 current setpoint |
| 106 | M6_OVP | Preset M6 OVP threshold |
| 107 | M6_OCP | Preset M6 OCP threshold |
| 108 | M7_V | Preset M7 voltage setpoint |
| 109 | M7_I | Preset M7 current setpoint |
| 110 | M7_OVP | Preset M7 OVP threshold |
| 111 | M7_OCP | Preset M7 OCP threshold |
| 112 | M8_V | Preset M8 voltage setpoint |
| 113 | M8_I | Preset M8 current setpoint |
| 114 | M8_OVP | Preset M8 OVP threshold |
| 115 | M8_OCP | Preset M8 OCP threshold |
| 116 | M9_V | Preset M9 voltage setpoint |
| 117 | M9_I | Preset M9 current setpoint |
| 118 | M9_OVP | Preset M9 OVP threshold |
| 119 | M9_OCP | Preset M9 OCP threshold |
| 208 | M10_V | Preset M10 voltage setpoint |
| 209 | M10_I | Preset M10 current setpoint |
| 210 | M10_OVP | Preset M10 OVP threshold |
| 211 | M10_OCP | Preset M10 OCP threshold |
| 212 | M11_V | Preset M11 voltage setpoint |
| 213 | M11_I | Preset M11 current setpoint |
| 214 | M11_OVP | Preset M11 OVP threshold |
| 215 | M11_OCP | Preset M11 OCP threshold |
| 216 | M12_V | Preset M12 voltage setpoint |
| 217 | M12_I | Preset M12 current setpoint |
| 218 | M12_OVP | Preset M12 OVP threshold |
| 219 | M12_OCP | Preset M12 OCP threshold |
| 220 | M13_V | Preset M13 voltage setpoint |
| 221 | M13_I | Preset M13 current setpoint |
| 222 | M13_OVP | Preset M13 OVP threshold |
| 223 | M13_OCP | Preset M13 OCP threshold |
| 224 | M14_V | Preset M14 voltage setpoint |
| 225 | M14_I | Preset M14 current setpoint |
| 226 | M14_OVP | Preset M14 OVP threshold |
| 227 | M14_OCP | Preset M14 OCP threshold |
| 228 | M15_V | Preset M15 voltage setpoint |
| 229 | M15_I | Preset M15 current setpoint |
| 230 | M15_OVP | Preset M15 OVP threshold |
| 231 | M15_OCP | Preset M15 OCP threshold |
| 232 | M16_V | Preset M16 voltage setpoint |
| 233 | M16_I | Preset M16 current setpoint |
| 234 | M16_OVP | Preset M16 OVP threshold |
| 235 | M16_OCP | Preset M16 OCP threshold |
| 236 | M17_V | Preset M17 voltage setpoint |
| 237 | M17_I | Preset M17 current setpoint |
| 238 | M17_OVP | Preset M17 OVP threshold |
| 239 | M17_OCP | Preset M17 OCP threshold |
| 256 | SYSTEM | System control; write 0x1601 to enter bootloader |

## Magic command values

| Value | Name | Use |
|---|---|---|
| `0x1501` (5377) | CAL_COMMIT_MAGIC | Write to `CAL_COMMIT` (54) to persist calibration |
| `0x1601` (5633) | REBOOT_MAGIC | Write to `SYSTEM` (256) to enter bootloader |

## Raw → engineering multipliers

| Model family | V | I | P |
|---|--:|--:|--:|
| RD6006 / RK6006 | 100 | 1000 | 100 |
| RD6006P | 1000 | 10000 | 1000 |
| RD6012 / RD6018 / RD6024 | 100 | 100 | 100 |

e.g. raw `V_OUT = 1200` × (1/100) = **12.00 V**.

## Evidence

- **RK6006 (id 60066, fw v1.09):** full read scan (0–260) shows the documented
  map plus addrs **120–207 reading `0xFFFF` (unimplemented)** and **zero**
  undocumented live registers. The RD6024 empirical banks (182–239) are NOT
  present on RK firmware.
- Sources: `riden_register.py`, Baldanos/rd6006, ShayBox/Riden,
  rssdev10/tjko riden-flashtool (magic values). See `ATTRIBUTION.md`.

