#!/usr/bin/env python3
"""Multi-panel BLE vs USB globe turn-on comparison + BLE latency analysis.

Usage:
    python3 scripts/awto_riden_plot_ble.py [--out docs/data/globe_ble_comparison.png]

Panels:
  1. V_out & I_out — BLE (cold, ~113 ms) vs USB-serial (warm/threshold-filtered)
  2. BLE inter-sample Δt timeline during globe capture
  3. RTT histogram — idle BLE profile vs globe-capture RTT, USB reference
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

DEFAULT_BLE_GLOBE  = Path("docs/data/globe_turnon_14v_ble80ms.jsonl")
DEFAULT_USB_GLOBE  = Path("docs/data/globe_turnon_14v.jsonl")
DEFAULT_PROFILE    = Path("docs/data/ble_profile_30s.json")
DEFAULT_OUT        = Path("docs/data/globe_ble_comparison.png")


# ── helpers ──────────────────────────────────────────────────────────────────

def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def time_zero(rows: list[dict], key: str = "ts") -> list[float]:
    """Return timestamps relative to first row."""
    t0 = rows[0][key]
    return [r[key] - t0 for r in rows]


def output_enable_time(rows: list[dict], times: list[float]) -> float:
    """Time of first row where output is True."""
    for t, r in zip(times, rows):
        if r.get("output"):
            return t
    return 0.0


def deltas(times: list[float]) -> list[float]:
    return [b - a for a, b in zip(times, times[1:])]


# ── panel 1 — V/I waveform comparison ────────────────────────────────────────

def plot_vi_comparison(ax_v, ax_i, ble_rows, usb_rows):
    # BLE — zero relative to output-enable
    ble_t = time_zero(ble_rows)
    ble_t0 = output_enable_time(ble_rows, ble_t)
    ble_t = [t - ble_t0 for t in ble_t]

    # USB — zero relative to output-enable
    usb_t = time_zero(usb_rows)
    usb_t0 = output_enable_time(usb_rows, usb_t)
    usb_t = [t - usb_t0 for t in usb_t]

    # --- shade CC regions ---
    def shade_cc(rows, times, ax, color):
        in_cc = False
        seg_start = None
        for i, (r, t) in enumerate(zip(rows, times)):
            is_cc = r.get("cv_cc") == "CC"
            if is_cc and not in_cc:
                seg_start = t
                in_cc = True
            elif not is_cc and in_cc:
                ax.axvspan(seg_start, t, alpha=0.12, color=color, zorder=0)
                in_cc = False
        if in_cc:
            ax.axvspan(seg_start, times[-1], alpha=0.12, color=color, zorder=0)

    shade_cc(ble_rows, ble_t, ax_v, "orange")
    shade_cc(usb_rows, usb_t, ax_v, "gold")

    ble_v = [r["v_out"] for r in ble_rows]
    ble_i = [r["i_out"] for r in ble_rows]
    usb_v = [r["v_out"] for r in usb_rows]
    usb_i = [r["i_out"] for r in usb_rows]

    # V traces
    l1, = ax_v.plot(ble_t, ble_v, "-o", ms=3, color="#1a6faf", lw=1.5,
                    label="BLE V_out (cold, ~113 ms/poll)")
    l2, = ax_v.plot(usb_t, usb_v, "--s", ms=4, color="#1a6faf", lw=1.5,
                    alpha=0.6, label="USB V_out (warm, threshold-filtered)")

    # I traces
    l3, = ax_i.plot(ble_t, ble_i, "-o", ms=3, color="#c0392b", lw=1.5,
                    label="BLE I_out")
    l4, = ax_i.plot(usb_t, usb_i, "--s", ms=4, color="#c0392b", lw=1.5,
                    alpha=0.6, label="USB I_out")

    ax_v.set_ylabel("V_out (V)", color="#1a6faf")
    ax_v.tick_params(axis="y", labelcolor="#1a6faf")
    ax_v.set_ylim(bottom=-0.3)
    ax_v.set_xlabel("Time from output-enable (s)")

    ax_i.set_ylabel("I_out (A)", color="#c0392b")
    ax_i.tick_params(axis="y", labelcolor="#c0392b")
    ax_i.set_ylim(bottom=-0.3)

    cc_patch = mpatches.Patch(color="orange", alpha=0.3, label="CC region (BLE=orange, USB=gold)")
    handles = [l1, l2, l3, l4, cc_patch]
    ax_v.legend(handles=handles, fontsize=7, loc="center right")
    ax_v.set_title("Panel 1 — Globe turn-on: V_out & I_out  (BLE cold vs USB warm+threshold-filtered)",
                   fontsize=9, fontweight="bold")
    ax_v.grid(True, alpha=0.3)


# ── panel 2 — BLE inter-sample Δt timeline ───────────────────────────────────

def plot_delta_timeline(ax, ble_rows):
    times = time_zero(ble_rows)
    t0 = output_enable_time(ble_rows, times)
    t_rel = [t - t0 for t in times]

    rtt = [r["rtt_ms"] for r in ble_rows]
    dt = [0.0] + [b - a for a, b in zip(t_rel, t_rel[1:])]  # first sample has no prior
    dt_s = [v * 1000 for v in dt[1:]]                        # convert to ms; skip first
    t_mid = [(a + b) / 2 for a, b in zip(t_rel, t_rel[1:])] # midpoint between samples

    # scatter: actual Δt between consecutive samples
    ax.scatter(t_mid, dt_s, s=25, color="#2ca02c", zorder=3, label="Actual Δt between samples (ms)")

    # rtt per sample (line)
    ax2 = ax.twinx()
    ax2.plot(t_rel, rtt, "-", color="#9467bd", lw=1.2, alpha=0.8, label="RTT per poll (ms)")
    ax2.set_ylabel("RTT (ms)", color="#9467bd")
    ax2.tick_params(axis="y", labelcolor="#9467bd")

    # reference lines
    ax.axhline(80,  color="#ff7f0e", ls="--", lw=1, alpha=0.8, label="Target 80 ms")
    ax.axhline(120, color="#2ca02c", ls=":",  lw=1, alpha=0.8, label="BLE connection interval ~120 ms")
    ax.axhline(160, color="#d62728", ls="--", lw=1, alpha=0.5, label="USB recommended_poll 160 ms")

    ax.set_xlabel("Time from output-enable (s)")
    ax.set_ylabel("Inter-sample Δt (ms)", color="#2ca02c")
    ax.tick_params(axis="y", labelcolor="#2ca02c")
    ax.set_ylim(0, 200)

    # combine legends
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper right")
    ax.set_title("Panel 2 — BLE inter-sample Δt & per-poll RTT during globe capture",
                 fontsize=9, fontweight="bold")
    ax.grid(True, alpha=0.3)


# ── panel 3 — RTT histogram ───────────────────────────────────────────────────

def plot_rtt_histogram(ax, profile_json: Path, ble_rows):
    with profile_json.open() as f:
        prof = json.load(f)

    idle_rtt = prof.get("raw_rtt_ms", [])
    globe_rtt = [r["rtt_ms"] for r in ble_rows]

    bins = np.arange(0, 220, 10)

    ax.hist(idle_rtt,  bins=bins, alpha=0.6, color="#1a6faf", label=f"BLE idle profile (n={len(idle_rtt)})")
    ax.hist(globe_rtt, bins=bins, alpha=0.6, color="#c0392b", label=f"BLE globe capture (n={len(globe_rtt)})")

    # median lines
    idle_med  = float(np.median(idle_rtt))
    globe_med = float(np.median(globe_rtt))
    ax.axvline(idle_med,  color="#1a6faf", ls="--", lw=1.5, label=f"Idle median {idle_med:.0f} ms")
    ax.axvline(globe_med, color="#c0392b", ls="--", lw=1.5, label=f"Globe median {globe_med:.0f} ms")

    # USB reference
    ax.axvline(137.3, color="#555", ls=":", lw=1.5, label="USB-serial median 137 ms")

    ax.set_xlabel("RTT (ms)")
    ax.set_ylabel("Count")
    ax.set_xlim(0, 210)
    ax.legend(fontsize=7)
    ax.set_title("Panel 3 — RTT histogram: BLE idle profile vs globe capture (USB reference shown)",
                 fontsize=9, fontweight="bold")
    ax.grid(True, alpha=0.3)

    # annotate jitter
    p95_idle  = float(np.percentile(idle_rtt,  95))
    p95_globe = float(np.percentile(globe_rtt, 95))
    jitter_idle  = p95_idle  - idle_med
    jitter_globe = p95_globe - globe_med
    txt = (f"Idle   p95={p95_idle:.0f} ms  jitter={jitter_idle:.0f} ms\n"
           f"Globe  p95={p95_globe:.0f} ms  jitter={jitter_globe:.0f} ms")
    ax.text(0.98, 0.97, txt, transform=ax.transAxes, fontsize=7,
            ha="right", va="top", family="monospace",
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#ccc", alpha=0.9))


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ble-globe",  type=Path, default=DEFAULT_BLE_GLOBE)
    p.add_argument("--usb-globe",  type=Path, default=DEFAULT_USB_GLOBE)
    p.add_argument("--profile",    type=Path, default=DEFAULT_PROFILE)
    p.add_argument("--out",        type=Path, default=DEFAULT_OUT)
    args = p.parse_args()

    ble_rows = load_jsonl(args.ble_globe)
    usb_rows = load_jsonl(args.usb_globe)

    fig = plt.figure(figsize=(14, 12))
    fig.suptitle("RK6006 — BLE vs USB globe turn-on: waveform & latency analysis",
                 fontsize=12, fontweight="bold", y=0.995)

    # Panel 1: V/I — uses twin-axis trick, so we build two axes manually
    ax1v = fig.add_subplot(3, 1, 1)
    ax1i = ax1v.twinx()
    plot_vi_comparison(ax1v, ax1i, ble_rows, usb_rows)

    ax2 = fig.add_subplot(3, 1, 2)
    plot_delta_timeline(ax2, ble_rows)

    ax3 = fig.add_subplot(3, 1, 3)
    plot_rtt_histogram(ax3, args.profile, ble_rows)

    fig.tight_layout(rect=[0, 0, 1, 0.995])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=150)
    print(f"Saved → {args.out}")


if __name__ == "__main__":
    main()
