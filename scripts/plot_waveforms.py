#!/usr/bin/env python3
"""Generate waveform documentation graphs from captured JSONL files."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def load(path):
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _period_plot(ax, rows, label):
    if not rows:
        ax.set_title(f"{label} (no data)")
        return
    # Use explicit captured phase if available; fallback to normalized elapsed.
    if "phase" in rows[0]:
        phase = np.array([r["phase"] for r in rows])
    else:
        t = np.array([r["ts"] for r in rows])
        dt = t - t.min()
        duration = dt.max() if dt.max() > 0 else 1.0
        phase = (dt / duration) % 1.0

    order = np.argsort(phase)
    x = phase[order]
    v_set = np.array([r.get("v_set", 0.0) for r in rows])[order]
    v_out = np.array([r.get("v_out", 0.0) for r in rows])[order]
    i_out = np.array([r.get("i_out", 0.0) for r in rows])[order]

    # Build smooth traces from sparse phase samples while keeping raw points visible.
    xu, idx = np.unique(x, return_index=True)
    vsu = v_set[idx]
    vou = v_out[idx]
    iou = i_out[idx]
    if len(xu) >= 2:
        x_dense = np.linspace(0.0, 1.0, 300)
        vs_dense = np.interp(x_dense, xu, vsu)
        vo_dense = np.interp(x_dense, xu, vou)
        io_dense = np.interp(x_dense, xu, iou)
    else:
        x_dense = x
        vs_dense = v_set
        vo_dense = v_out
        io_dense = i_out

    ax.plot(x_dense, vs_dense, "--", linewidth=1.8, alpha=0.75, color="#4C90C0", label="V_set")
    ax.plot(x_dense, vo_dense, "-", linewidth=2.2, color="#D06700", label="V_out")
    ax.scatter(x, v_out, s=12, color="#D06700", alpha=0.6, zorder=3, label="V_out samples")
    ax.set_xlim(0, 1)
    ax.set_xlabel("Phase (0..1, one period)")
    ax.set_ylabel("Voltage (V)")
    ax.set_title(label)
    ax.grid(True, alpha=0.28)

    v_min = min(float(np.min(v_set)), float(np.min(v_out)))
    v_max = max(float(np.max(v_set)), float(np.max(v_out)))
    pad = max(0.25, 0.08 * (v_max - v_min))
    ax.set_ylim(v_min - pad, v_max + pad)

    ax2 = ax.twinx()
    ax2.plot(x_dense, io_dense, "-", linewidth=1.8, alpha=0.9, color="#CC2F2F", label="I_out")
    ax2.scatter(x, i_out, s=10, color="#CC2F2F", alpha=0.5, zorder=3, label="I_out samples")
    ax2.set_ylabel("Current (A)", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")

    i_min = float(np.min(i_out))
    i_max = float(np.max(i_out))
    i_pad = max(0.02, 0.12 * (i_max - i_min if i_max > i_min else 0.1))
    ax2.set_ylim(i_min - i_pad, i_max + i_pad)

    overshoot = float(np.max(v_out - v_set)) if len(v_out) else 0.0
    ax.text(0.02, 0.92, f"max overshoot: {overshoot:.3f} V", transform=ax.transAxes, fontsize=8)

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8)


def _clip_plot(ax, rows):
    if not rows:
        ax.set_title("Current-limited sine clipping (no data)")
        return
    t0 = rows[0]["ts"]
    t = np.array([r["ts"] - t0 for r in rows])
    v_set = np.array([r.get("v_set", 0.0) for r in rows])
    v_out = np.array([r.get("v_out", 0.0) for r in rows])
    i_out = np.array([r.get("i_out", 0.0) for r in rows])
    cv = np.array([r.get("cv_cc", "CV") for r in rows])
    prot = np.array([r.get("protect", "none") for r in rows])

    ax.plot(t, v_set, "--", linewidth=1.8, alpha=0.75, color="#4C90C0", label="V_set")
    ax.plot(t, v_out, "-", linewidth=2.2, color="#D06700", label="V_out")
    ax.scatter(t, v_out, s=12, color="#D06700", alpha=0.6, zorder=3, label="V_out samples")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Voltage (V)")
    ax.grid(True, alpha=0.28)
    ax.set_title("Sine under current limiting (clipping / CC transitions)")

    v_min = min(float(np.min(v_set)), float(np.min(v_out)))
    v_max = max(float(np.max(v_set)), float(np.max(v_out)))
    pad = max(0.25, 0.08 * (v_max - v_min))
    ax.set_ylim(v_min - pad, v_max + pad)

    ax2 = ax.twinx()
    ax2.plot(t, i_out, "-", linewidth=1.8, alpha=0.9, color="#CC2F2F", label="I_out")
    ax2.scatter(t, i_out, s=10, color="#CC2F2F", alpha=0.5, zorder=3, label="I_out samples")
    ax2.set_ylabel("Current (A)", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")

    i_min = float(np.min(i_out))
    i_max = float(np.max(i_out))
    i_pad = max(0.01, 0.12 * (i_max - i_min if i_max > i_min else 0.05))
    ax2.set_ylim(i_min - i_pad, i_max + i_pad)

    in_cc = cv == "CC"
    if np.any(in_cc):
        start = None
        for idx, is_cc in enumerate(in_cc):
            if is_cc and start is None:
                start = t[idx]
            if (not is_cc) and start is not None:
                ax.axvspan(start, t[idx], color="orange", alpha=0.15)
                start = None
        if start is not None:
            ax.axvspan(start, t[-1], color="orange", alpha=0.15)

    oc_idx = np.where(prot != "none")[0]
    if len(oc_idx) > 0:
        ax.scatter(t[oc_idx], v_out[oc_idx], color="black", s=14, label="protect != none")
    ax.text(
        0.02,
        0.92,
        f"CC samples: {int(np.sum(in_cc))} / {len(rows)} | protect events: {len(oc_idx)}",
        transform=ax.transAxes,
        fontsize=8,
    )

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8)


def main() -> int:
    in_dir = Path("docs")
    period_png = in_dir / "mr11_waveform_tracking_one_period_view_same_settings_overshoot_visible.png"
    clip_png = in_dir / "mr11_sine_under_current_limiting_clipping_cc_transitions.png"

    period_sets = [
        (in_dir / "mr11_sine_period.jsonl", "Sine (period-wide)"),
        (in_dir / "mr11_sawtooth_period.jsonl", "Sawtooth (period-wide)"),
        (in_dir / "mr11_triangle_period.jsonl", "Triangle (period-wide)"),
        (in_dir / "mr11_square_period.jsonl", "Square on/off (period-wide)"),
    ]

    fig, axes = plt.subplots(4, 1, figsize=(13, 14), tight_layout=True)
    fig.suptitle(
        "MR11 waveform tracking — one-period view (same settings, overshoot visible)",
        fontsize=12,
        fontweight="bold",
    )
    for ax, (path, label) in zip(axes, period_sets):
        _period_plot(ax, load(path), label)
    plt.savefig(period_png, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {period_png}")

    clip_rows = load(in_dir / "mr11_sine_clipped_current_limit.jsonl")
    fig, ax = plt.subplots(1, 1, figsize=(13, 5.6), tight_layout=True)
    _clip_plot(ax, clip_rows)
    plt.savefig(clip_png, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {clip_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
