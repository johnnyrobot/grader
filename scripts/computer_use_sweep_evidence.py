#!/usr/bin/env python3
"""Validate signed-app Computer Use sweep captures for Grader release evidence.

The Computer Use sweep is intentionally human-operated: an agent/instructor drives
the signed app and captures accessibility trees plus screenshots. This script
turns those captures into a small auditable JSON artifact and fail-closes on the
stale approval-state bug that was found in the old signed DMG.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import subprocess
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
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE_ID = "com.grader.app"


def _read_text(path: Path) -> str:
    return path.read_text(errors="replace")


def _latest_mtime(path: Path) -> float:
    if path.is_file():
        return path.stat().st_mtime
    latest = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            continue
    return latest


def _mtime_metadata(path: Path) -> dict[str, Any]:
    mtime = _latest_mtime(path)
    return {
        "mtime_epoch": mtime,
        "mtime": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
    }


def _sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(child.relative_to(path)).encode())
        digest.update(b"\0")
        with child.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _git_source_revision() -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        timestamp = int(subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "log", "-1", "--format=%ct"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip())
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    return {
        "available": True,
        "commit": commit,
        "commit_time_epoch": timestamp,
        "commit_time": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
    }


def _app_bundle_metadata(
    app_path: Path | None,
    *,
    expected_version: str | None,
    expected_bundle_id: str | None,
) -> tuple[list[str], list[str], dict[str, Any] | None]:
    if app_path is None:
        return [], [], None

    failures: list[str] = []
    warnings: list[str] = []
    metadata: dict[str, Any] = {
        "path": str(app_path),
        "expected_version": expected_version,
        "expected_bundle_id": expected_bundle_id,
    }

    if not app_path.exists():
        metadata["present"] = False
        failures.append(f"app bundle does not exist: {app_path}")
        return failures, warnings, metadata
    if app_path.suffix != ".app" or not app_path.is_dir():
        metadata["present"] = False
        failures.append(f"app path is not a .app bundle: {app_path}")
        return failures, warnings, metadata

    metadata.update({
        "present": True,
        "file_count": len([child for child in app_path.rglob("*") if child.is_file()]),
        "sha256_tree": _sha256_tree(app_path),
        **_mtime_metadata(app_path),
    })

    info_plist = app_path / "Contents" / "Info.plist"
    metadata["info_plist"] = str(info_plist)
    if not info_plist.exists():
        failures.append(f"app Info.plist is missing: {info_plist}")
        return failures, warnings, metadata

    try:
        plist = plistlib.loads(info_plist.read_bytes())
    except Exception as exc:
        failures.append(f"could not parse app Info.plist: {exc}")
        return failures, warnings, metadata

    short_version = str(plist.get("CFBundleShortVersionString") or "")
    bundle_version = str(plist.get("CFBundleVersion") or "")
    bundle_id = str(plist.get("CFBundleIdentifier") or "")
    metadata.update({
        "bundle_id": bundle_id,
        "short_version": short_version,
        "bundle_version": bundle_version,
    })

    if expected_bundle_id and bundle_id != expected_bundle_id:
        failures.append(
            f"app bundle id {bundle_id!r} does not match expected {expected_bundle_id!r}"
        )
    if expected_version:
        mismatched = [
            name
            for name, version in (
                ("CFBundleShortVersionString", short_version),
                ("CFBundleVersion", bundle_version),
            )
            if version != expected_version
        ]
        if mismatched:
            failures.append(
                "app version mismatch for "
                + ", ".join(mismatched)
                + f"; expected {expected_version!r}"
            )
    else:
        warnings.append("no expected app version supplied for Computer Use sweep evidence")

    source = _git_source_revision()
    metadata["source_revision"] = source
    if source.get("available") is True:
        commit_time = float(source["commit_time_epoch"])
        metadata["fresh_for_current_source"] = metadata["mtime_epoch"] + 1 >= commit_time
        if not metadata["fresh_for_current_source"]:
            warnings.append(
                "app bundle timestamp predates the current git commit; rebuild signed app before using this sweep"
            )
    else:
        warnings.append("could not determine current git source revision for app freshness")

    return failures, warnings, metadata


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
    app_path: Path | None = None,
    expected_version: str | None = None,
    expected_bundle_id: str | None = DEFAULT_BUNDLE_ID,
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

    app_failures, app_warnings, app_metadata = _app_bundle_metadata(
        app_path,
        expected_version=expected_version,
        expected_bundle_id=expected_bundle_id,
    )
    failures.extend(app_failures)
    warnings.extend(app_warnings)

    if app_metadata and app_metadata.get("present") and ax_paths:
        latest_capture_mtime = max(path.stat().st_mtime for path in [*ax_paths, *png_paths])
        details["latest_capture_mtime_epoch"] = latest_capture_mtime
        details["latest_capture_mtime"] = datetime.fromtimestamp(
            latest_capture_mtime, timezone.utc
        ).isoformat()
        if latest_capture_mtime + 1 < float(app_metadata.get("mtime_epoch", 0)):
            warnings.append(
                "Computer Use captures predate the supplied app bundle; rerun visual sweep for strongest release evidence"
            )

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
    if app_metadata is not None:
        details["app"] = app_metadata

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
    parser.add_argument("--app", type=Path, default=None, help="Signed Grader.app bundle inspected by the sweep")
    parser.add_argument("--expected-version", default=None, help="Expected CFBundleShortVersionString and CFBundleVersion")
    parser.add_argument("--expected-bundle-id", default=DEFAULT_BUNDLE_ID)
    args = parser.parse_args()

    evidence = validate_sweep(
        capture_dir=args.capture_dir,
        min_captures=args.min_captures,
        app_path=args.app,
        expected_version=args.expected_version,
        expected_bundle_id=args.expected_bundle_id,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"status={evidence['status']}")
    print(f"json={args.out}")
    if evidence["failures"]:
        print("failures=" + "; ".join(evidence["failures"]))


if __name__ == "__main__":
    main()
