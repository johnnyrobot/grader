#!/usr/bin/env python3
"""Collect clean-machine release evidence for Grader public beta.

This script intentionally cannot "fake" a clean-machine pass. It can run the
local macOS signing/notarization checks for a supplied app/DMG and records the
manual checks that must be performed on a clean supported Mac:

  - first-run model setup
  - engine crash recovery
  - keyboard-only review flow
  - Computer Use instructor sweep
  - updater path from N to N+1

The resulting JSON is accepted by ``scripts/release_readiness.py`` only when
all required checks are true.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import plistlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


REQUIRED_CHECKS = (
    "codesign_verify",
    "stapler_validate",
    "spctl_assess",
    "release_provenance",
    "first_run_models",
    "crash_recovery",
    "keyboard_only_review",
    "computer_use_instructor_sweep",
    "updater_path",
)

MANUAL_CHECKS = (
    "first_run_models",
    "crash_recovery",
    "keyboard_only_review",
    "computer_use_instructor_sweep",
    "updater_path",
)

CHECK_DEFINITIONS: dict[str, dict[str, object]] = {
    "first_run_models": {
        "title": "First-run model setup",
        "objective": "Install Grader on a clean supported Mac and verify the first-run model flow completes without hidden terminal help.",
        "steps": [
            "Start from a user account without existing Grader app data or preinstalled grader-text/grader-vision Ollama tags.",
            "Install the signed/notarized release artifact.",
            "Launch Grader from Finder.",
            "Complete onboarding through model readiness.",
            "Confirm the app either downloads/installs required models or gives a clear resumable action.",
        ],
        "expected_result": "The teacher can reach the queue/review workflow with required models ready, or a clear non-crashing resumable model setup state.",
        "suggested_files": ["first_run_models.png", "first_run_models.log"],
    },
    "crash_recovery": {
        "title": "Local engine crash recovery",
        "objective": "Prove the desktop app detects a local engine failure and recovers without losing the review context.",
        "steps": [
            "Launch the signed app and enter the queue/review workflow.",
            "Force-stop the local Grader engine process.",
            "Observe the visible recovery alert.",
            "Use the in-app restart/retry action.",
            "Confirm the queue/review state returns and the app remains usable.",
        ],
        "expected_result": "The app shows a readable recovery path, restarts the local engine, and preserves or safely reloads teacher work.",
        "suggested_files": ["crash_recovery.png", "crash_recovery.log"],
    },
    "keyboard_only_review": {
        "title": "Keyboard-only review flow",
        "objective": "Verify an instructor can review, edit, approve, and return to the queue without using a mouse.",
        "steps": [
            "Launch the signed app.",
            "Navigate from queue to a review item with Tab/Shift+Tab/Enter/Space and documented shortcuts.",
            "Edit at least one score or feedback field.",
            "Save or approve the draft.",
            "Return to the queue using the keyboard only.",
        ],
        "expected_result": "Focus indicators are visible, tab order is logical, no trap occurs, and approval/edit actions are reachable by keyboard.",
        "suggested_files": ["keyboard_only_review.png", "keyboard_only_review.log"],
    },
    "computer_use_instructor_sweep": {
        "title": "Computer Use instructor sweep",
        "objective": "Use Computer Use against the signed app to inspect the visible instructor UI as an actual teacher, not only through headless Playwright.",
        "steps": [
            "Launch the signed app from Finder or the installed Applications location.",
            "Use Computer Use to capture the app accessibility tree and screenshot before each navigation decision.",
            "Inspect onboarding, queue, review, settings, model manager, recovery alert, and responsive/zoomed states.",
            "Exercise or inspect every visible option, button, field, tab, radio, checkbox, and keyboard shortcut path that is safe to use locally.",
            "Record any blocked action, visual ambiguity, inaccessible label, focus trap, stale state, or mismatch with the Playwright visual-sweep inventory.",
        ],
        "expected_result": "The signed app's visible controls match the release matrix, are understandable to an instructor, and no additional UI blocker is found by Computer Use inspection.",
        "suggested_files": ["computer_use_instructor_sweep.png", "computer_use_instructor_sweep.log"],
    },
    "updater_path": {
        "title": "Updater N-to-N+1 path",
        "objective": "Verify the signed app can discover, download, install, and relaunch into an N+1 update using the signed latest.json manifest.",
        "steps": [
            "Install the signed N release on the clean Mac.",
            "Host or publish a signed N+1 release artifact plus latest.json.",
            "Launch N and trigger the update check.",
            "Download/install the update through the app.",
            "Relaunch and confirm the app version is N+1.",
        ],
        "expected_result": "The app updates through the Tauri updater path without Gatekeeper warnings or manual file replacement.",
        "suggested_files": ["updater_path.png", "updater_path.log"],
    },
}


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(p for p in path.rglob("*") if p.is_file()):
        relative = child.relative_to(path).as_posix().encode()
        digest.update(relative)
        digest.update(b"\0")
        with child.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _artifact_metadata(path: Path | None) -> dict[str, object]:
    if path is None:
        return {"present": False, "path": None}
    if not path.exists():
        return {"present": False, "path": str(path), "reason": "path does not exist"}
    metadata: dict[str, object] = {
        "present": True,
        "path": str(path),
        "kind": "directory" if path.is_dir() else "file",
    }
    if path.is_file():
        metadata["size_bytes"] = path.stat().st_size
        metadata["sha256"] = _sha256_file(path)
    elif path.is_dir():
        files = [child for child in path.rglob("*") if child.is_file()]
        metadata["file_count"] = len(files)
        metadata["sha256_tree"] = _sha256_tree(path)
    return metadata


def _manifest_metadata(path: Path | None, expected_version: str | None) -> dict[str, object]:
    metadata = _artifact_metadata(path)
    if not metadata.get("present") or path is None:
        metadata["ok"] = False
        return metadata
    try:
        manifest = json.loads(path.read_text())
    except Exception as exc:
        metadata["ok"] = False
        metadata["reason"] = f"could not parse updater manifest: {exc}"
        return metadata
    platforms = manifest.get("platforms") or {}
    signatures_present = {
        str(name): bool(entry.get("signature"))
        for name, entry in platforms.items()
        if isinstance(entry, Mapping)
    }
    metadata.update({
        "version": manifest.get("version"),
        "pub_date": manifest.get("pub_date"),
        "platforms": sorted(str(name) for name in platforms),
        "signatures_present": signatures_present,
    })
    version_ok = expected_version is None or str(manifest.get("version") or "") == expected_version
    signatures_ok = bool(signatures_present) and all(signatures_present.values())
    metadata["ok"] = bool(version_ok and signatures_ok)
    if not version_ok:
        metadata["reason"] = f"manifest version {manifest.get('version')!r} does not match expected {expected_version!r}"
    elif not signatures_ok:
        metadata["reason"] = "updater manifest is missing one or more platform signatures"
    return metadata


def collect_release_provenance(
    *,
    app_path: Path | None,
    dmg_path: Path | None,
    updater_manifest: Path | None,
    expected_version: str | None,
) -> tuple[bool, dict[str, object]]:
    details = {
        "expected_version": expected_version,
        "app": _artifact_metadata(app_path),
        "dmg": _artifact_metadata(dmg_path),
        "updater_manifest": _manifest_metadata(updater_manifest, expected_version),
    }
    app_ok = bool(details["app"].get("present")) if isinstance(details["app"], Mapping) else False
    dmg_ok = bool(details["dmg"].get("present")) if isinstance(details["dmg"], Mapping) else False
    manifest_ok = bool(details["updater_manifest"].get("ok")) if isinstance(details["updater_manifest"], Mapping) else False
    version_ok = bool(expected_version)
    ok = app_ok and dmg_ok and manifest_ok and version_ok
    if not ok:
        missing: list[str] = []
        if not version_ok:
            missing.append("expected_version")
        if not app_ok:
            missing.append("app")
        if not dmg_ok:
            missing.append("dmg")
        if not manifest_ok:
            missing.append("updater_manifest")
        details["missing_provenance"] = missing
    return ok, details


def _run_check(
    command: list[str],
    *,
    runner: Runner = subprocess.run,
    output_limit: int | None = 2000,
) -> dict[str, object]:
    proc = runner(command, text=True, capture_output=True, check=False)
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {
        "command": command,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "output": output if output_limit is None else output[:output_limit],
    }


def _find_app_bundle(root: Path) -> Path | None:
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.suffix == ".app":
            return child
    for child in sorted(root.rglob("*.app")):
        if child.is_dir():
            return child
    return None


def _check_app_signature(app_path: Path, *, runner: Runner = subprocess.run) -> tuple[bool, bool, dict[str, object]]:
    codesign = _run_check(["codesign", "--verify", "--deep", "--strict", str(app_path)], runner=runner)
    spctl = _run_check(["spctl", "--assess", "--type", "execute", str(app_path)], runner=runner)
    return bool(codesign["ok"]), bool(spctl["ok"]), {
        "source": "app_path",
        "app_path": str(app_path),
        "codesign_verify": codesign,
        "spctl_assess": spctl,
    }


def _check_app_signature_from_dmg(dmg_path: Path, *, runner: Runner = subprocess.run) -> tuple[bool, bool, dict[str, object]]:
    details: dict[str, object] = {"source": "mounted_dmg_app", "dmg_path": str(dmg_path)}
    attach = _run_check(
        ["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", str(dmg_path)],
        runner=runner,
        output_limit=None,
    )
    details["hdiutil_attach"] = attach
    if not attach.get("ok"):
        details["reason"] = "could not mount DMG to verify bundled app"
        return False, False, details

    mount_point: Path | None = None
    try:
        plist = plistlib.loads(str(attach.get("output") or "").encode())
        for entity in plist.get("system-entities", []):
            candidate = entity.get("mount-point")
            if candidate:
                mount_point = Path(candidate)
                break
    except Exception as exc:
        details["reason"] = f"could not parse mounted DMG metadata: {exc}"

    if mount_point is None:
        details["reason"] = details.get("reason") or "mounted DMG did not report a mount point"
        return False, False, details

    details["mount_point"] = str(mount_point)
    try:
        app_path = _find_app_bundle(mount_point)
        if app_path is None:
            details["reason"] = "mounted DMG does not contain a .app bundle"
            return False, False, details
        details["app_path"] = str(app_path)
        codesign_ok, spctl_ok, app_details = _check_app_signature(app_path, runner=runner)
        details["app_signature"] = app_details
        return codesign_ok, spctl_ok, details
    finally:
        details["hdiutil_detach"] = _run_check(
            ["hdiutil", "detach", str(mount_point)],
            runner=runner,
        )


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
        "performed_at": "2026-07-13T12:00:00Z",
        "tester": "initials or name",
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

        performed_at = str(raw.get("performed_at") or "").strip()
        tester = str(raw.get("tester") or "").strip()
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
            and bool(performed_at)
            and bool(tester)
            and bool(notes)
            and (not require_files or bool(evidence_files))
            and not missing_files
        )
        details[name] = {
            "ok": ok,
            "passed": raw.get("passed") is True,
            "performed_at": performed_at,
            "tester": tester,
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
            "title": str(CHECK_DEFINITIONS[name]["title"]),
            "objective": str(CHECK_DEFINITIONS[name]["objective"]),
            "steps": CHECK_DEFINITIONS[name]["steps"],
            "expected_result": str(CHECK_DEFINITIONS[name]["expected_result"]),
            "passed": False,
            "performed_at": datetime.now(timezone.utc).isoformat(),
            "tester": "",
            "notes": "",
            "evidence_files": CHECK_DEFINITIONS[name]["suggested_files"],
        }
        for name in MANUAL_CHECKS
    }


def manual_evidence_runbook() -> str:
    lines = [
        "# Grader Clean-Machine Evidence Runbook",
        "",
        "Use this on a clean supported Mac or VM after producing a signed/notarized app or DMG.",
        "Do not include student data, tokens, private keys, or Apple credentials in screenshots or logs.",
        "",
        "## Evidence workflow",
        "",
        "```bash",
        "uv run python scripts/clean_machine_evidence.py --print-manual-template > clean-machine-manual-evidence.json",
        "mkdir -p clean-machine-evidence",
        "# Fill clean-machine-manual-evidence.json after completing the checks below.",
        "uv run python scripts/clean_machine_evidence.py --app /Applications/Grader.app --dmg /path/to/Grader.dmg --updater-manifest /path/to/latest.json --expected-version 0.1.0 --manual-evidence clean-machine-manual-evidence.json --evidence-root clean-machine-evidence --out artifacts/release-evidence/clean-machine.json",
        "```",
        "",
        "Every manual check must include `passed: true`, `performed_at`, `tester`, non-empty `notes`, and at least one existing evidence file. The final evidence also records hashes for the tested `.app`, DMG, and signed updater manifest.",
        "",
        "## Manual checks",
        "",
    ]
    for name in MANUAL_CHECKS:
        spec = CHECK_DEFINITIONS[name]
        lines.extend([
            f"### `{name}` — {spec['title']}",
            "",
            str(spec["objective"]),
            "",
            "Steps:",
        ])
        for index, step in enumerate(spec["steps"], start=1):
            lines.append(f"{index}. {step}")
        lines.extend([
            "",
            f"Expected result: {spec['expected_result']}",
            "",
            "Suggested evidence files:",
        ])
        for file_name in spec["suggested_files"]:  # type: ignore[index]
            lines.append(f"- `{file_name}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def collect_checks(
    *,
    app_path: Path | None,
    dmg_path: Path | None,
    manual_passes: Iterable[str],
    updater_manifest: Path | None = None,
    expected_version: str | None = None,
    runner: Runner = subprocess.run,
) -> tuple[dict[str, bool], dict[str, object]]:
    checks = {name: False for name in REQUIRED_CHECKS}
    details: dict[str, object] = {}

    app_check_details: dict[str, object] | None = None
    if app_path is not None and app_path.exists():
        codesign_ok, spctl_ok, app_check_details = _check_app_signature(app_path, runner=runner)
        checks["codesign_verify"] = codesign_ok
        checks["spctl_assess"] = spctl_ok
        details["app_signature"] = app_check_details
    elif app_path is not None:
        details["app_path"] = {"ok": False, "reason": "app path does not exist", "path": str(app_path)}

    if (
        dmg_path is not None
        and dmg_path.exists()
        and (checks["codesign_verify"] is not True or checks["spctl_assess"] is not True)
    ):
        dmg_codesign_ok, dmg_spctl_ok, dmg_app_details = _check_app_signature_from_dmg(
            dmg_path,
            runner=runner,
        )
        details["mounted_dmg_app_signature"] = dmg_app_details
        if dmg_codesign_ok and dmg_spctl_ok:
            checks["codesign_verify"] = True
            checks["spctl_assess"] = True
            details["app_signature_source"] = "mounted_dmg_app"
        elif app_check_details is not None:
            details["app_signature_source"] = "app_path_failed_mounted_dmg_failed"
    elif app_check_details is not None:
        details["app_signature_source"] = "app_path"

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

    provenance_ok, provenance_details = collect_release_provenance(
        app_path=app_path,
        dmg_path=dmg_path,
        updater_manifest=updater_manifest,
        expected_version=expected_version,
    )
    checks["release_provenance"] = provenance_ok
    details["release_provenance"] = provenance_details

    return checks, details


def build_evidence(
    *,
    checks: dict[str, bool],
    details: dict[str, object] | None = None,
    manual_evidence: dict[str, object] | None = None,
    app_path: Path | None = None,
    dmg_path: Path | None = None,
    updater_manifest: Path | None = None,
    expected_version: str | None = None,
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
        "updater_manifest": str(updater_manifest) if updater_manifest else None,
        "expected_version": expected_version,
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
    parser.add_argument("--updater-manifest", type=Path, default=None, help="Path to signed Tauri latest.json")
    parser.add_argument("--expected-version", default=None, help="Expected app/updater version")
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/clean-machine.json"))
    parser.add_argument("--notes", default="")
    parser.add_argument("--manual-evidence", type=Path, default=None,
                        help="JSON evidence for first-run/crash/keyboard/updater manual checks")
    parser.add_argument("--evidence-root", type=Path, default=None,
                        help="Resolve relative manual evidence files against this directory")
    parser.add_argument("--print-manual-template", action="store_true",
                        help="Print a clean-machine manual evidence JSON template and exit")
    parser.add_argument("--print-manual-runbook", action="store_true",
                        help="Print a clean-machine Markdown runbook and exit")
    parser.add_argument("--allow-manual-flags-without-evidence", action="store_true",
                        help="Legacy mode: count manual --first-run/--crash/etc flags without evidence files")
    parser.add_argument("--first-run-models", action="store_true")
    parser.add_argument("--crash-recovery", action="store_true")
    parser.add_argument("--keyboard-only-review", action="store_true")
    parser.add_argument("--computer-use-instructor-sweep", action="store_true")
    parser.add_argument("--updater-path", action="store_true")
    args = parser.parse_args()

    if args.print_manual_template:
        print(json.dumps(manual_evidence_template(), indent=2, sort_keys=True))
        return
    if args.print_manual_runbook:
        print(manual_evidence_runbook(), end="")
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
            ("computer_use_instructor_sweep", args.computer_use_instructor_sweep),
            ("updater_path", args.updater_path),
        )
        if enabled
    ]
    if not args.allow_manual_flags_without_evidence:
        manual = []
    manual.extend(evidence_passes)
    checks, details = collect_checks(
        app_path=args.app,
        dmg_path=args.dmg,
        updater_manifest=args.updater_manifest,
        expected_version=args.expected_version,
        manual_passes=manual,
    )
    details["manual_evidence"] = evidence_details
    evidence = build_evidence(
        checks=checks,
        details=details,
        manual_evidence=manual_evidence,
        app_path=args.app,
        dmg_path=args.dmg,
        updater_manifest=args.updater_manifest,
        expected_version=args.expected_version,
        notes=args.notes,
    )
    write_evidence(args.out, evidence)
    print(f"status={evidence['status']}")
    print(f"json={args.out}")
    if evidence["missing_checks"]:
        print("missing=" + ",".join(evidence["missing_checks"]))


if __name__ == "__main__":
    main()
