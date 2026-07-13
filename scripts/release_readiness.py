#!/usr/bin/env python3
"""Build a consolidated Grader release-readiness report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    "updater",
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
) -> dict[str, str | bool]:
    return {
        "id": id,
        "area": area,
        "requirement": requirement,
        "gate": gate,
        "status": status,
        "evidence": evidence,
        "summary": summary,
        "blocking": not _status_is_passing(status),
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


def build_e2e_matrix(evidence: dict[str, Any]) -> list[dict[str, str | bool]]:
    frontend = evidence.get("frontend", {})
    frontend_summary = frontend.get("summary", {}) if isinstance(frontend, dict) else {}
    frontend_status = _frontend_matrix_status(evidence, expected_screens=16, expected_controls=47)
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
            id="signed-updater-manifest",
            area="updates",
            requirement="Signed updater latest.json matches the evaluated release artifact and app version.",
            gate="updater",
            status=_gate_status(evidence, "updater"),
            evidence="scripts/updater_evidence.py",
            summary=_gate_summary(evidence.get("updater", {})),
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
    ]
    return rows


def summarize_e2e_matrix(matrix: list[dict[str, str | bool]]) -> dict[str, int]:
    summary = {"total": len(matrix), "passing": 0, "blocking": 0}
    for row in matrix:
        status = str(row.get("status", "MISSING"))
        if _status_is_passing(status):
            summary["passing"] += 1
        else:
            summary["blocking"] += 1
        summary[status] = summary.get(status, 0) + 1
    return summary


def redact_secrets(value: str) -> str:
    redacted = SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    return BEARER_RE.sub("Bearer <redacted>", redacted)


def _run_jsonish(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    output = redact_secrets((proc.stdout or "") + (proc.stderr or ""))
    status = "PASS" if proc.returncode == 0 else "FAIL"
    return {"status": status, "command": command, "returncode": proc.returncode, "output": output}


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text())


def _artifact_ref(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "exists": False}
    ref: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return ref
    stat = path.stat()
    ref.update({
        "size_bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
    return ref


def _normalize_artifact(path: Path | None, default_status: str) -> dict[str, Any]:
    artifact = _artifact_ref(path)
    data = _load_json(path)
    if data is None:
        return {"status": default_status, "artifact": artifact}
    data = dict(data)
    data["artifact"] = artifact
    if "status" not in data:
        if data.get("release_status"):
            data["status"] = data["release_status"]
        elif data.get("citation_mode", {}).get("release_status"):
            data["status"] = data["citation_mode"]["release_status"]
    return data


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


def _normalize_updater_key_artifact(path: Path | None) -> dict[str, Any]:
    return _normalize_artifact(path, "SKIP")


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
    evidence["updater_key"] = _normalize_updater_key_artifact(args.updater_key_artifact)
    evidence["updater"] = _augment_updater_with_key_diagnostic(
        _normalize_updater_artifact(args.updater_artifact, expected_version),
        evidence["updater_key"],
    )
    evidence["signing"] = _signing_preflight()
    evidence["clean_machine"] = _normalize_clean_machine_artifact(
        args.clean_machine_artifact,
        expected_version=expected_version,
    )
    evidence["verdict"] = decide_verdict(evidence)
    evidence["blockers"] = summarize_blockers(evidence)
    evidence["e2e_matrix"] = build_e2e_matrix(evidence)
    evidence["e2e_matrix_summary"] = summarize_e2e_matrix(evidence["e2e_matrix"])
    return evidence


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
    for row in matrix:
        area = str(row.get("area", "")).replace("|", "\\|")
        requirement = str(row.get("requirement", "")).replace("|", "\\|")
        row_evidence = str(row.get("evidence", "")).replace("|", "\\|")
        status = str(row.get("status", "MISSING"))
        summary = str(row.get("summary", "")).replace("|", "\\|")
        lines.append(f"| {area} | {requirement} | {row_evidence} | `{status}` | {summary} |")
    blockers = evidence.get("blockers") or summarize_blockers(evidence)
    if blockers:
        lines.extend(["", "## Blocking Items", ""])
        for blocker in blockers:
            suffix = f" — {blocker['summary']}" if blocker.get("summary") else ""
            lines.append(f"- `{blocker['gate']}`: `{blocker['status']}`{suffix}")
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
    parser.add_argument("--updater-artifact", type=Path, default=None)
    parser.add_argument(
        "--updater-key-artifact",
        type=Path,
        default=Path("artifacts/release-evidence/updater-key.json"),
        help="Optional updater signing-key preflight artifact from scripts/updater_key_doctor.py",
    )
    parser.add_argument("--clean-machine-artifact", type=Path, default=None)
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
