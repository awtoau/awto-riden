# Regenerating generated artifacts (docs, figures, register map)

**TL;DR — one command:**

```bash
scripts/awto-riden-dev.py gen-docs
```

That rebuilds every generated artifact from its source. **Never hand-edit a
generated file** — edit its source and re-run `gen-docs`. This page is the whole
story so you never have to re-figure it out.

---

## What is generated, and from what

| Generated artifact | Source of truth | Rebuilt by |
|---|---|---|
| `docs/registers.md` | **`register_map.py`** (names from `riden_register.py` + descriptions/ranges/magic) | `awto-riden-dev.py gen-docs` |
| `docs/data/*.png` (waveform/inrush/characterisation figures) | **`docs/data/*.jsonl`** capture fixtures | `awto-riden-dev.py gen-docs` → `regen_waveforms.py` |
| Timing figures (`*timing*`, `*rtt*`) | `docs/data/*timing*.json` | `awto-riden-dev.py plot ...` / `timing_test_set.py --analyze-only` |

The figure regeneration is **offline** — it reads the committed `.jsonl`/`.json`
data and re-renders the PNGs. **No PSU, no load, no hardware required.** (Verified:
`gen-docs` rebuilds 10/10 waveform PNGs from fixtures alone.)

## The register map: single source of truth

- Edit **`register_map.py`** (the one place) — add a register name/description,
  adjust a range, etc.
- Run `gen-docs`. `docs/registers.md` is regenerated and carries a
  *"GENERATED — do not edit by hand"* header.
- The same in-code map drives `awto-riden-dev.py analyze-scan` (classifying a
  register scan as known / unimplemented `0xFFFF` / RE-target), so there is no
  second copy to keep in sync.

## The capture → fixture → regenerate loop (for figures)

Documentation figures need an **appropriate load** to *capture* — a resistor is
linear and dull; the interesting transients come from reactive/non-linear loads
(an incandescent globe's inrush, an MR11 halogen, a motor). But once captured,
the recorded values live in a committed `.jsonl` fixture, and **anyone can
regenerate the figure without the load**.

1. **Capture** against real hardware with the load connected, e.g.:
   - `scripts/waveform_capture.py` — sine/triangle/sawtooth/square + clipping runs
   - `scripts/led_mr11_test.py` — MR11 inrush + V/I characteristic
   - `scripts/ble_globe_turnon.py` — globe turn-on over BLE
   Each writes a `.jsonl` of timestamped V/I samples into `docs/data/`.
2. **Commit the `.jsonl`** — that file *is* the recorded dataset.
3. **`gen-docs`** re-renders the PNG from it. Done — no hardware needed again.

To add a new graph: capture → commit the `.jsonl` → `gen-docs`.

## Committed capture fixtures (current)

| Fixture (`docs/data/`) | Load / signal |
|---|---|
| `globe_inrush.jsonl`, `globe12_inrush_20260503.jsonl` | 12 V incandescent globe — cold-filament inrush |
| `globe_vsweep.jsonl`, `globe12_vsweep_20260503.jsonl` | globe V/I characteristic sweep |
| `globe_turnon_14v.jsonl`, `globe_turnon_14v_ble80ms.jsonl` | globe turn-on (USB vs BLE transport) |
| `mr11_inrush.jsonl` | MR11 halogen bulb inrush |
| `sine_wave_high_density.jsonl` | high-density arbitrary-waveform capture |
| `connected_load_timing_matrix_*.json` | Modbus RTT-vs-cadence under load |

## Bench tooling (where these live)

- **`scripts/awto-riden-test.py`** — hardware test/characterization runner (drives
  the PSU; safe-state teardown between stages). Use this to *capture*/characterize.
- **`scripts/awto-riden-dev.py`** — analysis + docs (no PSU output). `gen-docs`,
  `analyze-scan`, `export-map`, `plot`, `ble`, `report`.

---

**Rule of thumb:** if a file is in the "Generated" column above, don't edit it —
edit its source and run `gen-docs`. If you captured new data, commit the `.jsonl`
and run `gen-docs`.
