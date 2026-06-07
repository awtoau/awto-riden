# riden_register.py — Modbus register map for Riden RD60xx / RK60xx power supplies.
#
# Register map cross-referenced from various sources — Baldanos/rd6006
# registers.md (the community map), ShayBox/Riden, and riden-flashtool (the
# calibration/reboot magic values). See ATTRIBUTION.md.
#   https://github.com/Baldanos/rd6006/blob/master/registers.md
#
#   Live register scan (RD6024 fw109, 2026-05-03) — empirical observations
#   for clusters 182-195 and 208-239:
#   docs/data/register_scan_20260503-011913Z.json


class Register:
    # Init
    ID = 0
    SN_H = 1
    SN_L = 2
    FW = 3
    # Info
    INT_C_S = 4
    INT_C = 5
    INT_F_S = 6
    INT_F = 7
    V_SET = 8
    I_SET = 9
    V_OUT = 10
    I_OUT = 11
    AH = 12
    P_OUT = 13
    V_IN = 14
    KEYPAD = 15
    OVP_OCP = 16
    CV_CC = 17
    OUTPUT = 18
    PRESET = 19
    I_RANGE = 20  # Used on RD6012p
    # Unused/Unknown 21-31
    BAT_MODE = 32
    V_BAT = 33
    EXT_C_S = 34
    EXT_C = 35
    EXT_F_S = 36
    EXT_F = 37
    AH_H = 38
    AH_L = 39
    WH_H = 40
    WH_L = 41
    # Unused/Unknown 42-47
    # Date
    YEAR = 48
    MONTH = 49
    DAY = 50
    # Time
    HOUR = 51
    MINUTE = 52
    SECOND = 53
    # Commit calibration registers to NVRAM.
    # Write CAL_COMMIT_MAGIC (0x1501 = 5377) to persist calibration.
    # Source: Baldanos/rd6006 registers.md; rssdev10/riden-flashtool common.rs
    CAL_COMMIT = 54
    CAL_COMMIT_MAGIC = 0x1501  # 5377 — write to reg 54 to commit calibration
    # Calibration
    # DO NOT CHANGE Unless you know what you're doing!
    # Register naming: rssdev10/riden-flashtool uses "Target" for the DAC-side
    # calibration and "Display" for the ADC/readback side. ShayBox names them
    # V_OUT/V_BACK and I_OUT/I_BACK respectively.
    V_OUT_ZERO = 55   # Target V Offset  (DAC zero trim)
    V_OUT_SCALE = 56  # Target V Scale   (DAC scale)
    V_BACK_ZERO = 57  # Display V Offset (ADC zero trim)
    V_BACK_SCALE = 58 # Display V Scale  (ADC scale)
    I_OUT_ZERO = 59   # Target I Offset  (DAC zero trim)
    I_OUT_SCALE = 60  # Target I Scale   (DAC scale)
    I_BACK_ZERO = 61  # Display I Offset (ADC zero trim)
    I_BACK_SCALE = 62 # Display I Scale  (ADC scale)
    # Unused/Unknown 63-65
    # Settings/Options
    OPT_TAKE_OK = 66
    OPT_TAKE_OUT = 67
    OPT_BOOT_POW = 68
    OPT_BUZZ = 69
    OPT_LOGO = 70
    OPT_LANG = 71
    OPT_LIGHT = 72
    # Unused/Unknown 73-79
    # Presets
    M0_V = 80
    M0_I = 81
    M0_OVP = 82
    M0_OCP = 83
    M1_V = 84
    M1_I = 85
    M1_OVP = 86
    M1_OCP = 87
    M2_V = 88
    M2_I = 89
    M2_OVP = 90
    M2_OCP = 91
    M3_V = 92
    M3_I = 93
    M3_OVP = 94
    M3_OCP = 95
    M4_V = 96
    M4_I = 97
    M4_OVP = 98
    M4_OCP = 99
    M5_V = 100
    M5_I = 101
    M5_OVP = 102
    M5_OCP = 103
    M6_V = 104
    M6_I = 105
    M6_OVP = 106
    M6_OCP = 107
    M7_V = 108
    M7_I = 109
    M7_OVP = 110
    M7_OCP = 111
    M8_V = 112
    M8_I = 113
    M8_OVP = 114
    M8_OCP = 115
    M9_V = 116
    M9_I = 117
    M9_OVP = 118
    M9_OCP = 119
    # Unused/Unknown 120-181
    # Empirical cluster A (RD6024 fw109, scan 2026-05-03): 182-195
    # Appears to be firmware metadata / calibration mirror bank.
    # reg 182 = firmware revision copy; regs 186-192 mirror calibration constants.
    # Source: docs/data/register_scan_20260503-011913Z.json
    # Unused/Unknown 196-207
    # Extended preset bank M10..M17 — empirically confirmed (RD6024 fw1.39, scan 2026-05-03).
    # Repeating 4-register pattern (V / I / OVP / OCP), same layout as M0..M9 at 80..119.
    # Not documented in Baldanos/rd6006 or ShayBox/Riden; discovered via live register scan.
    # Source: docs/data/register_scan_20260503-011913Z.json + riden-flashtool dump-regs
    M10_V = 208
    M10_I = 209
    M10_OVP = 210
    M10_OCP = 211
    M11_V = 212
    M11_I = 213
    M11_OVP = 214
    M11_OCP = 215
    M12_V = 216
    M12_I = 217
    M12_OVP = 218
    M12_OCP = 219
    M13_V = 220
    M13_I = 221
    M13_OVP = 222
    M13_OCP = 223
    M14_V = 224
    M14_I = 225
    M14_OVP = 226
    M14_OCP = 227
    M15_V = 228
    M15_I = 229
    M15_OVP = 230
    M15_OCP = 231
    M16_V = 232
    M16_I = 233
    M16_OVP = 234
    M16_OCP = 235
    M17_V = 236
    M17_I = 237
    M17_OVP = 238
    M17_OCP = 239
    # Unused/Unknown 240-255
    # System control — enter bootloader by writing REBOOT_MAGIC.
    # Source: rssdev10/riden-flashtool common.rs (REBOOT_REGISTER / REBOOT_MAGIC)
    SYSTEM = 256
    REBOOT_MAGIC = 0x1601  # 5633 — write to SYSTEM to enter bootloader
    # NOT REGISTERS — magic numbers used with SYSTEM register
    BOOTLOADER = 5633
