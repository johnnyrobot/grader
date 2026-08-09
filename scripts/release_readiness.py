#!/usr/bin/env python3
"""Build a consolidated Grader release-readiness report."""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "engine") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "engine"))

from gates.result import CheckResult
from grader.draftbias import EVIDENCE_FLOOR
from scripts.frontend_evidence import REQUIRED_CONTROL_LABELS, REQUIRED_VISUAL_SCREENS
from scripts.signing_doctor import collect_signing_diagnostics


SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(TOKEN|PASSWORD|SECRET|PRIVATE_KEY|API_KEY)[A-Z0-9_]*)=([^\s]+)"
)
BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")

REQUIRED_GATES = (
    "quality",
    "fairness",
    "citations",
    "canvas",
    "privacy",
    "performance",
    "frontend",
    "rust",
    "release_version",
    "github_release",
    "updater",
    "updater_channel",
    "signing",
    "clean_machine",
)

CLEAN_MACHINE_REQUIRED_CHECKS = (
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

CLEAN_MACHINE_MANUAL_CHECKS = (
    "first_run_models",
    "crash_recovery",
    "keyboard_only_review",
    "computer_use_instructor_sweep",
    "updater_path",
)

PASSING_STATUSES = {"PASS", "PASS_EXPERIMENTAL"}

# Two audiences read this matrix and they are not asking the same question.
# `supervised_public_beta` is the entry gate for the supervised beta, and it is
# what `decide_verdict` answers. `ship_v1` is everything that gate asks plus what
# only a beta can produce — so it is a superset, and a row that belongs to it
# alone can never move the beta verdict.
BETA_PROFILE = "supervised_public_beta"
SHIP_V1_PROFILE = "ship_v1"

DRAFT_BIAS_ROW_ID = "draft-bias-teacher-approvals"
DRAFT_BIAS_ARTIFACT = "artifacts/release-evidence/draft-bias-teacher-local.json"
# Real approvals by a real teacher on their own Assignment. The public proxy is
# graded against a rubric we wrote, so a bias measured on it says nothing about
# whether Grader anchors *a teacher* low (ADR-0001, "Revisit when").
DRAFT_BIAS_EVIDENCE_TIER = "teacher_local"


def _status_is_passing(status: str) -> bool:
    return status in PASSING_STATUSES


def _gate_summary(gate: dict[str, Any]) -> str:
    updater_key_reason = gate.get("updater_key_reason")
    missing_checks = gate.get("missing_checks")
    if isinstance(missing_checks, list) and missing_checks:
        return "missing checks: " + ", ".join(str(check) for check in missing_checks)
    missing_manual = gate.get("missing_manual_evidence")
    if isinstance(missing_manual, list) and missing_manual:
        return "missing manual evidence: " + ", ".join(str(check) for check in missing_manual)
    version_mismatches = gate.get("version_mismatches")
    if isinstance(version_mismatches, list) and version_mismatches:
        return "version mismatches: " + "; ".join(str(item) for item in version_mismatches[:5])
    for key in ("summary", "reason"):
        value = gate.get(key)
        if value:
            if isinstance(value, dict):
                if value.get("visual_sweep_validation"):
                    return (
                        f"component_tests={value.get('component_tests')}, "
                        f"playwright_e2e_tests={value.get('playwright_e2e_tests')}, "
                        f"visual_sweep={value.get('visual_sweep_validation')}, "
                        f"screens={value.get('visual_sweep_screens')}, "
                        f"visible_controls={value.get('visual_sweep_visible_controls')}"
                    )
                return json.dumps(value, sort_keys=True)
            return str(value)
    failures = gate.get("failures")
    if isinstance(failures, list) and failures:
        summary = "failures: " + "; ".join(str(failure) for failure in failures[:5])
        if updater_key_reason:
            summary += f"; updater key: {updater_key_reason}"
        return summary
    if updater_key_reason:
        return f"updater key: {updater_key_reason}"
    output = str(gate.get("output", "")).strip()
    if output:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if lines:
            if lines[0][:1] in {"✓", "✗", "⊘"} and len(lines) > 1:
                return f"{lines[0]} — {lines[1]}"[:240]
            return lines[0][:240]
    return ""


def summarize_blockers(evidence: dict[str, Any]) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    for gate in REQUIRED_GATES:
        gate_evidence = evidence.get(gate, {})
        status = str(gate_evidence.get("status", "MISSING"))
        if not _status_is_passing(status):
            blockers.append({
                "gate": gate,
                "status": status,
                "summary": _gate_summary(gate_evidence),
            })
    return blockers


def _matrix_row(
    *,
    id: str,
    area: str,
    requirement: str,
    gate: str,
    status: str,
    evidence: str,
    summary: str = "",
    profiles: tuple[str, ...] = (BETA_PROFILE, SHIP_V1_PROFILE),
) -> dict[str, Any]:
    return {
        "id": id,
        "area": area,
        "requirement": requirement,
        "gate": gate,
        "status": status,
        "evidence": evidence,
        "summary": summary,
        "blocking": not _status_is_passing(status),
        "profiles": list(profiles),
    }


def _gate_status(evidence: dict[str, Any], gate: str) -> str:
    return str(evidence.get(gate, {}).get("status", "MISSING"))


def _clean_check_status(evidence: dict[str, Any], check: str) -> str:
    clean = evidence.get("clean_machine", {})
    if _gate_status(evidence, "clean_machine") == "PASS":
        return "PASS"
    checks = clean.get("checks", {}) if isinstance(clean, dict) else {}
    if checks.get(check) is True:
        return "PASS"
    status = _gate_status(evidence, "clean_machine")
    return status if status != "PASS" else "FAIL"


def _artifact_path(evidence: Any, fallback: str) -> str:
    if not isinstance(evidence, dict):
        return fallback
    artifact = evidence.get("artifact")
    if isinstance(artifact, dict):
        return str(artifact.get("path") or fallback)
    if isinstance(artifact, str):
        return artifact
    return fallback


def _frontend_matrix_status(evidence: dict[str, Any], expected_screens: int, expected_controls: int) -> str:
    frontend = evidence.get("frontend", {})
    status = _gate_status(evidence, "frontend")
    if status != "PASS":
        return status
    summary = frontend.get("summary", {}) if isinstance(frontend, dict) else {}
    if (
        summary.get("visual_sweep_validation") == "PASS"
        and int(summary.get("visual_sweep_required_screens") or 0) >= expected_screens
        and int(summary.get("visual_sweep_required_controls") or 0) >= expected_controls
    ):
        return "PASS"
    return "FAIL"


def _draft_bias_is_surfaced(evidence: dict[str, Any]) -> bool:
    """Is the measured figure actually in front of the teacher?

    The app half of the row, and the frontend gate is what carries it: its
    required visual sweep covers the calibration screen and the review-screen
    bias line. Measurement alone is not the mitigation — a bias nobody is shown
    still anchors the reviewer low.
    """
    return _status_is_passing(_gate_status(evidence, "frontend"))


def _draft_bias_matrix_status(evidence: dict[str, Any]) -> str:
    """Measured *and* surfaced, or the row does not pass."""
    measured = _gate_status(evidence, "draft_bias")
    if not _status_is_passing(measured):
        return measured
    return "PASS" if _draft_bias_is_surfaced(evidence) else "FAIL"


def _draft_bias_summary(evidence: dict[str, Any]) -> str:
    summary = _gate_summary(evidence.get("draft_bias", {}))
    if _status_is_passing(_gate_status(evidence, "draft_bias")) and not _draft_bias_is_surfaced(evidence):
        surfaced = (
            "measured, but not surfaced in the app: frontend gate "
            f"{_gate_status(evidence, 'frontend')}"
        )
        return f"{summary}; {surfaced}" if summary else surfaced
    return summary


def build_e2e_matrix(
    evidence: dict[str, Any],
    profile: str = BETA_PROFILE,
) -> list[dict[str, Any]]:
    """The matrix rows one profile asks for.

    `ship_v1` is a superset of `supervised_public_beta`: v1.0 asks everything the
    beta entry gate asks, and more.
    """
    frontend = evidence.get("frontend", {})
    frontend_summary = frontend.get("summary", {}) if isinstance(frontend, dict) else {}
    # The floor is what frontend_evidence.py requires *now*, read from the same
    # lists it validates against. Written as literals it silently stops tracking
    # them: the calibration screen was added to the required set without this
    # number moving, so a `>=` against a stale count waved through an artifact
    # that had never checked for the screen.
    frontend_status = _frontend_matrix_status(
        evidence,
        expected_screens=len(REQUIRED_VISUAL_SCREENS),
        expected_controls=len(REQUIRED_CONTROL_LABELS),
    )
    frontend_detail = (
        f"{frontend_summary.get('visual_sweep_screens', '?')} screens, "
        f"{frontend_summary.get('visual_sweep_visible_controls', '?')} visible controls, "
        f"{frontend_summary.get('visual_sweep_required_controls', '?')} required controls"
    )

    canvas = evidence.get("canvas", {})
    canvas_summary = _gate_summary(canvas) or (
        f"course={canvas.get('course_id')} assignment={canvas.get('assignment_id')} "
        f"posted={canvas.get('posted')} progress={canvas.get('progress_state')}"
        if isinstance(canvas, dict)
        else ""
    )

    clean = evidence.get("clean_machine", {})
    clean_summary = _gate_summary(clean)

    rows = [
        _matrix_row(
            id="quality-public-proxy-qwk",
            area="grading quality",
            requirement="Open human-scored public-proxy quality gate meets the beta QWK floor.",
            gate="quality",
            status=_gate_status(evidence, "quality"),
            evidence="engine/gates/quality.py",
            summary=_gate_summary(evidence.get("quality", {})),
        ),
        _matrix_row(
            id="fairness-perturbations",
            area="fairness",
            requirement="Perturbation fairness checks pass across release families.",
            gate="fairness",
            status=_gate_status(evidence, "fairness"),
            evidence="engine/gates/bias.py",
            summary=_gate_summary(evidence.get("fairness", {})),
        ),
        _matrix_row(
            id="citations-safe-mode",
            area="citations",
            requirement="Citation verification is either accurate enough for release or explicitly experimental with safe labeling.",
            gate="citations",
            status=_gate_status(evidence, "citations"),
            evidence="engine/verify_citations.py",
            summary=_gate_summary(evidence.get("citations", {})),
        ),
        _matrix_row(
            id="privacy-egress",
            area="privacy",
            requirement="Network egress is allowlisted and FERPA-safe for local-first operation.",
            gate="privacy",
            status=_gate_status(evidence, "privacy"),
            evidence="engine/gates/egress.py",
            summary=_gate_summary(evidence.get("privacy", {})),
        ),
        _matrix_row(
            id="performance-floor",
            area="performance",
            requirement="Release performance fits the 16 GB / batch budget.",
            gate="performance",
            status=_gate_status(evidence, "performance"),
            evidence="engine/gates/perf.py",
            summary=_gate_summary(evidence.get("performance", {})),
        ),
        _matrix_row(
            id="ui-onboarding-queue-review-settings-models",
            area="desktop UI",
            requirement="Instructor workflow covers onboarding, queue, review, settings, model manager, recovery, accessibility, and 200% reflow.",
            gate="frontend",
            status=frontend_status,
            evidence=_artifact_path(frontend, "artifacts/release-evidence/frontend.json"),
            summary=frontend_detail,
        ),
        _matrix_row(
            id="canvas-import-approve-post-readback",
            area="Canvas",
            requirement="Live Canvas import → approve/override → post → Progress completed → read-back path passes.",
            gate="canvas",
            status=_gate_status(evidence, "canvas"),
            evidence=_artifact_path(canvas, "artifacts/release-evidence/canvas-live.json"),
            summary=canvas_summary,
        ),
        _matrix_row(
            id="tauri-rust-build",
            area="desktop shell",
            requirement="Tauri/Rust shell checks pass for the desktop bundle boundary.",
            gate="rust",
            status=_gate_status(evidence, "rust"),
            evidence="app/src-tauri cargo check evidence",
            summary=_gate_summary(evidence.get("rust", {})),
        ),
        _matrix_row(
            id="release-version-consistency",
            area="distribution",
            requirement="Release tag/version metadata matches the app, Rust crate, frontend package, and Python project.",
            gate="release_version",
            status=_gate_status(evidence, "release_version"),
            evidence=_artifact_path(
                evidence.get("release_version", {}),
                "artifacts/release-evidence/release-version.json",
            ),
            summary=_gate_summary(evidence.get("release_version", {})),
        ),
        _matrix_row(
            id="github-release-publication-preflight",
            area="distribution",
            requirement="GitHub release workflow, updater endpoint, Actions secrets, and optional candidate tag checks are ready for publishing N+1.",
            gate="github_release",
            status=_gate_status(evidence, "github_release"),
            evidence=_artifact_path(
                evidence.get("github_release", {}),
                "artifacts/release-evidence/github-release-preflight-current.json",
            ),
            summary=_gate_summary(evidence.get("github_release", {})),
        ),
        _matrix_row(
            id="signed-updater-manifest",
            area="updates",
            requirement="Signed updater latest.json matches the evaluated release artifact and app version.",
            gate="updater",
            status=_gate_status(evidence, "updater"),
            evidence="scripts/updater_evidence.py",
            summary=_gate_summary(evidence.get("updater", {})),
        ),
        _matrix_row(
            id="updater-release-channel-preflight",
            area="updates",
            requirement="Live release channel advertises a newer signed Grader.app.tar.gz updater bundle before clean-machine N to N+1 testing starts.",
            gate="updater_channel",
            status=_gate_status(evidence, "updater_channel"),
            evidence=_artifact_path(
                evidence.get("updater_channel", {}),
                "artifacts/release-evidence/updater-channel-current.json",
            ),
            summary=_gate_summary(evidence.get("updater_channel", {})),
        ),
        _matrix_row(
            id="developer-id-notarization",
            area="distribution",
            requirement="Developer ID signing, hardened runtime, notarization, and staple preflight pass.",
            gate="signing",
            status=_gate_status(evidence, "signing"),
            evidence="scripts/signing_doctor.py",
            summary=_gate_summary(evidence.get("signing", {})),
        ),
        _matrix_row(
            id="clean-machine-install",
            area="clean machine",
            requirement="Signed/notarized app verifies with codesign, stapler, spctl, and release provenance on the tested artifact.",
            gate="clean_machine",
            status=_clean_check_status(evidence, "release_provenance"),
            evidence="scripts/clean_machine_evidence.py",
            summary=clean_summary,
        ),
        _matrix_row(
            id="first-run-models",
            area="clean machine",
            requirement="First-run model setup works on a clean supported Mac.",
            gate="clean_machine",
            status=_clean_check_status(evidence, "first_run_models"),
            evidence="scripts/clean_machine_evidence.py manual evidence",
            summary=clean_summary,
        ),
        _matrix_row(
            id="crash-recovery",
            area="clean machine",
            requirement="Engine/app crash recovery is validated on the signed clean-machine build.",
            gate="clean_machine",
            status=_clean_check_status(evidence, "crash_recovery"),
            evidence="scripts/clean_machine_evidence.py manual evidence",
            summary=clean_summary,
        ),
        _matrix_row(
            id="keyboard-only-review",
            area="clean machine",
            requirement="Keyboard-only review and approval path works in the signed app.",
            gate="clean_machine",
            status=_clean_check_status(evidence, "keyboard_only_review"),
            evidence="scripts/clean_machine_evidence.py manual evidence",
            summary=clean_summary,
        ),
        _matrix_row(
            id="computer-use-instructor-sweep",
            area="clean machine",
            requirement="Computer Use inspection confirms the signed app's visible instructor controls/options match the release matrix.",
            gate="clean_machine",
            status=_clean_check_status(evidence, "computer_use_instructor_sweep"),
            evidence="scripts/clean_machine_evidence.py manual evidence",
            summary=clean_summary,
        ),
        _matrix_row(
            id="clean-machine-updater-path",
            area="clean machine",
            requirement="N to N+1 updater path completes without manual replacement.",
            gate="clean_machine",
            status=_clean_check_status(evidence, "updater_path"),
            evidence="scripts/clean_machine_evidence.py manual evidence",
            summary=clean_summary,
        ),
        _matrix_row(
            id=DRAFT_BIAS_ROW_ID,
            area="calibration",
            requirement=(
                f"Draft bias is measured over real teacher Approvals at n >= {EVIDENCE_FLOOR} "
                "on an Assignment, and surfaced in the app."
            ),
            gate="draft_bias",
            status=_draft_bias_matrix_status(evidence),
            evidence=(
                f"{_artifact_path(evidence.get('draft_bias'), DRAFT_BIAS_ARTIFACT)} "
                f"({DRAFT_BIAS_EVIDENCE_TIER} calibration health from "
                "GET /api/calibration/{assignment_id}); the app surface is the frontend "
                "gate's 15-calibration required screen and the review-screen Draft bias "
                "line asserted in app/e2e/neuroinclusive-ui.spec.ts"
            ),
            summary=_draft_bias_summary(evidence),
            # Ship-v1.0 only. The beta entry gate has already been read as
            # passing, and this risk was settled as a reason to run the
            # supervised beta rather than to hold it (ADR-0001).
            profiles=(SHIP_V1_PROFILE,),
        ),
    ]
    return [row for row in rows if profile in row["profiles"]]


def summarize_e2e_matrix(matrix: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"total": len(matrix), "passing": 0, "blocking": 0}
    for row in matrix:
        status = str(row.get("status", "MISSING"))
        if _status_is_passing(status):
            summary["passing"] += 1
        else:
            summary["blocking"] += 1
        summary[status] = summary.get(status, 0) + 1
    return summary


def build_next_actions(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Return secret-safe, command-oriented next actions for blocking release gates."""
    expected_version = str(evidence.get("expected_version") or _app_version_from_config())
    actions: list[dict[str, Any]] = []

    def blocked(gate: str) -> bool:
        return not _status_is_passing(_gate_status(evidence, gate))

    if blocked("github_release"):
        actions.append({
            "gate": "github_release",
            "title": "Verify the public release repository and local publication path.",
            "commands": [
                (
                    f"uv run python scripts/github_release_preflight.py --candidate-tag v{expected_version} "
                    "--out artifacts/release-evidence/github-release-preflight-current.json --no-fail"
                ),
                (
                    "uv run python scripts/publish_local_release.py "
                    "--dmg app/src-tauri/target/release/bundle/dmg/Grader.dmg "
                    "--updater-bundle app/src-tauri/target/release/bundle/macos/Grader.app.tar.gz "
                    "--manifest latest.json "
                    f"--version {expected_version}"
                ),
            ],
            "notes": [
                "Apple signing/notarization stays on the maintainer Mac and does not require a .p12 export.",
                "The publisher is dry-run by default; use --apply only after signing and updater evidence pass.",
            ],
        })

    if blocked("updater"):
        actions.append({
            "gate": "updater",
            "title": "Regenerate signed updater manifest evidence for the evaluated release version.",
            "commands": [
                "uv run python scripts/updater_key_doctor.py --print-private-runbook > updater-key-private-runbook.md",
                (
                    "uv run python scripts/updater_key_doctor.py "
                    "--signing-key tauri-updater-key.pem "
                    "--password-keychain-service TAURI_SIGNING_PRIVATE_KEY_PASSWORD "
                    "--password-keychain-account grader-updater "
                    "--out artifacts/release-evidence/updater-key-current.json --no-fail"
                ),
                (
                    f"uv run python scripts/generate_appcast.py "
                    "--app app/src-tauri/target/release/bundle/macos/Grader.app"
                    f" --version {expected_version}"
                    " --signing-key tauri-updater-key.pem"
                    " --password-keychain-service TAURI_SIGNING_PRIVATE_KEY_PASSWORD"
                    " --password-keychain-account grader-updater"
                    " --output latest.json"
                ),
                (
                    f"uv run python scripts/updater_evidence.py --manifest latest.json "
                    " --artifact app/src-tauri/target/release/bundle/macos/Grader.app.tar.gz"
                    f" --version {expected_version}"
                    " --out artifacts/release-evidence/updater-current.json"
                ),
            ],
            "notes": [
                "The current blocker is expected while latest.json is stale or the updater-key password is unavailable.",
                "The updater-key password must come from a private shell, CI secret, or named macOS Keychain item.",
            ],
        })

    if blocked("updater_channel"):
        actions.append({
            "gate": "updater_channel",
            "title": "Publish the signed release assets and preflight the live updater channel.",
            "commands": [
                (
                    "uv run python scripts/updater_channel_preflight.py "
                    "--current-version <installed-N-version> "
                    "--out artifacts/release-evidence/updater-channel-current.json --no-fail"
                ),
            ],
            "notes": [
                "Run this only after the GitHub release exposes latest.json and the signed Grader.app.tar.gz updater bundle.",
                "This is a preflight for, not a replacement for, the clean-machine in-app N→N+1 update test.",
            ],
        })

    if blocked("signing"):
        actions.append({
            "gate": "signing",
            "title": "Repair local Developer ID signing and notarization credentials.",
            "commands": [
                "uv run python scripts/signing_doctor.py --print-repair-runbook > signing-repair-runbook.md",
                (
                    "uv run python scripts/signing_doctor.py "
                    "--out artifacts/release-evidence/signing-current.json --no-fail"
                ),
                "./scripts/sign_and_notarize.sh --dmg",
            ],
            "notes": [
                "Do this in a private local shell; do not paste Apple passwords or keychain passwords into chat.",
                "Repair private-key ACLs if the doctor reports errSecInternalComponent.",
                "Restore the notarytool profile before attempting notarization/stapling.",
            ],
        })

    if blocked("clean_machine"):
        actions.append({
            "gate": "clean_machine",
            "title": "Collect final clean-machine install, Computer Use, and updater N→N+1 evidence.",
            "commands": [
                (
                    "uv run python scripts/release_evidence_package.py "
                    "--out-dir artifacts/release-evidence/clean-machine-handoff "
                    "--github-release-evidence artifacts/release-evidence/github-release-preflight-current.json "
                    "--updater-channel-evidence artifacts/release-evidence/updater-channel-current.json "
                    "--updater-key-evidence artifacts/release-evidence/updater-key-current.json "
                    "--github-release-secrets-evidence artifacts/release-evidence/github-release-secrets-current.json"
                ),
                (
                    "uv run python scripts/clean_machine_evidence.py "
                    "--app /Applications/Grader.app "
                    "--dmg app/src-tauri/target/release/bundle/dmg/Grader.dmg "
                    "--updater-manifest latest.json "
                    f"--expected-version {expected_version} "
                    "--manual-evidence clean-machine-manual-evidence.json "
                    "--evidence-root <clean-machine-evidence-root> "
                    "--out artifacts/release-evidence/clean-machine.json"
                ),
            ],
            "notes": [
                "The updater_path manual check must use the in-app Install update and restart flow, not manual app replacement.",
                "Never include student work, Canvas tokens, Apple credentials, private keys, or passwords in the handoff package.",
            ],
        })

    return actions


def redact_secrets(value: str) -> str:
    redacted = SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    return BEARER_RE.sub("Bearer <redacted>", redacted)


def _run_jsonish(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    output = redact_secrets((proc.stdout or "") + (proc.stderr or ""))
    status = "PASS" if proc.returncode == 0 else "FAIL"
    return {"status": status, "command": command, "returncode": proc.returncode, "output": output}


def _normalize_artifact(path: Path | None, default_status: str) -> dict[str, Any]:
    """One check's evidence, as the flat dict the rest of this report expects.

    Reading an artifact — where the status hides, what to do when the file is
    absent or corrupt, what provenance to record — is gates.result's job now, so
    every check here reads its evidence by the same rules. `default_status` says
    what an absent artifact means for this particular check: SKIP where it is
    optional, a BLOCKED_* reason where its absence is itself the finding.
    """
    result = CheckResult.load(path, missing=default_status)
    # The artifact's own keys, plus a status and provenance — and nothing else.
    # Consumers downstream read `summary` as a structured block of the check's
    # own making, so a key the artifact never had cannot be invented here.
    return {"status": result.status, **result.details}


def _app_version_from_config(config_path: Path | None = None) -> str:
    config = config_path or PROJECT_ROOT / "app" / "src-tauri" / "tauri.conf.json"
    try:
        data = json.loads(config.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"missing Tauri config: {config}") from exc
    version = str(data.get("version") or "").strip()
    if not version:
        raise SystemExit(f"missing version in Tauri config: {config}")
    return version


def _version_mismatches(pairs: list[tuple[str, Any]], expected_version: str) -> list[str]:
    mismatches: list[str] = []
    for label, value in pairs:
        if value is None or value == "":
            mismatches.append(f"{label} is missing")
            continue
        if str(value) != expected_version:
            mismatches.append(f"{label} {value!r} does not match expected {expected_version!r}")
    return mismatches


def _fail_for_version_mismatch(
    evidence: dict[str, Any],
    *,
    expected_version: str,
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    mismatches = _version_mismatches(pairs, expected_version)
    if not mismatches:
        return evidence
    evidence["status"] = "FAIL"
    evidence["reason"] = "release artifact version mismatch"
    evidence["expected_version"] = expected_version
    evidence["version_mismatches"] = mismatches
    return evidence


def _normalize_citation_artifact(path: Path | None) -> dict[str, Any]:
    citation = _normalize_artifact(path, "SKIP")
    mode = citation.get("citation_mode", {})
    if mode:
        citation["mode"] = mode.get("mode")
        citation["status"] = mode.get("release_status", citation.get("status"))

    if citation.get("mode") == "experimental":
        false_positives = int(citation.get("false_positives", 0) or 0)
        retraction = citation.get("retraction_check", {})
        retraction_total = int(retraction.get("total", 0) or 0)
        retraction_caught = int(retraction.get("caught", 0) or 0)
        if false_positives == 0 and retraction_total == retraction_caught:
            citation["status"] = "PASS_EXPERIMENTAL"
            citation["accepted_experimental"] = True
            citation["reason"] = (
                "citation checks are explicitly experimental; zero false positives and all "
                "retraction fixtures are caught"
            )

    return citation


def _normalize_draft_bias_artifact(path: Path | None) -> dict[str, Any]:
    """Teacher-local `Draft bias` evidence, checked against what the term means.

    Absent evidence blocks on the reason it is absent: before the supervised beta
    there are no real teacher Approvals to measure over, so this is a gap that a
    release cannot close by rerunning anything locally.
    """
    bias = _normalize_artifact(path, "BLOCKED_TEACHER_APPROVALS")
    if bias.get("status") == "BLOCKED_TEACHER_APPROVALS" and not bias.get("reason"):
        bias["reason"] = (
            f"no {DRAFT_BIAS_EVIDENCE_TIER} Draft bias evidence — real teacher Approvals "
            "do not exist before the supervised beta"
        )
    if bias.get("status") != "PASS":
        return bias

    failures: list[str] = []
    tier = str(bias.get("evidence_tier") or "")
    if tier != DRAFT_BIAS_EVIDENCE_TIER:
        failures.append(
            f"evidence tier {tier or 'missing'!r} is not {DRAFT_BIAS_EVIDENCE_TIER!r}: "
            "Draft bias must be measured over real teacher Approvals"
        )
    n = bias.get("n")
    if not isinstance(n, int) or n < EVIDENCE_FLOOR:
        failures.append(f"n={n} is below the evidence floor of {EVIDENCE_FLOOR} approvals")
    if bias.get("draft_bias") is None:
        failures.append("draft_bias is null: no direction was measured")

    if failures:
        bias["status"] = "FAIL"
        bias["reason"] = "teacher-local Draft bias evidence does not meet the floor"
        bias["failures"] = failures
    return bias


def _normalize_updater_artifact(path: Path | None, expected_version: str) -> dict[str, Any]:
    updater = _normalize_artifact(path, "BLOCKED_UPDATER_EVIDENCE")
    if updater.get("status") != "PASS":
        return updater
    details = updater.get("details", {})
    if not isinstance(details, dict):
        details = {}
    return _fail_for_version_mismatch(
        updater,
        expected_version=expected_version,
        pairs=[
            ("details.expected_version", details.get("expected_version")),
            ("details.config_version", details.get("config_version")),
            ("details.manifest_version", details.get("manifest_version")),
        ],
    )


def _normalize_updater_channel_artifact(path: Path | None, expected_version: str) -> dict[str, Any]:
    channel = _normalize_artifact(path, "SKIP")
    if channel.get("status") != "PASS":
        return channel
    details = channel.get("details", {})
    if not isinstance(details, dict):
        details = {}
    return _fail_for_version_mismatch(
        channel,
        expected_version=expected_version,
        pairs=[
            ("details.config_version", details.get("config_version")),
            ("details.manifest_version", details.get("manifest_version")),
        ],
    )


def _augment_updater_with_key_diagnostic(
    updater: dict[str, Any],
    updater_key: dict[str, Any],
) -> dict[str, Any]:
    """Attach updater-key preflight context to a blocked updater manifest gate."""
    if _status_is_passing(str(updater.get("status", ""))):
        return updater

    key_status = str(updater_key.get("status", ""))
    if key_status in {"", "PASS", "SKIP"}:
        return updater

    augmented = dict(updater)
    reason = str(updater_key.get("reason") or "").strip()
    augmented["updater_key_status"] = key_status
    if reason:
        augmented["updater_key_reason"] = reason
    diagnostics = dict(augmented.get("diagnostics") or {})
    diagnostics["updater_key"] = {
        "status": key_status,
        "reason": reason,
        "missing_checks": updater_key.get("missing_checks", []),
    }
    augmented["diagnostics"] = diagnostics
    return augmented


def _normalize_clean_machine_artifact(path: Path | None, expected_version: str | None = None) -> dict[str, Any]:
    clean = _normalize_artifact(path, "BLOCKED_CLEAN_MACHINE_EVIDENCE")
    checks = clean.get("checks", {})
    missing = [name for name in CLEAN_MACHINE_REQUIRED_CHECKS if checks.get(name) is not True]
    if clean.get("status") != "PASS":
        if isinstance(checks, dict) and missing:
            clean["missing_checks"] = missing
        return clean
    details = clean.get("details", {})
    manual_details = details.get("manual_evidence", {}) if isinstance(details, dict) else {}
    weak_manual = [
        name
        for name in CLEAN_MACHINE_MANUAL_CHECKS
        if not isinstance(manual_details.get(name), dict) or manual_details[name].get("ok") is not True
    ]
    if missing:
        clean["status"] = "FAIL"
        clean["reason"] = "clean-machine PASS artifact is missing required passing checks"
        clean["missing_checks"] = missing
    elif weak_manual:
        clean["status"] = "FAIL"
        clean["reason"] = "clean-machine PASS artifact is missing auditable manual evidence"
        clean["missing_manual_evidence"] = weak_manual
    elif expected_version:
        provenance = details.get("release_provenance", {}) if isinstance(details, dict) else {}
        manifest = provenance.get("updater_manifest", {}) if isinstance(provenance, dict) else {}
        clean = _fail_for_version_mismatch(
            clean,
            expected_version=expected_version,
            pairs=[
                ("expected_version", clean.get("expected_version")),
                ("details.release_provenance.expected_version", provenance.get("expected_version")),
                ("details.release_provenance.updater_manifest.version", manifest.get("version")),
            ],
        )
    return clean


def _signing_preflight() -> dict[str, Any]:
    return collect_signing_diagnostics(runner=subprocess.run)


def decide_verdict(evidence: dict[str, Any]) -> str:
    statuses = {
        gate: str(evidence.get(gate, {}).get("status", "MISSING"))
        for gate in REQUIRED_GATES
    }
    if any(status == "FAIL" or status.startswith("BLOCKED") for status in statuses.values()):
        return "blocked"
    if all(status in {"PASS", "PASS_EXPERIMENTAL"} for status in statuses.values()):
        return "supervised_public_beta"
    return "alpha"


def collect_evidence(args: argparse.Namespace) -> dict[str, Any]:
    expected_version = args.expected_version or _app_version_from_config()
    evidence: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": args.profile,
        "expected_version": expected_version,
        "system": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
    }
    evidence["quality"] = _run_jsonish(
        [
            "uv",
            "run",
            "python",
            "engine/gates/quality.py",
            "--profile",
            "public_proxy",
            "--public-proxy-dir",
            args.public_proxy_dir,
        ]
    )
    evidence["fairness"] = _run_jsonish(
        [
            "uv",
            "run",
            "python",
            "engine/gates/bias.py",
            "--profile",
            "public_proxy",
            "--public-proxy-dir",
            args.public_proxy_dir,
        ]
    )
    evidence["citations"] = _normalize_citation_artifact(args.citation_artifact)
    evidence["canvas"] = _normalize_artifact(args.canvas_artifact, "SKIP")
    evidence["privacy"] = _normalize_artifact(args.privacy_artifact, "SKIP")
    evidence["performance"] = _normalize_artifact(args.performance_artifact, "SKIP")
    evidence["frontend"] = _normalize_artifact(args.frontend_artifact, "SKIP")
    evidence["rust"] = _normalize_artifact(args.rust_artifact, "SKIP")
    evidence["release_version"] = _normalize_artifact(args.release_version_artifact, "BLOCKED_RELEASE_VERSION")
    evidence["github_release"] = _normalize_artifact(
        args.github_release_artifact, "BLOCKED_GITHUB_RELEASE_PREFLIGHT")
    evidence["updater_key"] = _normalize_artifact(args.updater_key_artifact, "SKIP")
    evidence["updater"] = _augment_updater_with_key_diagnostic(
        _normalize_updater_artifact(args.updater_artifact, expected_version),
        evidence["updater_key"],
    )
    evidence["updater_channel"] = _normalize_updater_channel_artifact(
        args.updater_channel_artifact,
        expected_version,
    )
    evidence["signing"] = _signing_preflight()
    evidence["clean_machine"] = _normalize_clean_machine_artifact(
        args.clean_machine_artifact,
        expected_version=expected_version,
    )
    evidence["draft_bias"] = _normalize_draft_bias_artifact(args.draft_bias_artifact)
    evidence["verdict"] = decide_verdict(evidence)
    evidence["blockers"] = summarize_blockers(evidence)
    evidence["e2e_matrix"] = build_e2e_matrix(evidence)
    evidence["e2e_matrix_summary"] = summarize_e2e_matrix(evidence["e2e_matrix"])
    evidence["ship_v1_matrix"] = build_e2e_matrix(evidence, SHIP_V1_PROFILE)
    evidence["ship_v1_matrix_summary"] = summarize_e2e_matrix(evidence["ship_v1_matrix"])
    evidence["next_actions"] = build_next_actions(evidence)
    return evidence


def _matrix_table_rows(matrix: list[dict[str, Any]]) -> list[str]:
    lines = []
    for row in matrix:
        area = str(row.get("area", "")).replace("|", "\\|")
        requirement = str(row.get("requirement", "")).replace("|", "\\|")
        row_evidence = str(row.get("evidence", "")).replace("|", "\\|")
        status = str(row.get("status", "MISSING"))
        summary = str(row.get("summary", "")).replace("|", "\\|")
        lines.append(f"| {area} | {requirement} | {row_evidence} | `{status}` | {summary} |")
    return lines


def write_reports(evidence: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "release-readiness.json"
    md_path = out_dir / "release-readiness.md"
    json_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Grader Release Readiness",
        "",
        f"Verdict: `{evidence.get('verdict', 'unknown')}`",
        "",
        "| Gate | Status | Summary |",
        "| --- | --- | --- |",
    ]
    for gate in REQUIRED_GATES:
        gate_evidence = evidence.get(gate, {})
        status = gate_evidence.get("status", "MISSING")
        summary = _gate_summary(gate_evidence).replace("|", "\\|")
        lines.append(f"| {gate} | `{status}` | {summary} |")
    matrix = evidence.get("e2e_matrix") or build_e2e_matrix(evidence)
    matrix_summary = evidence.get("e2e_matrix_summary") or summarize_e2e_matrix(matrix)
    lines.extend([
        "",
        "## End-to-End Testing and Evaluation Matrix",
        "",
        (
            f"Passing rows: {matrix_summary.get('passing', 0)}/"
            f"{matrix_summary.get('total', len(matrix))}; blocking rows: "
            f"{matrix_summary.get('blocking', 0)}."
        ),
        "",
        "| Area | Requirement | Evidence | Status | Summary |",
        "| --- | --- | --- | --- | --- |",
    ])
    lines.extend(_matrix_table_rows(matrix))

    # Ship-v1.0 asks for everything above and more, so only the difference is
    # tabulated — repeating the beta rows would invite the two tables to be read
    # as alternatives rather than as one nested inside the other.
    ship_matrix = evidence.get("ship_v1_matrix") or build_e2e_matrix(evidence, SHIP_V1_PROFILE)
    ship_summary = evidence.get("ship_v1_matrix_summary") or summarize_e2e_matrix(ship_matrix)
    beta_ids = {row.get("id") for row in matrix}
    ship_only = [row for row in ship_matrix if row.get("id") not in beta_ids]
    if ship_only:
        lines.extend([
            "",
            "## Ship v1.0 Matrix",
            "",
            (
                "Ship v1.0 requires every row above, plus the rows below. This is not the "
                "beta entry gate and does not change the verdict. Passing rows: "
                f"{ship_summary.get('passing', 0)}/{ship_summary.get('total', len(ship_matrix))}; "
                f"blocking rows: {ship_summary.get('blocking', 0)}."
            ),
            "",
            "| Area | Requirement | Evidence | Status | Summary |",
            "| --- | --- | --- | --- | --- |",
        ])
        lines.extend(_matrix_table_rows(ship_only))
    blockers = evidence.get("blockers") or summarize_blockers(evidence)
    if blockers:
        lines.extend(["", "## Blocking Items", ""])
        for blocker in blockers:
            suffix = f" — {blocker['summary']}" if blocker.get("summary") else ""
            lines.append(f"- `{blocker['gate']}`: `{blocker['status']}`{suffix}")
    next_actions = evidence.get("next_actions") or build_next_actions(evidence)
    if next_actions:
        lines.extend(["", "## Next Actions", ""])
        for action in next_actions:
            lines.append(f"### {action.get('gate', 'unknown')}: {action.get('title', '')}")
            lines.append("")
            commands = action.get("commands") or []
            if commands:
                lines.append("```bash")
                lines.extend(str(command) for command in commands)
                lines.append("```")
            notes = action.get("notes") or []
            for note in notes:
                lines.append(f"- {note}")
            lines.append("")
    lines.extend(
        [
            "",
            "Public beta requires every listed gate to pass or be explicitly disabled with honest product labeling.",
            "",
        ]
    )
    md_path.write_text("\n".join(lines))
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Write Grader release readiness reports")
    parser.add_argument("--profile", default="public_beta")
    parser.add_argument("--public-proxy-dir", default="fixtures/public_proxy")
    parser.add_argument("--citation-artifact", type=Path, default=None)
    parser.add_argument("--canvas-artifact", type=Path, default=None)
    parser.add_argument("--privacy-artifact", type=Path, default=None)
    parser.add_argument("--performance-artifact", type=Path, default=None)
    parser.add_argument("--frontend-artifact", type=Path, default=None)
    parser.add_argument("--rust-artifact", type=Path, default=None)
    parser.add_argument("--release-version-artifact", type=Path, default=None)
    parser.add_argument(
        "--github-release-artifact",
        type=Path,
        default=Path("artifacts/release-evidence/github-release-preflight-current.json"),
        help="Optional read-only publication preflight artifact from scripts/github_release_preflight.py",
    )
    parser.add_argument("--updater-artifact", type=Path, default=None)
    parser.add_argument(
        "--updater-channel-artifact",
        type=Path,
        default=None,
        help="Optional live release-channel preflight artifact from scripts/updater_channel_preflight.py",
    )
    parser.add_argument(
        "--updater-key-artifact",
        type=Path,
        default=Path("artifacts/release-evidence/updater-key-current.json"),
        help="Optional updater signing-key preflight artifact from scripts/updater_key_doctor.py",
    )
    parser.add_argument("--clean-machine-artifact", type=Path, default=None)
    parser.add_argument(
        "--draft-bias-artifact",
        type=Path,
        default=Path(DRAFT_BIAS_ARTIFACT),
        help=(
            "Optional teacher-local Draft bias evidence: the calibration health payload for one "
            f"Assignment, with evidence_tier={DRAFT_BIAS_EVIDENCE_TIER}. Ship-v1.0 only."
        ),
    )
    parser.add_argument(
        "--expected-version",
        default=None,
        help="Expected Grader app/updater version; defaults to app/src-tauri/tauri.conf.json",
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-readiness"))
    args = parser.parse_args()

    evidence = collect_evidence(args)
    json_path, md_path = write_reports(evidence, args.out)
    print(f"verdict={evidence['verdict']}")
    print(f"json={json_path}")
    print(f"markdown={md_path}")


if __name__ == "__main__":
    main()
