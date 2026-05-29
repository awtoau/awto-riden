# Riden register map — cross-source validation

Confirms awto-riden's Modbus register map (`riden_register.py`) against
independent implementations. **Result: validated — zero address conflicts.**

## Sources

| Source | Yielded a map? | Notes |
|---|---|---|
| **awto-riden** `riden_register.py` | yes (reference) | derived from Baldanos + ShayBox + riden-flashtool + live RD6024 scan |
| **sigrok** `rdtech-dps/protocol.c` | yes | `enum rdtech_rd_register` |
| **Baldanos/rd6006** `registers.md` | yes | the canonical community source |
| **nsbawden/RIDEN_MPPT** | yes (subset) | core regs as `REG_*` constants |
| **MathiasMoog/Rd6006ModbusTcp** | no | transparent TCP↔RTU bridge; defines no registers (defers to Baldanos) |
| **Foujiwara/Riden-RD6018-RE** | no | hardware reverse-eng (KiCad); register notes live in an external Google Sheet (not fetchable) |

4 of 6 sources carry register data; the two that don't aren't register maps by
nature (a bridge and a hardware project).

## Core registers (all AGREE)

| Register | awto-riden | sigrok | Baldanos | nsbawden |
|---|---|---|---|---|
| model id | 0 | 0 | 0 | — |
| serial (hi/lo) | 1, 2 | 1 (u32) | 1, 2 | — |
| firmware | 3 | 3 | 3 | — |
| int temp (sign/val) | 4, 5 | 4 | 4, 5 | — |
| int temp °F | 6, 7 | 6 | 6, 7 | — |
| V_set | 8 | 8 | 8 | 8 |
| I_set | 9 | 9 | 9 | 9 |
| V_out | 10 | 10 | 10 | 10 |
| I_out | 11 | 11 | 11 | 11 |
| energy/Ah | 12 | 12 | 12 | — |
| power | 13 | 13 | 13 | 13 |
| V_in | 14 | 14 | 14 | 14 |
| keypad lock | 15 | (—) | 15 | — |
| protect (OVP/OCP) | 16 | 16 | 16 | — |
| CV/CC | 17 | 17 | 17 | 17 |
| output enable | 18 | 18 | 18 | 18 |
| preset | 19 | 19 | 19 | — |
| **range / I_range** | **20** | **20** | **20** | — |
| OVP threshold | 82 | 82 | 82 | — |
| OCP threshold | 83 | 83 | 83 | — |
| preset mem start | 84 (M0 live=80) | 84 | 80–119 | — |

## Findings

1. **No address discrepancies** anywhere — every register present in >1 source
   agrees.
2. **awto-riden matches sigrok and the majority (Baldanos) fully** on the core
   set, including register 20 (`I_RANGE`), where awto-riden and sigrok agree on
   both address *and* the "device-specific, RD6012P only" semantics. Baldanos
   lists 20 as undocumented, so awto-riden carries *more* info, not conflicting
   info.
3. **awto-riden extras (single-sourced, not contradicted):** battery/ext-temp/
   energy block (32–41), date-time (48–53), calibration (`CAL_COMMIT=54` +
   magic, from riden-flashtool), `SYSTEM=256`/`REBOOT_MAGIC`, and the empirical
   M10–M17 (208–239) / 182–195 clusters discovered via a live RD6024 fw1.39
   scan. All correctly annotated as empirically derived. sigrok comments these
   regions ("Battery at 32", "Date/time at 48") without naming them.
4. **awto-riden lacks nothing** the others have — it is a superset.

**Bottom line:** awto-riden's register map is correct and the most complete of
the sources checked.
