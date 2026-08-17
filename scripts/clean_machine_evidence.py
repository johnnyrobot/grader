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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ports import CodeSigner, DiskImage, find_app_bundle, source_revision

REQUIRED_CHECKS = (
    "codesign_verify",
    "stapler_validate",
    "spctl_assess",
    "release_provenance",
    "engine_bundle_self_contained",
    "foreign_machine_first_run",
    "first_run_models",
    "crash_recovery",
    "keyboard_only_review",
    "computer_use_instructor_sweep",
    "updater_path",
)

MANUAL_CHECKS = (
    "foreign_machine_first_run",
    "first_run_models",
    "crash_recovery",
    "keyboard_only_review",
    "computer_use_instructor_sweep",
    "updater_path",
)

CHECK_DEFINITIONS: dict[str, dict[str, object]] = {
    "foreign_machine_first_run": {
        "title": "First run on a Mac that has never held this repository",
        "objective": (
            "Launch the signed app on physically separate hardware that has never held a checkout "
            "of this repository, no uv, and no Python, and reach the queue."
        ),
        "required_assertions": [
            {
                "id": "never_held_repository",
                "label": (
                    "The test Mac has never held a checkout of this repository. A fresh user "
                    "profile on the build machine does NOT satisfy this: the engine used to be "
                    "found on the filesystem, which a new profile does not change (issue #23)."
                ),
            },
            {"id": "no_uv_installed", "label": "Confirmed `uv` is not installed on the test Mac (`which uv` finds nothing)."},
            {"id": "no_developer_python", "label": "Confirmed no Homebrew or pyenv Python is installed on the test Mac."},
            {"id": "engine_started_from_bundle", "label": "The app reached a ready engine without any terminal step."},
            {"id": "queue_reachable", "label": "Reached the queue/review workflow."},
            {"id": "hardware_recorded", "label": "Recorded the test Mac's model, OS version, and how it is known never to have held the repository."},
        ],
        "steps": [
            "Choose a Mac that has never had this repository on it. Borrowed hardware or a fresh VM both qualify; another account on the build machine does not.",
            "On that Mac, confirm the absence of the old dependencies: `which uv`, `which python3`, `ls ~/code/grader`.",
            "Copy over only the signed/notarized release artifact.",
            "Install and launch Grader from Finder — no Terminal.",
            "Confirm the engine becomes ready and the queue is reachable.",
            "Record the machine's model identifier and OS version in the notes.",
        ],
        "expected_result": (
            "Grader starts its own engine and reaches the queue on hardware that has never held "
            "this repository, with no uv and no Python installed."
        ),
        "suggested_files": ["foreign_machine_first_run.png", "foreign_machine_first_run.log"],
    },
    "first_run_models": {
        "title": "First-run model setup",
        "objective": "Install Grader on a clean supported Mac and verify the first-run model flow completes without hidden terminal help.",
        "required_assertions": [
            {"id": "clean_profile", "label": "Started from a clean user profile without existing Grader app data."},
            {"id": "models_absent_or_reconciled", "label": "Confirmed required Grader model tags were absent before first run or explicitly reconciled by the app."},
            {"id": "model_flow_completed_or_resumable", "label": "Completed model setup or reached a clear resumable model-readiness state without terminal-only instructions."},
            {"id": "queue_reachable", "label": "Reached the queue/review workflow or a non-crashing state that clearly explains what remains."},
        ],
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
        "required_assertions": [
            {"id": "review_context_open", "label": "Started from a queue/review state with teacher work visible."},
            {"id": "engine_forced_stopped", "label": "Forced the local engine process to stop during the session."},
            {"id": "visible_recovery_alert", "label": "Observed a readable in-app recovery alert or disconnected state."},
            {"id": "restart_action_works", "label": "Used the in-app restart/retry action and restored a usable queue/review state."},
        ],
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
        "required_assertions": [
            {"id": "queue_to_review_keyboard", "label": "Moved from queue to a review item using only keyboard input."},
            {"id": "visible_focus", "label": "Observed visible focus indicators on interactive controls."},
            {"id": "edit_and_approve_keyboard", "label": "Edited feedback or score and approved/saved using only keyboard input."},
            {"id": "no_focus_trap", "label": "Returned to the queue without a keyboard focus trap."},
        ],
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
        "required_assertions": [
            {"id": "signed_app_target", "label": "Computer Use targeted the installed signed app, not only the dev server or headless browser."},
            {"id": "screens_and_controls_covered", "label": "Captured onboarding, queue, review, settings, model manager, recovery, and safe visible controls."},
            {"id": "pass_json_included", "label": "Included a PASS JSON artifact from scripts/computer_use_sweep_evidence.py."},
            {"id": "no_ui_blockers", "label": "Recorded no unresolved inaccessible label, focus trap, stale state, or instructor-blocking ambiguity."},
        ],
        "steps": [
            "Launch the signed app from Finder or the installed Applications location.",
            "Use Computer Use to capture the app accessibility tree and screenshot before each navigation decision.",
            "Inspect onboarding, queue, review, settings, model manager, recovery alert, and responsive/zoomed states.",
            "Exercise or inspect every visible option, button, field, tab, radio, checkbox, and keyboard shortcut path that is safe to use locally.",
            "Run scripts/computer_use_sweep_evidence.py against the capture directory with --app and --expected-version, then include the PASS JSON output in this manual evidence.",
            "Record any blocked action, visual ambiguity, inaccessible label, focus trap, stale state, or mismatch with the Playwright visual-sweep inventory.",
        ],
        "expected_result": "The signed app's visible controls match the release matrix, are understandable to an instructor, and no additional UI blocker is found by Computer Use inspection.",
        "suggested_files": ["computer_use_instructor_sweep.json", "computer_use_instructor_sweep.png", "computer_use_instructor_sweep.log"],
    },
    "updater_path": {
        "title": "Updater N-to-N+1 path",
        "objective": "Verify the signed app can discover, download, install, and relaunch into an N+1 update using the signed latest.json manifest.",
        "required_assertions": [
            {"id": "installed_version_n", "label": "Started from a signed installed N release."},
            {"id": "signed_manifest_n_plus_1", "label": "Hosted or supplied a signed latest.json and signed N+1 artifact."},
            {"id": "in_app_update_completed", "label": "Completed download/install through the app updater path."},
            {"id": "relaunched_version_n_plus_1", "label": "Relaunched into N+1 and verified the displayed or recorded app version."},
        ],
        "steps": [
            "Install the signed N release on the clean Mac.",
            "Publish a signed N+1 release artifact plus latest.json to the configured updater endpoint, currently https://github.com/johnnyrobot/grader-releases/releases/latest/download/latest.json.",
            "Launch N and trigger the update check from the queue.",
            "Use the in-app Install update and restart action; do not replace the app manually.",
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


def _git_source_revision() -> dict[str, object]:
    return source_revision(PROJECT_ROOT)


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
    latest_mtime = _latest_mtime(path)
    metadata["mtime_epoch"] = latest_mtime
    metadata["mtime"] = datetime.fromtimestamp(latest_mtime, timezone.utc).isoformat()
    if path.is_file():
        metadata["size_bytes"] = path.stat().st_size
        metadata["sha256"] = _sha256_file(path)
    elif path.is_dir():
        files = [child for child in path.rglob("*") if child.is_file()]
        metadata["file_count"] = len(files)
        metadata["sha256_tree"] = _sha256_tree(path)
    return metadata


def _source_freshness(details: Mapping[str, object]) -> tuple[bool, dict[str, object]]:
    source = _git_source_revision()
    result: dict[str, object] = {
        "source_revision": source,
        "checked_artifacts": [],
        "stale_artifacts": [],
    }
    if source.get("available") is not True:
        result["ok"] = False
        result["reason"] = "could not determine current git source revision"
        return False, result

    commit_time = float(source["commit_time_epoch"])
    stale: list[str] = []
    checked: list[dict[str, object]] = []
    for name in ("app", "dmg", "updater_manifest"):
        artifact = details.get(name)
        if not isinstance(artifact, Mapping) or artifact.get("present") is not True:
            continue
        mtime = artifact.get("mtime_epoch")
        if not isinstance(mtime, (int, float)):
            stale.append(name)
            checked.append({"name": name, "reason": "missing mtime"})
            continue
        row = {
            "name": name,
            "path": artifact.get("path"),
            "mtime": artifact.get("mtime"),
            "mtime_epoch": mtime,
            "current_commit": source.get("commit"),
            "current_commit_time": source.get("commit_time"),
        }
        checked.append(row)
        if float(mtime) + 1 < commit_time:
            stale.append(name)

    result["checked_artifacts"] = checked
    result["stale_artifacts"] = stale
    result["ok"] = not stale and bool(checked)
    if stale:
        result["reason"] = (
            "release artifact timestamps predate the current git commit; "
            "rebuild and regenerate release evidence from this checkout"
        )
    elif not checked:
        result["reason"] = "no release artifacts with mtimes were available for source freshness"
    return bool(result["ok"]), result


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
    freshness_ok, freshness_details = _source_freshness(details)
    details["source_freshness"] = freshness_details
    ok = app_ok and dmg_ok and manifest_ok and version_ok and freshness_ok
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
        if not freshness_ok:
            missing.append("source_freshness")
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


def _check_engine_bundle(app_path: Path | None, *, runner: Runner = subprocess.run) -> tuple[bool, dict[str, object]]:
    """Ask whether the app carries a working engine, rather than expecting to find one.

    This is the check that would have caught #23 on the build machine. It relocates the
    app's engine outside this checkout and starts it with no uv, no Homebrew, and a
    throwaway HOME. It does not replace `foreign_machine_first_run`, which is the only
    evidence that genuinely foreign hardware can run the release.
    """
    if app_path is None:
        return False, {"ok": False, "reason": "no app path supplied"}
    if not app_path.exists():
        return False, {"ok": False, "reason": "app path does not exist", "path": str(app_path)}

    result = _run_check(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_engine_bundle.py"),
            "--app",
            str(app_path),
        ],
        runner=runner,
    )
    if not result["ok"]:
        result["reason"] = (
            "the app's engine did not start outside this checkout; it cannot run on a "
            "machine without this repository (issue #23)"
        )
    return bool(result["ok"]), result


def _check_app_signature(app_path: Path, *, runner: Runner = subprocess.run) -> tuple[bool, bool, dict[str, object]]:
    signer = CodeSigner(runner=runner)
    codesign = signer.verify_app(app_path)
    spctl = signer.assess_app(app_path)
    return bool(codesign["ok"]), bool(spctl["ok"]), {
        "source": "app_path",
        "app_path": str(app_path),
        "codesign_verify": codesign,
        "spctl_assess": spctl,
    }


def _check_app_signature_from_dmg(dmg_path: Path, *, runner: Runner = subprocess.run) -> tuple[bool, bool, dict[str, object]]:
    details: dict[str, object] = {"source": "mounted_dmg_app", "dmg_path": str(dmg_path)}
    codesign_ok = spctl_ok = False

    with DiskImage(dmg_path, runner=runner).mounted() as (mount_point, records):
        if mount_point is not None:
            app_path = find_app_bundle(mount_point)
            if app_path is None:
                records["reason"] = "mounted DMG does not contain a .app bundle"
            else:
                details["app_path"] = str(app_path)
                codesign_ok, spctl_ok, app_details = _check_app_signature(
                    app_path, runner=runner
                )
                details["app_signature"] = app_details
    details.update(records)

    return codesign_ok, spctl_ok, details


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
    expected_version: str | None = None,
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
        expected_assertions = [
            str(assertion["id"])
            for assertion in CHECK_DEFINITIONS[name].get("required_assertions", [])  # type: ignore[union-attr]
            if isinstance(assertion, Mapping) and assertion.get("id")
        ]
        raw_assertions = raw.get("assertions") or {}
        assertion_values: dict[str, bool] = {}
        if isinstance(raw_assertions, Mapping):
            assertion_values = {str(key): value is True for key, value in raw_assertions.items()}
        missing_assertions = [
            assertion_id for assertion_id in expected_assertions if assertion_values.get(assertion_id) is not True
        ]

        missing_files: list[str] = []
        resolved_files: list[Path] = []
        for file_name in evidence_files:
            candidate = Path(file_name)
            if not candidate.is_absolute() and evidence_root is not None:
                candidate = evidence_root / candidate
            if evidence_root is not None and not candidate.exists():
                missing_files.append(str(candidate))
            elif candidate.exists():
                resolved_files.append(candidate)

        computer_use_status = ""
        computer_use_status_files: list[str] = []
        computer_use_version = ""
        computer_use_bundle_version = ""
        computer_use_reason = ""
        computer_use_status_ok = True
        if name == "computer_use_instructor_sweep":
            computer_use_status_ok = False
            for candidate in resolved_files:
                if candidate.suffix.lower() != ".json":
                    continue
                try:
                    payload = json.loads(candidate.read_text())
                except Exception:
                    computer_use_status_files.append(str(candidate))
                    continue
                status = str(payload.get("status") or "")
                computer_use_status_files.append(str(candidate))
                if status == "PASS":
                    computer_use_status = status
                    if expected_version:
                        app_details = payload.get("details", {}).get("app", {})
                        if not isinstance(app_details, Mapping) or not app_details:
                            computer_use_reason = (
                                "PASS Computer Use sweep JSON is missing details.app metadata"
                            )
                            continue
                        computer_use_version = str(app_details.get("short_version") or "")
                        computer_use_bundle_version = str(app_details.get("bundle_version") or "")
                        if (
                            computer_use_version == expected_version
                            and computer_use_bundle_version == expected_version
                        ):
                            computer_use_status_ok = True
                            break
                        computer_use_reason = (
                            "PASS Computer Use sweep JSON does not match expected version "
                            f"{expected_version!r}"
                        )
                        continue
                    computer_use_status_ok = True
                    break
                if status and not computer_use_status:
                    computer_use_status = status

        ok = (
            raw.get("passed") is True
            and bool(performed_at)
            and bool(tester)
            and bool(notes)
            and (not require_files or bool(evidence_files))
            and not missing_files
            and not missing_assertions
            and computer_use_status_ok
        )
        details[name] = {
            "ok": ok,
            "passed": raw.get("passed") is True,
            "performed_at": performed_at,
            "tester": tester,
            "notes": notes,
            "evidence_files": evidence_files,
            "missing_files": missing_files,
            "assertions": assertion_values,
            "missing_assertions": missing_assertions,
        }
        if name == "computer_use_instructor_sweep":
            details[name]["computer_use_sweep_status"] = computer_use_status
            details[name]["computer_use_sweep_status_files"] = computer_use_status_files
            if expected_version:
                details[name]["expected_version"] = expected_version
                details[name]["computer_use_sweep_short_version"] = computer_use_version
                details[name]["computer_use_sweep_bundle_version"] = computer_use_bundle_version
            if not computer_use_status_ok:
                details[name]["reason"] = computer_use_reason or (
                    "computer_use_instructor_sweep requires a PASS JSON artifact from scripts/computer_use_sweep_evidence.py"
                )
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
            "assertions": {
                str(assertion["id"]): False
                for assertion in CHECK_DEFINITIONS[name].get("required_assertions", [])  # type: ignore[union-attr]
                if isinstance(assertion, Mapping) and assertion.get("id")
            },
            "passed": False,
            "performed_at": datetime.now(timezone.utc).isoformat(),
            "tester": "",
            "notes": "",
            "evidence_files": CHECK_DEFINITIONS[name]["suggested_files"],
        }
        for name in MANUAL_CHECKS
    }


