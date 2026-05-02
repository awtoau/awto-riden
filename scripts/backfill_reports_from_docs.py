#!/usr/bin/env python3
"""Backfill per-run report pages from existing docs artifacts.

Creates report pages + manifests in docs/reports and rebuilds docs/reports/index.md.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path

from report_pages import normalize_device_slug, update_reports_index, write_manifest


def _safe_stamp(value: str, fallback: str) -> str:
    if not value:
        return fallback
    txt = value.strip().replace(":", "").replace("-", "")
    txt = txt.replace("T", "-").replace("Z", "")
    txt = re.sub(r"[^0-9]", "", txt)
    if len(txt) >= 14:
        return f"{txt[:8]}-{txt[8:14]}Z"
    return fallback


def _timing_report_from_json(
    *,
    reports_root: Path,
    docs_dir: Path,
    json_path: Path,
    device_meta: dict,
) -> Path:
    data = json.loads(json_path.read_text())
    captured_on = str(data.get("captured_on", ""))
    run_stamp = _safe_stamp(captured_on, time.strftime("%Y%m%d-%H%M%SZ", time.gmtime()))

    device_slug = normalize_device_slug(device_meta)
    run_dir = reports_root / device_slug / "timing_matrix" / f"{run_stamp}-{json_path.stem}"
    run_dir.mkdir(parents=True, exist_ok=True)

    src_rtt = json_path.with_suffix(".rtt.png")
    src_timeout = json_path.with_suffix(".timeout.png")
    dst_json = run_dir / "timing_matrix.json"
    dst_rtt = run_dir / "timing_matrix.rtt.png"
    dst_timeout = run_dir / "timing_matrix.timeout.png"

    shutil.copy2(json_path, dst_json)
    if src_rtt.exists():
        shutil.copy2(src_rtt, dst_rtt)
    if src_timeout.exists():
        shutil.copy2(src_timeout, dst_timeout)

    report_path = run_dir / "report.md"
    lines: list[str] = []
    lines.append("# Connected Load Timing Matrix Report")
    lines.append("")
    lines.append(f"Source: {json_path.relative_to(docs_dir.parent)}")
    lines.append("")
    lines.append("## Device")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Model | {device_meta.get('type')} |")
    lines.append(f"| Device ID | {device_meta.get('id')} |")
    lines.append(f"| Firmware | {device_meta.get('fw')} |")
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    lines.append(f"- [timing_matrix.json]({dst_json.name})")
    if dst_rtt.exists():
        lines.append(f"- [timing_matrix.rtt.png]({dst_rtt.name})")
    if dst_timeout.exists():
        lines.append(f"- [timing_matrix.timeout.png]({dst_timeout.name})")
    if dst_rtt.exists():
        lines.append("")
        lines.append("![RTT chart](timing_matrix.rtt.png)")
    if dst_timeout.exists():
        lines.append("")
        lines.append("![Timeout chart](timing_matrix.timeout.png)")
    report_path.write_text("\n".join(lines) + "\n")

    artifacts = [report_path, dst_json]
    if dst_rtt.exists():
        artifacts.append(dst_rtt)
    if dst_timeout.exists():
        artifacts.append(dst_timeout)

    write_manifest(
        run_dir=run_dir,
        reports_root=reports_root,
        report_kind="timing_matrix",
        report_title=f"Timing Matrix {json_path.stem}",
        device_meta=device_meta,
        report_path=report_path,
        artifacts=artifacts,
        extra={"source_json": str(json_path.relative_to(docs_dir.parent))},
    )
    return report_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs-dir", default="docs/data")
    ap.add_argument("--reports-root", default="docs/reports")
    ap.add_argument("--model", default="RK6006")
    ap.add_argument("--device-id", default="unknown")
    ap.add_argument("--fw", default="unknown")
    ap.add_argument("--sn", default="unknown")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    docs_dir = (root / args.docs_dir).resolve()
    reports_root = (root / args.reports_root).resolve()

    device_meta = {
        "type": args.model,
        "id": args.device_id,
        "fw": args.fw,
        "sn": args.sn,
    }

    timing_json = sorted(
        [
            p
            for p in docs_dir.glob("*.json")
            if "results" in json.loads(p.read_text()) and "poll_points_ms" in json.loads(p.read_text())
        ]
    )

    written: list[Path] = []
    for p in timing_json:
        written.append(_timing_report_from_json(reports_root=reports_root, docs_dir=docs_dir, json_path=p, device_meta=device_meta))

    index_path = update_reports_index(reports_root)
    for p in written:
        print(f"WROTE {p}")
    print(f"WROTE {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
