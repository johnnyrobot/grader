#!/usr/bin/env python3
"""Validate signed-app Computer Use sweep captures for Grader release evidence.

The Computer Use sweep is intentionally human-operated: an agent/instructor drives
the signed app and captures accessibility trees plus screenshots. This script
turns those captures into a small auditable JSON artifact and fail-closes on the
stale approval-state bug that was found in the old signed DMG.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REQUIRED_TEXT_MARKERS: tuple[str, ...] = (
    "Your grading stays yours",
    "Check this Mac",
    "Local engine connected",
    "Grading queue",
    "Settings",
    "General grading settings",
    "A workspace that fits how you read",
    "Model configuration",
    "Rubric library",
    "Local models",
    "Installed Grader models",
    "TEACHER DECISION",
    "Score and feedback",
    "Save changes and approve",
)

CONTRADICTION_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "approved_and_unsaved",
        "Your changes are approved",
        "Teacher changes not saved yet.",
    ),
    (
        "draft_approved_and_unsaved",
        "Grade approved. It is ready for your next posting step.",
        "Teacher changes not saved yet.",
    ),
)

NOOP_SCORE_UPDATE_RE = re.compile(r"Score updated from\s+(\d+(?:\.\d+)?)\s+to\s+\1\b")


def _read_text(path: Path) -> str:
    return path.read_text(errors="replace")


def _capture_stem(path: Path) -> str:
    name = path.name
    if name.endswith(".ax.txt"):
        return name[:-7]
    return path.stem


def collect_capture_files(capture_dir: Path) -> dict[str, dict[str, Path]]:
    captures: dict[str, dict[str, Path]] = {}
    if not capture_dir.exists():
        return captures
    for path in sorted(capture_dir.iterdir()):
        if path.name.endswith(".ax.txt"):
            captures.setdefault(_capture_stem(path), {})["ax"] = path
        elif path.suffix.lower() == ".png":
            captures.setdefault(path.stem, {})["png"] = path
    return captures


def _summary_status(capture_dir: Path) -> tuple[list[str], dict[str, Any] | None]:
    summary_path = capture_dir / "computer-use-signed-local-evidence.json"
    if not summary_path.exists():
        return [], None
    try:
        summary = json.loads(summary_path.read_text())
    except Exception as exc:
        return [f"could not parse existing sweep summary: {exc}"], None
    status = str(summary.get("status") or "")
    if status and status != "PASS":
        return [f"existing sweep summary status is {status}"], summary
    return [], summary


def validate_sweep(
    *,
    capture_dir: Path,
    min_captures: int = 12,
    required_text_markers: Iterable[str] = REQUIRED_TEXT_MARKERS,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {}

    captures = collect_capture_files(capture_dir)
    ax_paths = [entry["ax"] for entry in captures.values() if entry.get("ax")]
    png_paths = [entry["png"] for entry in captures.values() if entry.get("png")]
    missing_png = [
        stem
        for stem, entry in captures.items()
        if entry.get("ax") and not entry.get("png")
    ]
    missing_ax = [
        stem
        for stem, entry in captures.items()
        if entry.get("png") and not entry.get("ax")
    ]

    if not capture_dir.exists():
        failures.append(f"capture directory does not exist: {capture_dir}")
    if len(ax_paths) < min_captures:
        failures.append(f"only {len(ax_paths)} accessibility captures found; expected at least {min_captures}")
    if missing_png:
        failures.append("missing screenshots for captures: " + ", ".join(missing_png[:10]))
    if missing_ax:
        failures.append("missing accessibility captures for screenshots: " + ", ".join(missing_ax[:10]))

    combined_text = "\n".join(_read_text(path) for path in ax_paths)
    missing_markers = [marker for marker in required_text_markers if marker not in combined_text]
    if missing_markers:
        failures.append("required UI markers missing: " + ", ".join(missing_markers))

    stale_state_failures: list[dict[str, str]] = []
    for path in ax_paths:
        text = _read_text(path)
        for name, approved_text, unsaved_text in CONTRADICTION_PATTERNS:
            if approved_text in text and unsaved_text in text:
                stale_state_failures.append({
                    "file": str(path),
                    "pattern": name,
                    "approved_text": approved_text,
                    "unsaved_text": unsaved_text,
                })
        if NOOP_SCORE_UPDATE_RE.search(text):
            stale_state_failures.append({
                "file": str(path),
                "pattern": "noop_score_update",
                "approved_text": "Score updated from N to N",
                "unsaved_text": "",
            })
    if stale_state_failures:
        failures.append("stale or contradictory review approval state found")

    summary_failures, existing_summary = _summary_status(capture_dir)
    failures.extend(summary_failures)

    details.update({
        "capture_dir": str(capture_dir),
        "capture_count": len(captures),
        "accessibility_capture_count": len(ax_paths),
        "screenshot_count": len(png_paths),
        "min_captures": min_captures,
        "missing_markers": missing_markers,
        "missing_screenshots": missing_png,
        "missing_accessibility_captures": missing_ax,
        "stale_state_failures": stale_state_failures,
        "existing_summary_status": existing_summary.get("status") if isinstance(existing_summary, dict) else None,
    })

    status = "PASS" if not failures else "FAIL_COMPUTER_USE_SWEEP"
    if not capture_dir.exists() or len(ax_paths) == 0:
        status = "BLOCKED_COMPUTER_USE_SWEEP"

    if status == "PASS" and existing_summary is None:
        warnings.append("no existing Computer Use sweep summary JSON was present; validated raw captures only")

    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "failures": failures,
        "warnings": warnings,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate signed-app Computer Use sweep captures")
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/computer-use-sweep.json"))
    parser.add_argument("--min-captures", type=int, default=12)
    args = parser.parse_args()

    evidence = validate_sweep(capture_dir=args.capture_dir, min_captures=args.min_captures)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"status={evidence['status']}")
    print(f"json={args.out}")
    if evidence["failures"]:
        print("failures=" + "; ".join(evidence["failures"]))


if __name__ == "__main__":
    main()
