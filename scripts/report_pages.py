#!/usr/bin/env python3
"""Helpers for per-device run reports and a global reports index."""

from __future__ import annotations

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
