"""
MR11 LED/halogen bulb characterisation tests.

Test 1 — Inrush capture: output OFF → set VSET/ISET → turn ON → sample V+I
          as fast as Modbus allows for ~4 seconds. Captures CC→CV transition.

Test 2 — VI characteristic: ramp VSET 0 → 15 V in 0.25 V steps, dwell 300 ms
          at each step, record V_out + I_out. Produces the lamp's I-V curve.

Writes two JSONL files (one per test) then saves a two-panel PNG.

IMPORTANT: The MCP server (mcp_server.py) must NOT be running — it holds the
serial port exclusively.  Disable it in VS Code settings or reload the window
and run this script before Copilot Chat reconnects.  The script will exit with
a clear error if the port is busy.

Usage:
    python3 scripts/awto_riden_mr11.py [--port /dev/ttyUSB0] [--voltage 12]
                                     [--max-current 6] [--out-dir /tmp]
                                     [--skip-inrush] [--skip-vsweep]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port",        default="/dev/ttyUSB0")
    p.add_argument("--baud",        type=int, default=115200)
    p.add_argument("--address",     type=int, default=1)
    p.add_argument("--voltage",     type=float, default=12.0,
                   help="Nominal lamp voltage for inrush test (default 12 V)")
    p.add_argument("--max-current", type=float, default=6.0,
                   help="Current limit for both tests (default 6 A = RK6006 max)")
    p.add_argument("--vsweep-max",  type=float, default=15.0,
                   help="Max voltage for VI sweep (default 15 V)")
    p.add_argument("--vsweep-step", type=float, default=0.25,
                   help="Voltage step for VI sweep (default 0.25 V)")
    p.add_argument("--dwell-ms",    type=int, default=300,
                   help="Settle time per voltage step in sweep (default 300 ms)")
    p.add_argument("--inrush-s",    type=float, default=4.0,
                   help="Inrush capture duration in seconds (default 4)")
    p.add_argument("--out-dir",     default="/tmp",
                   help="Output directory for JSONL + PNG (default /tmp)")
    p.add_argument("--skip-inrush", action="store_true")
    p.add_argument("--skip-vsweep", action="store_true")
    return p.parse_args()

# ---------------------------------------------------------------------------
# Low-level Modbus helpers (modbus_tk — avoids riden.update() hang)
# ---------------------------------------------------------------------------

# RD6006 / RK6006 register map (Modbus FC03 read / FC06 write)
REG_MODEL   = 0    # model ID × 10
REG_VSET    = 8    # V setpoint × 100
REG_ISET    = 9    # I setpoint × 1000
REG_VOUT    = 10   # V output × 100
REG_IOUT    = 11   # I output × 1000
REG_POWER   = 12   # Power × 100
REG_VIN     = 14   # V input × 100
REG_OUTPUT  = 18   # 0=off, 1=on


def _connect(port: str, baud: int, address: int):
    """Open a modbus_tk RTU master. Returns (master, address)."""
    try:
        import modbus_tk.defines as cst
        import modbus_tk.modbus_rtu as modbus_rtu
        import serial
    except ImportError:
        sys.exit("ERROR: modbus_tk and pyserial are required. Install with:\n"
                 "  pip install modbus-tk pyserial")

    try:
        master = modbus_rtu.RtuMaster(
            serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=1.0,
            )
        )
        master.set_timeout(1.0)
        master.set_verbose(False)
    except serial.SerialException as e:
        if "busy" in str(e).lower() or "permission" in str(e).lower():
            sys.exit(
                f"ERROR: Cannot open {port} — {e}\n\n"
                "The MCP server (mcp_server.py) is probably holding the port.\n"
                "To free it: close VS Code Copilot Chat, or disable the\n"
                "  awto-riden server in .vscode/mcp.json, then re-run."
            )
        sys.exit(f"ERROR: Serial open failed: {e}")

    return master, address


def _read(master, address: int, reg: int, count: int = 1):
    import modbus_tk.defines as cst
    return master.execute(address, cst.READ_HOLDING_REGISTERS, reg, count)


def _write(master, address: int, reg: int, value: int):
    import modbus_tk.defines as cst
    master.execute(address, cst.WRITE_SINGLE_REGISTER, reg, 1, value)


def _output_off(master, address):
    _write(master, address, REG_OUTPUT, 0)


def _output_on(master, address):
    _write(master, address, REG_OUTPUT, 1)


def _set_vset(master, address, volts: float):
    _write(master, address, REG_VSET, round(volts * 100))


def _set_iset(master, address, amps: float):
    _write(master, address, REG_ISET, round(amps * 1000))


def _read_vi(master, address) -> tuple[float, float]:
    """Single FC03 call reading VOUT + IOUT (2 consecutive registers)."""
    raw = _read(master, address, REG_VOUT, 2)
    v = raw[0] / 100.0
    i = raw[1] / 1000.0
    return v, i

# ---------------------------------------------------------------------------
# Test 1 — Inrush capture
# ---------------------------------------------------------------------------

def test_inrush(
    master, address: int,
    voltage: float, max_current: float, duration_s: float,
    out_path: Path,
) -> list[dict]:
    print(f"\n{'='*60}")
    print(f"TEST 1 — Inrush capture: {voltage} V, {max_current} A limit")
    print(f"  capturing {duration_s:.1f} s → {out_path}")
    print(f"{'='*60}")

    _output_off(master, address)
    time.sleep(0.1)
    _set_iset(master, address, max_current)
    _set_vset(master, address, voltage)
    time.sleep(0.05)

    samples = []
    t_start = None

    with open(out_path, "w") as f:
        # Start logging tight loop, then turn on
        _output_on(master, address)
        t_start = time.monotonic()

        while (time.monotonic() - t_start) < duration_s:
            v, i = _read_vi(master, address)
            ts = time.time()
            t_rel = time.monotonic() - t_start
            row = {"ts": ts, "t": round(t_rel, 4), "v_out": v, "i_out": i}
            samples.append(row)
            f.write(json.dumps(row) + "\n")
            f.flush()
            # No sleep — run as fast as Modbus allows

    _output_off(master, address)
    dt = samples[-1]["t"] if samples else 0
    rate = len(samples) / dt if dt > 0 else 0
    print(f"  captured {len(samples)} samples in {dt:.2f} s  ({rate:.1f} Hz)")
    return samples

# ---------------------------------------------------------------------------
# Test 2 — VI characteristic sweep
# ---------------------------------------------------------------------------

def test_vsweep(
    master, address: int,
    max_current: float, vsweep_max: float, vsweep_step: float, dwell_ms: int,
    out_path: Path,
) -> list[dict]:
    import numpy as np

    print(f"\n{'='*60}")
    print(f"TEST 2 — VI sweep: 0 → {vsweep_max} V, step {vsweep_step} V, "
          f"dwell {dwell_ms} ms")
    print(f"  → {out_path}")
    print(f"{'='*60}")

    _output_off(master, address)
    time.sleep(0.1)
    _set_iset(master, address, max_current)
    _set_vset(master, address, 0.0)
    time.sleep(0.05)
    _output_on(master, address)

    voltages = list(np.arange(0.0, vsweep_max + vsweep_step / 2, vsweep_step))
    samples = []

    with open(out_path, "w") as f:
        for v_set in voltages:
            _set_vset(master, address, round(v_set, 2))
            time.sleep(dwell_ms / 1000.0)
            v_out, i_out = _read_vi(master, address)
            row = {
                "ts": time.time(),
                "v_set": round(v_set, 2),
                "v_out": v_out,
                "i_out": i_out,
                "p_out": round(v_out * i_out, 3),
            }
            samples.append(row)
            f.write(json.dumps(row) + "\n")
            f.flush()
            cc = i_out >= (max_current * 0.98)
            print(f"  VSET={v_set:5.2f} V  →  V={v_out:6.3f} V  I={i_out:6.3f} A"
                  f"  P={row['p_out']:6.2f} W{'  [CC]' if cc else ''}")

    _output_off(master, address)
    print(f"  {len(samples)} points recorded")
    return samples

# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _plot(inrush_path: Path | None, vsweep_path: Path | None, out_png: Path):
    try:
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
        import numpy as np
    except ImportError:
        print("WARNING: matplotlib not installed — skipping plot. Install with:\n"
              "  pip install matplotlib")
        return

    n_panels = sum(p is not None for p in [inrush_path, vsweep_path])
    if n_panels == 0:
        return

    fig = plt.figure(figsize=(12, 5 * n_panels), tight_layout=True)
    fig.suptitle("MR11 LED Bulb Characterisation", fontsize=14, fontweight="bold")
    gs = gridspec.GridSpec(n_panels, 1, figure=fig, hspace=0.4)
    panel = 0

    # --- Inrush panel ---
    if inrush_path and inrush_path.exists():
        rows = [json.loads(l) for l in inrush_path.read_text().splitlines() if l.strip()]
        t  = np.array([r["t"]     for r in rows])
        v  = np.array([r["v_out"] for r in rows])
        i  = np.array([r["i_out"] for r in rows])

        ax1 = fig.add_subplot(gs[panel])
        ax1.set_title("Start-up Inrush — Current & Voltage vs Time", pad=8)
        ax1.set_xlabel("Time (s)")
        ax1.set_ylabel("Current (A)", color="#d62728")
        ax1.tick_params(axis="y", labelcolor="#d62728")
        ax1.plot(t, i, color="#d62728", linewidth=1.2, label="I_out (A)")
        ax1.set_ylim(bottom=0)

        ax1b = ax1.twinx()
        ax1b.set_ylabel("Voltage (V)", color="#1f77b4")
        ax1b.tick_params(axis="y", labelcolor="#1f77b4")
        ax1b.plot(t, v, color="#1f77b4", linewidth=1.0, alpha=0.7, label="V_out (V)")
        ax1b.set_ylim(bottom=0)

        # Annotate peak current
        i_peak = i.max()
        t_peak = t[i.argmax()]
        ax1.annotate(
            f"peak {i_peak:.3f} A @ {t_peak*1000:.0f} ms",
            xy=(t_peak, i_peak),
            xytext=(t_peak + 0.1, i_peak * 0.92),
            arrowprops=dict(arrowstyle="->", color="black"),
            fontsize=9,
        )

        # Combined legend
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax1b.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8)
        panel += 1

    # --- VI sweep panel ---
    if vsweep_path and vsweep_path.exists():
        rows = [json.loads(l) for l in vsweep_path.read_text().splitlines() if l.strip()]
        v  = np.array([r["v_out"] for r in rows])
        i  = np.array([r["i_out"] for r in rows])
        p  = v * i

        ax2 = fig.add_subplot(gs[panel])
        ax2.set_title("VI Characteristic (0 → max V sweep)", pad=8)
        ax2.set_xlabel("Output Voltage (V)")
        ax2.set_ylabel("Current (A)", color="#d62728")
        ax2.tick_params(axis="y", labelcolor="#d62728")
        ax2.plot(v, i, "o-", color="#d62728", markersize=3, linewidth=1.2, label="I (A)")
        ax2.set_ylim(bottom=0)

        ax2b = ax2.twinx()
        ax2b.set_ylabel("Power (W)", color="#2ca02c")
        ax2b.tick_params(axis="y", labelcolor="#2ca02c")
        ax2b.plot(v, p, "s--", color="#2ca02c", markersize=3, linewidth=1.0, alpha=0.8,
                  label="P (W)")
        ax2b.set_ylim(bottom=0)

        lines1, labels1 = ax2.get_legend_handles_labels()
        lines2, labels2 = ax2b.get_legend_handles_labels()
        ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8)
        panel += 1

    plt.savefig(out_png, dpi=150, bbox_inches="tight")
    print(f"\nPlot saved → {out_png}")
    try:
        import subprocess
        subprocess.Popen(["xdg-open", str(out_png)])
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = _parse()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    inrush_path  = out_dir / "mr11_inrush.jsonl"
    vsweep_path  = out_dir / "mr11_vsweep.jsonl"
    png_path     = out_dir / "mr11_led_characterisation.png"

    print(f"MR11 LED test  port={args.port}  baud={args.baud}  addr={args.address}")
    print(f"  VSET={args.voltage} V  ISET_max={args.max_current} A  "
          f"V-sweep 0→{args.vsweep_max} V step={args.vsweep_step} V")

    master, address = _connect(args.port, args.baud, args.address)

    try:
        if not args.skip_inrush:
            test_inrush(
                master, address,
                voltage=args.voltage,
                max_current=args.max_current,
                duration_s=args.inrush_s,
                out_path=inrush_path,
            )
            time.sleep(1.0)  # let bulb cool slightly between tests

        if not args.skip_vsweep:
            test_vsweep(
                master, address,
                max_current=args.max_current,
                vsweep_max=args.vsweep_max,
                vsweep_step=args.vsweep_step,
                dwell_ms=args.dwell_ms,
                out_path=vsweep_path,
            )
    finally:
        _output_off(master, address)
        print("\nOutput OFF — done.")

    _plot(
        inrush_path  if not args.skip_inrush  else None,
        vsweep_path  if not args.skip_vsweep  else None,
        png_path,
    )


if __name__ == "__main__":
    main()