def _release_version(config_path: Path | None = None) -> str:
    """The version this runbook is for, read from the app rather than typed.

    A version written into the prose dates itself at the next release, and the
    cost lands on whoever copies the command: the manual pass is 45 minutes,
    and evidence stamped for the previous version is rejected by
    `release_readiness` on exactly the mismatch it was collected to clear.
    """
    path = config_path or PROJECT_ROOT / "app" / "src-tauri" / "tauri.conf.json"
    try:
        return str(json.loads(path.read_text())["version"])
    except (OSError, ValueError, KeyError):
        # A placeholder the reader must replace beats a wrong version they
        # would not think to question.
        return "<version>"


def manual_evidence_runbook(config_path: Path | None = None) -> str:
    version = _release_version(config_path)
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
        f"uv run python scripts/clean_machine_evidence.py --app /Applications/Grader.app --dmg /path/to/Grader.dmg --updater-manifest /path/to/latest.json --expected-version {version} --manual-evidence clean-machine-manual-evidence.json --evidence-root clean-machine-evidence --out artifacts/release-evidence/clean-machine.json",
        "```",
        "",
        "Every manual check must include `passed: true`, `performed_at`, `tester`, non-empty `notes`, all required `assertions` set to `true`, and at least one existing evidence file. The `computer_use_instructor_sweep` check must include a PASS JSON artifact generated by `scripts/computer_use_sweep_evidence.py`. The final evidence also records hashes for the tested `.app`, DMG, and signed updater manifest.",
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
            "Required assertions:",
        ])
        for assertion in spec.get("required_assertions", []):  # type: ignore[union-attr]
            if isinstance(assertion, Mapping):
                lines.append(f"- `{assertion.get('id')}`: {assertion.get('label')}")
        lines.extend([
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

    bundle_ok, bundle_details = _check_engine_bundle(app_path, runner=runner)
    checks["engine_bundle_self_contained"] = bundle_ok
    details["engine_bundle_self_contained"] = bundle_details

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
        expected_version=args.expected_version,
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
