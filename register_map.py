# register_map.py — single source of truth for the Riden register map (rich).
#
# Addresses and names come from riden_register.Register (the canonical source).
# THIS module is the one place to maintain the
# human-facing map on top of that: per-register descriptions, the known/unknown
# range classification, the command "magic" values, and the raw->engineering
# multipliers. Both the dev analysis tool (awto-riden-dev.py) and the docs
# generator read from here, so docs/registers.md is GENERATED, never hand-edited.

from __future__ import annotations

from riden_register import Register as R

MAX_ADDR = 256

# Per-register descriptions, keyed by the canonical names in riden_register.py.
# Preset-bank descriptions (M0..M17) are generated, so they are not listed here.
DESCRIPTIONS: dict[str, str] = {
    "ID": "Device ID/model code",
    "SN_H": "Serial number high word",
    "SN_L": "Serial number low word",
    "FW": "Firmware version code",
    "INT_C_S": "Internal temperature sign (C)",
    "INT_C": "Internal temperature value (C)",
    "INT_F_S": "Internal temperature sign (F)",
    "INT_F": "Internal temperature value (F)",
    "V_SET": "Voltage setpoint raw",
    "I_SET": "Current setpoint raw",
    "V_OUT": "Output voltage raw",
    "I_OUT": "Output current raw",
    "AH": "Amp-hour counter raw",
    "P_OUT": "Output power raw",
    "V_IN": "Input voltage raw",
    "KEYPAD": "Keypad lock state",
    "OVP_OCP": "Protection state (none/OVP/OCP)",
    "CV_CC": "Regulation mode (CV/CC)",
    "OUTPUT": "Output enabled state",
    "PRESET": "Active preset index",
    "I_RANGE": "Current range selector (RD6012P)",
    "BAT_MODE": "Battery mode",
    "V_BAT": "Battery voltage",
    "EXT_C_S": "External temperature sign (C)",
    "EXT_C": "External temperature value (C)",
    "EXT_F_S": "External temperature sign (F)",
    "EXT_F": "External temperature value (F)",
    "AH_H": "Amp-hour counter high word",
    "AH_L": "Amp-hour counter low word",
    "WH_H": "Watt-hour counter high word",
    "WH_L": "Watt-hour counter low word",
    "YEAR": "RTC year",
    "MONTH": "RTC month",
    "DAY": "RTC day",
    "HOUR": "RTC hour",
    "MINUTE": "RTC minute",
    "SECOND": "RTC second",
    "CAL_COMMIT": "Write 0x1501 to commit calibration",
    "V_OUT_ZERO": "Target voltage offset trim (DAC)",
    "V_OUT_SCALE": "Target voltage scale trim (DAC)",
    "V_BACK_ZERO": "Display voltage offset trim (ADC)",
    "V_BACK_SCALE": "Display voltage scale trim (ADC)",
    "I_OUT_ZERO": "Target current offset trim (DAC)",
    "I_OUT_SCALE": "Target current scale trim (DAC)",
    "I_BACK_ZERO": "Display current offset trim (ADC)",
    "I_BACK_SCALE": "Display current scale trim (ADC)",
    "OPT_TAKE_OK": "Option: take OK behavior",
    "OPT_TAKE_OUT": "Option: take output behavior",
    "OPT_BOOT_POW": "Option: boot power state",
    "OPT_BUZZ": "Option: buzzer",
    "OPT_LOGO": "Option: startup logo",
    "OPT_LANG": "Option: language",
    "OPT_LIGHT": "Option: display brightness",
    "SYSTEM": "System control; write 0x1601 to enter bootloader",
}

# Known/unknown/empirical coverage of the 0..256 address space.
RANGES: list[tuple[int, int, str, str]] = [
    (0, 20, "Known", "Core identity, measurements, output state"),
    (21, 31, "Unknown", "Reserved/undocumented"),
    (32, 41, "Known", "Battery/temp/energy counters"),
    (42, 47, "Unknown", "Reserved/undocumented"),
    (48, 53, "Known", "RTC date/time"),
    (54, 54, "Known", "Calibration commit register"),
    (55, 62, "Known", "Calibration trim registers"),
    (63, 65, "Unknown", "Reserved/undocumented"),
    (66, 72, "Known", "UI/options"),
    (73, 79, "Unknown", "Reserved/undocumented"),
    (80, 119, "Known", "Presets M0-M9"),
    (120, 181, "Unknown", "Reserved/undocumented"),
    (182, 195, "Empirical", "Metadata/calibration mirror cluster on RD6024 fw109"),
    (196, 207, "Unknown", "Reserved/undocumented"),
    (208, 239, "Empirical", "Extended presets M10-M17 (V/I/OVP/OCP)"),
    (240, 255, "Unknown", "Reserved/undocumented"),
    (256, 256, "Known", "System control (bootloader trigger)"),
]

