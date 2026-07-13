#!/usr/bin/env python3
"""Collect clean-machine release evidence for Grader public beta.

This script intentionally cannot "fake" a clean-machine pass. It can run the
local macOS signing/notarization checks for a supplied app/DMG and records the
manual checks that must be performed on a clean supported Mac:

  - first-run model setup
  - engine crash recovery
  - keyboard-only review flow
  - updater path from N to N+1

The resulting JSON is accepted by ``scripts/release_readiness.py`` only when
all required checks are true.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


REQUIRED_CHECKS = (
    "codesign_verify",
    "stapler_validate",
    "spctl_assess",
    "first_run_models",
    "crash_recovery",
    "keyboard_only_review",
    "updater_path",
)

MANUAL_CHECKS = (
    "first_run_models",
    "crash_recovery",
    "keyboard_only_review",
    "updater_path",
)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run_check(command: list[str], *, runner: Runner = subprocess.run) -> dict[str, object]:
    proc = runner(command, text=True, capture_output=True, check=False)
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {
        "command": command,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "output": output[:2000],
    }


def load_manual_evidence(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        return {"_load_error": f"manual evidence file does not exist: {path}"}
    return json.loads(path.read_text())


def validate_manual_evidence(
    evidence: Mapping[str, Any],
    *,
    evidence_root: Path | None = None,
    require_files: bool = True,
) -> tuple[list[str], dict[str, object]]:
    """Return manual checks with auditable evidence.

    Each manual check must be shaped like:

      {
        "passed": true,
        "notes": "what was verified",
        "evidence_files": ["relative/or/absolute/path.png"]
      }

    ``evidence_files`` must be present and non-empty by default. Existing files
    are verified when paths are available locally.
    """
    passed: list[str] = []
    details: dict[str, object] = {}
    if evidence.get("_load_error"):
        details["_load_error"] = evidence["_load_error"]
        return passed, details

    for name in MANUAL_CHECKS:
        raw = evidence.get(name)
        if not isinstance(raw, Mapping):
            details[name] = {"ok": False, "reason": "missing manual evidence entry"}
            continue

        notes = str(raw.get("notes") or "").strip()
        evidence_files = raw.get("evidence_files") or []
        if isinstance(evidence_files, str):
            evidence_files = [evidence_files]
        evidence_files = [str(path) for path in evidence_files if str(path).strip()]

        missing_files: list[str] = []
        for file_name in evidence_files:
            candidate = Path(file_name)
            if not candidate.is_absolute() and evidence_root is not None:
                candidate = evidence_root / candidate
            if evidence_root is not None and not candidate.exists():
                missing_files.append(str(candidate))

        ok = (
            raw.get("passed") is True
            and bool(notes)
            and (not require_files or bool(evidence_files))
            and not missing_files
        )
        details[name] = {
            "ok": ok,
            "passed": raw.get("passed") is True,
            "notes": notes,
            "evidence_files": evidence_files,
            "missing_files": missing_files,
        }
        if ok:
            passed.append(name)

    return passed, details


def manual_evidence_template() -> dict[str, object]:
    return {
        name: {
            "passed": False,
            "performed_at": datetime.now(timezone.utc).isoformat(),
            "tester": "",
            "notes": "",
            "evidence_files": [
                f"{name}.png",
                f"{name}.log",
            ],
        }
        for name in MANUAL_CHECKS
    }


def collect_checks(
    *,
    app_path: Path | None,
    dmg_path: Path | None,
    manual_passes: Iterable[str],
    runner: Runner = subprocess.run,
) -> tuple[dict[str, bool], dict[str, object]]:
    checks = {name: False for name in REQUIRED_CHECKS}
    details: dict[str, object] = {}

    if app_path is not None and app_path.exists():
        codesign = _run_check(["codesign", "--verify", "--deep", "--strict", str(app_path)], runner=runner)
        spctl = _run_check(["spctl", "--assess", "--type", "execute", str(app_path)], runner=runner)
        checks["codesign_verify"] = bool(codesign["ok"])
        checks["spctl_assess"] = bool(spctl["ok"])
        details["codesign_verify"] = codesign
        details["spctl_assess"] = spctl
    elif app_path is not None:
        details["app_path"] = {"ok": False, "reason": "app path does not exist", "path": str(app_path)}

    stapler_target = dmg_path if dmg_path is not None else app_path
    if stapler_target is not None and stapler_target.exists():
        stapler = _run_check(["xcrun", "stapler", "validate", str(stapler_target)], runner=runner)
        checks["stapler_validate"] = bool(stapler["ok"])
        details["stapler_validate"] = stapler
    elif stapler_target is not None:
        details["stapler_validate"] = {
            "ok": False,
            "reason": "stapler target does not exist",
            "path": str(stapler_target),
        }

    for name in manual_passes:
        if name in MANUAL_CHECKS:
            checks[name] = True

    return checks, details


def build_evidence(
    *,
    checks: dict[str, bool],
    details: dict[str, object] | None = None,
    manual_evidence: dict[str, object] | None = None,
    app_path: Path | None = None,
    dmg_path: Path | None = None,
    notes: str = "",
) -> dict[str, object]:
    missing = [name for name in REQUIRED_CHECKS if checks.get(name) is not True]
    status = "PASS" if not missing else "BLOCKED_CLEAN_MACHINE_EVIDENCE"
    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "app_path": str(app_path) if app_path else None,
        "dmg_path": str(dmg_path) if dmg_path else None,
        "checks": {name: bool(checks.get(name)) for name in REQUIRED_CHECKS},
        "missing_checks": missing,
        "details": details or {},
        "manual_evidence": manual_evidence or {},
        "notes": notes,
    }


def write_evidence(path: Path, evidence: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect clean-machine release evidence")
    parser.add_argument("--app", type=Path, default=None, help="Path to signed/notarized Grader.app")
    parser.add_argument("--dmg", type=Path, default=None, help="Path to signed/notarized DMG")
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/clean-machine.json"))
    parser.add_argument("--notes", default="")
    parser.add_argument("--manual-evidence", type=Path, default=None,
                        help="JSON evidence for first-run/crash/keyboard/updater manual checks")
    parser.add_argument("--evidence-root", type=Path, default=None,
                        help="Resolve relative manual evidence files against this directory")
    parser.add_argument("--print-manual-template", action="store_true",
                        help="Print a clean-machine manual evidence JSON template and exit")
    parser.add_argument("--allow-manual-flags-without-evidence", action="store_true",
                        help="Legacy mode: count manual --first-run/--crash/etc flags without evidence files")
    parser.add_argument("--first-run-models", action="store_true")
    parser.add_argument("--crash-recovery", action="store_true")
    parser.add_argument("--keyboard-only-review", action="store_true")
    parser.add_argument("--updater-path", action="store_true")
    args = parser.parse_args()

    if args.print_manual_template:
        print(json.dumps(manual_evidence_template(), indent=2, sort_keys=True))
        return

    manual_evidence = load_manual_evidence(args.manual_evidence)
    evidence_root = args.evidence_root
    if evidence_root is None and args.manual_evidence is not None:
        evidence_root = args.manual_evidence.parent
    evidence_passes, evidence_details = validate_manual_evidence(
        manual_evidence,
        evidence_root=evidence_root,
    )
    manual = [
        name
        for name, enabled in (
            ("first_run_models", args.first_run_models),
            ("crash_recovery", args.crash_recovery),
            ("keyboard_only_review", args.keyboard_only_review),
            ("updater_path", args.updater_path),
        )
        if enabled
    ]
    if not args.allow_manual_flags_without_evidence:
        manual = []
    manual.extend(evidence_passes)
    checks, details = collect_checks(app_path=args.app, dmg_path=args.dmg, manual_passes=manual)
    details["manual_evidence"] = evidence_details
    evidence = build_evidence(
        checks=checks,
        details=details,
        manual_evidence=manual_evidence,
        app_path=args.app,
        dmg_path=args.dmg,
        notes=args.notes,
    )
    write_evidence(args.out, evidence)
    print(f"status={evidence['status']}")
    print(f"json={args.out}")
    if evidence["missing_checks"]:
        print("missing=" + ",".join(evidence["missing_checks"]))


if __name__ == "__main__":
    main()
