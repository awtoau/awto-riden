#!/usr/bin/env python3
"""Helpers for per-device run reports and a global reports index."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any


def _slug_token(value: Any) -> str:
    text = str(value if value is not None else "unknown").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "unknown"


def normalize_device_slug(device_meta: dict[str, Any]) -> str:
    model = _slug_token(device_meta.get("type") or device_meta.get("model") or "unknown")
    fw = _slug_token(device_meta.get("fw") or device_meta.get("firmware") or "unknown")
    device_id = _slug_token(device_meta.get("id") or "unknown")
    return f"{model}-id{device_id}-fw{fw}"


def utc_run_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())


def copy_artifact(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def _rel_to(path: Path, base_dir: Path) -> str:
    return str(path.resolve().relative_to(base_dir.resolve()))


def write_manifest(
    *,
    run_dir: Path,
    reports_root: Path,
    report_kind: str,
    report_title: str,
    device_meta: dict[str, Any],
    report_path: Path,
    artifacts: list[Path],
    extra: dict[str, Any] | None = None,
) -> Path:
    payload: dict[str, Any] = {
        "report_kind": report_kind,
        "report_title": report_title,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_dir": _rel_to(run_dir, reports_root),
        "report_path": _rel_to(report_path, reports_root),
        "device": device_meta,
        "artifacts": [_rel_to(p, reports_root) for p in artifacts],
    }
    if extra:
        payload.update(extra)
    out = run_dir / "manifest.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    return out


def update_reports_index(reports_root: Path) -> Path:
    reports_root.mkdir(parents=True, exist_ok=True)
    manifests = sorted(reports_root.glob("**/manifest.json"))

    rows: list[dict[str, Any]] = []
    for manifest in manifests:
        try:
            payload = json.loads(manifest.read_text())
        except Exception:
            continue
        report_rel = payload.get("report_path")
        device = payload.get("device", {})
        if not report_rel:
            continue
        rows.append(
            {
                "created_utc": payload.get("created_utc", ""),
                "kind": payload.get("report_kind", "unknown"),
                "title": payload.get("report_title", "report"),
                "report_rel": report_rel,
                "model": device.get("type") or device.get("model") or "unknown",
                "firmware": device.get("fw") or device.get("firmware") or "unknown",
                "port": device.get("port", ""),
            }
        )

    rows.sort(key=lambda x: x.get("created_utc", ""), reverse=True)

    lines: list[str] = []
    lines.append("# Riden Test Reports Index")
    lines.append("")
    lines.append(f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    lines.append("")
    if not rows:
        lines.append("No reports found yet.")
    else:
        lines.append("| Date (UTC) | Kind | Device | FW | Port | Report |")
        lines.append("|---|---|---|---|---|---|")
        for row in rows:
            report_rel = row["report_rel"].replace("\\", "/")
            lines.append(
                "| {created_utc} | {kind} | {model} | {firmware} | {port} | [{title}]({report_rel}) |".format(
                    created_utc=row["created_utc"],
                    kind=row["kind"],
                    model=row["model"],
                    firmware=row["firmware"],
                    port=row["port"],
                    title=row["title"],
                    report_rel=report_rel,
                )
            )

    out = reports_root / "index.md"
    out.write_text("\n".join(lines) + "\n")
    return out


# ---------------------------------------------------------------------------
# Backfill: (re)build per-run report pages from existing docs/data artifacts.
# (merged from the former backfill_reports_from_docs.py)
# ---------------------------------------------------------------------------

def _safe_stamp(value: str, fallback: str) -> str:
    if not value:
        return fallback
    txt = value.strip().replace(":", "").replace("-", "")
    txt = txt.replace("T", "-").replace("Z", "")
    txt = re.sub(r"[^0-9]", "", txt)
    if len(txt) >= 14:
        return f"{txt[:8]}-{txt[8:14]}Z"
    return fallback


def _timing_report_from_json(*, reports_root: Path, docs_dir: Path,
                             json_path: Path, device_meta: dict) -> Path:
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


def backfill_main() -> int:
    ap = argparse.ArgumentParser(description="Backfill report pages from existing docs artifacts")
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
    device_meta = {"type": args.model, "id": args.device_id, "fw": args.fw, "sn": args.sn}

    timing_json = sorted(
        p for p in docs_dir.glob("*.json")
        if "results" in json.loads(p.read_text()) and "poll_points_ms" in json.loads(p.read_text())
    )
    written = [
        _timing_report_from_json(reports_root=reports_root, docs_dir=docs_dir,
                                 json_path=p, device_meta=device_meta)
        for p in timing_json
    ]
    index_path = update_reports_index(reports_root)
    for p in written:
        print(f"WROTE {p}")
    print(f"WROTE {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(backfill_main())