# Raw -> engineering multipliers by model family.
MULTIPLIERS: list[tuple[str, int, int, int]] = [
    ("RD6006 / RK6006", 100, 1000, 100),
    ("RD6006P", 1000, 10000, 1000),
    ("RD6012 / RD6018 / RD6024", 100, 100, 100),
]

_PRESET_FIELD = {"V": "voltage setpoint", "I": "current setpoint",
                 "OVP": "OVP threshold", "OCP": "OCP threshold"}


def _preset_desc(name: str) -> str | None:
    # Names like M0_V, M10_OCP -> "Preset M0 voltage setpoint".
    if not name.startswith("M") or "_" not in name:
        return None
    bank, _, field = name.partition("_")
    if bank[1:].isdigit() and field in _PRESET_FIELD:
        return f"Preset {bank} {_PRESET_FIELD[field]}"
    return None


def registers() -> dict[int, dict]:
    """Canonical address -> {name, desc}, sorted by address."""
    out: dict[int, dict] = {}
    for name, val in vars(R).items():
        if name.startswith("_") or not isinstance(val, int):
            continue
        if val <= MAX_ADDR and not name.endswith("_MAGIC") and name != "BOOTLOADER":
            desc = DESCRIPTIONS.get(name) or _preset_desc(name) or ""
            out[val] = {"name": name, "desc": desc}
    return dict(sorted(out.items()))


def magic() -> dict[str, int]:
    """Command 'magic' values (written to a register to trigger an action)."""
    return {
        name: val for name, val in vars(R).items()
        if not name.startswith("_") and isinstance(val, int)
        and (val > MAX_ADDR or name.endswith("_MAGIC") or name == "BOOTLOADER")
    }


def render_markdown() -> str:
    """Render docs/registers.md entirely from this module. GENERATED — do not edit by hand."""
    regs = registers()
    lines: list[str] = []
    lines.append("# Riden RD60xx/RK60xx Register Map")
    lines.append("")
    lines.append("> **GENERATED FILE — do not edit by hand.**")
    lines.append("> Regenerate with: `scripts/awto-riden-dev.py gen-docs`")
    lines.append("> Single source of truth: `register_map.py` (built on `riden_register.py`).")
    lines.append("")
    lines.append("## Address-space coverage (0–256)")
    lines.append("")
    lines.append("| Range | Status | Notes |")
    lines.append("|---|---|---|")
    for lo, hi, status, note in RANGES:
        rng = f"{lo}" if lo == hi else f"{lo}–{hi}"
        lines.append(f"| {rng} | {status} | {note} |")
    lines.append("")
    lines.append("## Register details")
    lines.append("")
    lines.append("| Addr | Name | Description |")
    lines.append("|---|---|---|")
    for addr, info in regs.items():
        lines.append(f"| {addr} | {info['name']} | {info['desc']} |")
    lines.append("")
    lines.append("## Magic command values")
    lines.append("")
    lines.append("| Value | Name | Use |")
    lines.append("|---|---|---|")
    lines.append("| `0x1501` (5377) | CAL_COMMIT_MAGIC | Write to `CAL_COMMIT` (54) to persist calibration |")
    lines.append("| `0x1601` (5633) | REBOOT_MAGIC | Write to `SYSTEM` (256) to enter bootloader |")
    lines.append("")
    lines.append("## Raw → engineering multipliers")
    lines.append("")
    lines.append("| Model family | V | I | P |")
    lines.append("|---|--:|--:|--:|")
    for fam, v, i, pw in MULTIPLIERS:
        lines.append(f"| {fam} | {v} | {i} | {pw} |")
    lines.append("")
    lines.append("e.g. raw `V_OUT = 1200` × (1/100) = **12.00 V**.")
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    lines.append("- **RK6006 (id 60066, fw v1.09):** full read scan (0–260) shows the documented")
    lines.append("  map plus addrs **120–207 reading `0xFFFF` (unimplemented)** and **zero**")
    lines.append("  undocumented live registers. The RD6024 empirical banks (182–239) are NOT")
    lines.append("  present on RK firmware.")
    lines.append("- Sources: `riden_register.py`, Baldanos/rd6006, ShayBox/Riden,")
    lines.append("  rssdev10/tjko riden-flashtool (magic values). See `ATTRIBUTION.md`.")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_markdown())
