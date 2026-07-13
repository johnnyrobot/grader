import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.release_readiness import (
    CLEAN_MACHINE_REQUIRED_CHECKS,
    _augment_updater_with_key_diagnostic,
    _normalize_artifact,
    _normalize_citation_artifact,
    _normalize_clean_machine_artifact,
    _normalize_updater_artifact,
    _normalize_updater_key_artifact,
    _signing_preflight,
    build_e2e_matrix,
    decide_verdict,
    redact_secrets,
    summarize_blockers,
    summarize_e2e_matrix,
    write_reports,
)


def test_decide_verdict_supervised_public_beta_when_required_gates_pass():
    evidence = {
        "quality": {"status": "PASS"},
        "fairness": {"status": "PASS"},
        "citations": {"status": "PASS", "mode": "enabled"},
        "canvas": {"status": "PASS"},
        "privacy": {"status": "PASS"},
        "performance": {"status": "PASS"},
        "frontend": {"status": "PASS"},
        "rust": {"status": "PASS"},
        "updater": {"status": "PASS"},
        "updater_channel": {"status": "PASS"},
        "signing": {"status": "PASS"},
        "clean_machine": {"status": "PASS"},
    }
    assert decide_verdict(evidence) == "supervised_public_beta"


def test_decide_verdict_allows_accepted_experimental_citations():
    evidence = {
        "quality": {"status": "PASS"},
        "fairness": {"status": "PASS"},
        "citations": {"status": "PASS_EXPERIMENTAL", "mode": "experimental"},
        "canvas": {"status": "PASS"},
        "privacy": {"status": "PASS"},
        "performance": {"status": "PASS"},
        "frontend": {"status": "PASS"},
        "rust": {"status": "PASS"},
        "updater": {"status": "PASS"},
        "updater_channel": {"status": "PASS"},
        "signing": {"status": "PASS"},
        "clean_machine": {"status": "PASS"},
    }
    assert decide_verdict(evidence) == "supervised_public_beta"


def test_decide_verdict_blocked_when_signing_blocked():
    evidence = {"quality": {"status": "PASS"}, "signing": {"status": "BLOCKED_SIGNING_CREDENTIALS"}}
    assert decide_verdict(evidence) == "blocked"


def test_build_e2e_matrix_maps_public_beta_requirements_to_evidence():
    evidence = {
        "quality": {"status": "PASS", "output": "quality ok"},
        "fairness": {"status": "PASS", "output": "fairness ok"},
        "citations": {"status": "PASS_EXPERIMENTAL", "reason": "experimental label accepted"},
        "privacy": {"status": "PASS", "summary": "allowlist pass"},
        "performance": {"status": "PASS", "summary": "perf pass"},
        "frontend": {
            "status": "PASS",
            "summary": {
                "visual_sweep_validation": "PASS",
                "visual_sweep_required_screens": 16,
                "visual_sweep_required_controls": 47,
                "visual_sweep_screens": 16,
                "visual_sweep_visible_controls": 167,
            },
            "artifact": {"path": "artifacts/release-evidence/frontend.json"},
        },
        "canvas": {
            "status": "PASS",
            "course_id": 106,
            "assignment_id": 2841,
            "posted": 1,
            "progress_state": "completed",
            "artifact": {"path": "artifacts/release-evidence/canvas-live.json"},
        },
        "rust": {"status": "PASS"},
        "updater": {"status": "PASS"},
        "updater_channel": {
            "status": "PASS",
            "artifact": {"path": "artifacts/release-evidence/updater-channel-current.json"},
        },
        "signing": {"status": "PASS"},
        "clean_machine": {
            "status": "PASS",
            "checks": {name: True for name in CLEAN_MACHINE_REQUIRED_CHECKS},
        },
    }

    matrix = build_e2e_matrix(evidence)
    summary = summarize_e2e_matrix(matrix)

    assert summary["total"] == len(matrix)
    assert summary["blocking"] == 0
    assert summary["PASS"] == 16
    assert summary["PASS_EXPERIMENTAL"] == 1
    by_id = {row["id"]: row for row in matrix}
    assert by_id["ui-onboarding-queue-review-settings-models"]["status"] == "PASS"
    assert by_id["canvas-import-approve-post-readback"]["evidence"] == "artifacts/release-evidence/canvas-live.json"
    assert by_id["updater-release-channel-preflight"]["evidence"] == "artifacts/release-evidence/updater-channel-current.json"


def test_build_e2e_matrix_exposes_clean_machine_subcheck_blockers():
    evidence = {
        "quality": {"status": "PASS"},
        "fairness": {"status": "PASS"},
        "citations": {"status": "PASS_EXPERIMENTAL"},
        "privacy": {"status": "PASS"},
        "performance": {"status": "PASS"},
        "frontend": {
            "status": "PASS",
            "summary": {
                "visual_sweep_validation": "PASS",
                "visual_sweep_required_screens": 16,
                "visual_sweep_required_controls": 47,
            },
        },
        "canvas": {"status": "PASS"},
        "rust": {"status": "PASS"},
        "updater": {"status": "BLOCKED_UPDATER_EVIDENCE"},
        "updater_channel": {"status": "BLOCKED_UPDATER_CHANNEL", "failures": ["manifest returned HTTP 404"]},
        "signing": {"status": "BLOCKED_SIGNING_CREDENTIALS"},
        "clean_machine": {
            "status": "BLOCKED_CLEAN_MACHINE_EVIDENCE",
            "checks": {
                "stapler_validate": True,
                "first_run_models": False,
                "keyboard_only_review": False,
                "computer_use_instructor_sweep": False,
            },
            "missing_checks": [
                "first_run_models",
                "keyboard_only_review",
                "computer_use_instructor_sweep",
            ],
        },
    }

    by_id = {row["id"]: row for row in build_e2e_matrix(evidence)}

    assert by_id["first-run-models"]["status"] == "BLOCKED_CLEAN_MACHINE_EVIDENCE"
    assert by_id["keyboard-only-review"]["status"] == "BLOCKED_CLEAN_MACHINE_EVIDENCE"
    assert by_id["computer-use-instructor-sweep"]["status"] == "BLOCKED_CLEAN_MACHINE_EVIDENCE"
    assert by_id["signed-updater-manifest"]["blocking"] is True
    assert by_id["updater-release-channel-preflight"]["blocking"] is True
    assert "HTTP 404" in by_id["updater-release-channel-preflight"]["summary"]
    assert "missing checks" in by_id["clean-machine-install"]["summary"]


def test_signing_preflight_blocks_when_notary_profile_missing(monkeypatch):
    class Proc:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, **_kwargs):
        if command[:2] == ["security", "find-identity"]:
            return Proc(0, stdout='1) ABC "Developer ID Application: Example (TEAMID)"')
        if command[:1] == ["codesign"]:
            return Proc(0)
        if command[:2] == ["xcrun", "notarytool"]:
            return Proc(69, stderr="No Keychain password item found for profile: grader")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("scripts.release_readiness.subprocess.run", fake_run)

    result = _signing_preflight()

    assert result["status"] == "BLOCKED_SIGNING_CREDENTIALS"
    assert result["checks"]["developer_id_certificate"] is True
    assert result["checks"]["codesign_private_key_access"] is True
    assert result["checks"]["notary_keychain_profile"] is False
    assert "notarytool keychain profile" in result["reason"]


def test_signing_preflight_blocks_when_codesign_private_key_unavailable(monkeypatch):
    class Proc:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, **_kwargs):
        if command[:2] == ["security", "find-identity"]:
            return Proc(0, stdout='1) ABC "Developer ID Application: Example (TEAMID)"')
        if command[:1] == ["codesign"]:
            return Proc(1, stderr="errSecInternalComponent")
        if command[:2] == ["xcrun", "notarytool"]:
            return Proc(0)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("scripts.release_readiness.subprocess.run", fake_run)

    result = _signing_preflight()

    assert result["status"] == "BLOCKED_SIGNING_CREDENTIALS"
    assert result["checks"]["developer_id_certificate"] is True
    assert result["checks"]["codesign_private_key_access"] is False
    assert result["checks"]["notary_keychain_profile"] is True
    assert "codesign cannot use the Developer ID private key" in result["reason"]


def test_normalize_citation_artifact_accepts_safe_experimental_mode(tmp_path):
    artifact = tmp_path / "citation-results.json"
    artifact.write_text(json.dumps({
        "accuracy": 0.69,
        "false_positives": 0,
        "retraction_check": {"total": 3, "caught": 3},
        "citation_mode": {
            "mode": "experimental",
            "release_status": "FAIL",
            "experimental_required": True,
            "failures": ["accuracy below 90%"],
        },
    }))

    citation = _normalize_citation_artifact(artifact)

    assert citation["status"] == "PASS_EXPERIMENTAL"
    assert citation["accepted_experimental"] is True


def test_normalize_citation_artifact_rejects_experimental_false_positives(tmp_path):
    artifact = tmp_path / "citation-results.json"
    artifact.write_text(json.dumps({
        "accuracy": 0.69,
        "false_positives": 1,
        "retraction_check": {"total": 3, "caught": 3},
        "citation_mode": {
            "mode": "experimental",
            "release_status": "FAIL",
            "experimental_required": True,
            "failures": ["accuracy below 90%"],
        },
    }))

    citation = _normalize_citation_artifact(artifact)

    assert citation["status"] == "FAIL"


def test_normalize_artifact_records_file_provenance(tmp_path):
    artifact = tmp_path / "frontend.json"
    artifact.write_text(json.dumps({"status": "PASS", "summary": "component tests passed"}))

    normalized = _normalize_artifact(artifact, "SKIP")

    assert normalized["status"] == "PASS"
    assert normalized["artifact"]["path"] == str(artifact)
    assert normalized["artifact"]["exists"] is True
    assert normalized["artifact"]["size_bytes"] > 0
    assert len(normalized["artifact"]["sha256"]) == 64
    assert normalized["artifact"]["mtime"]


def test_normalize_artifact_records_missing_path_metadata(tmp_path):
    artifact = tmp_path / "missing.json"

    normalized = _normalize_artifact(artifact, "SKIP")

    assert normalized["status"] == "SKIP"
    assert normalized["artifact"] == {"path": str(artifact), "exists": False}


def test_normalize_updater_artifact_rejects_wrong_version_pass(tmp_path):
    artifact = tmp_path / "updater.json"
    artifact.write_text(json.dumps({
        "status": "PASS",
        "details": {
            "config_version": "0.1.0",
            "expected_version": "0.1.0",
            "manifest_version": "0.0.9",
        },
    }))

    updater = _normalize_updater_artifact(artifact, "0.1.0")

    assert updater["status"] == "FAIL"
    assert updater["reason"] == "release artifact version mismatch"
    assert "manifest_version" in updater["version_mismatches"][0]


def test_normalize_updater_artifact_rejects_missing_version_fields(tmp_path):
    artifact = tmp_path / "updater.json"
    artifact.write_text(json.dumps({"status": "PASS", "details": {}}))

    updater = _normalize_updater_artifact(artifact, "0.1.0")

    assert updater["status"] == "FAIL"
    assert "details.expected_version is missing" in updater["version_mismatches"]
    assert "details.manifest_version is missing" in updater["version_mismatches"]


def test_normalize_updater_key_artifact_missing_is_non_blocking_skip(tmp_path):
    artifact = tmp_path / "missing-updater-key.json"

    updater_key = _normalize_updater_key_artifact(artifact)

    assert updater_key["status"] == "SKIP"
    assert updater_key["artifact"] == {"path": str(artifact), "exists": False}


def test_updater_matrix_summary_includes_updater_key_blocker_reason():
    updater = _augment_updater_with_key_diagnostic(
        {
            "status": "BLOCKED_UPDATER_EVIDENCE",
            "failures": ["manifest platform darwin-aarch64 is missing signature"],
        },
        {
            "status": "BLOCKED_UPDATER_KEY",
            "reason": "TAURI_SIGNING_PRIVATE_KEY_PASSWORD is not set",
            "missing_checks": ["password_env_present", "signing_probe"],
        },
    )
    evidence = {
        "quality": {"status": "PASS"},
        "fairness": {"status": "PASS"},
        "citations": {"status": "PASS_EXPERIMENTAL"},
        "canvas": {"status": "PASS"},
        "privacy": {"status": "PASS"},
        "performance": {"status": "PASS"},
        "frontend": {"status": "PASS"},
        "rust": {"status": "PASS"},
        "updater": updater,
        "signing": {"status": "PASS"},
        "clean_machine": {"status": "PASS"},
    }

    matrix = {row["id"]: row for row in build_e2e_matrix(evidence)}
    blockers = {blocker["gate"]: blocker for blocker in summarize_blockers(evidence)}

    assert "missing signature" in matrix["signed-updater-manifest"]["summary"]
    assert "TAURI_SIGNING_PRIVATE_KEY_PASSWORD" in matrix["signed-updater-manifest"]["summary"]
    assert blockers["updater"]["status"] == "BLOCKED_UPDATER_EVIDENCE"
    assert "TAURI_SIGNING_PRIVATE_KEY_PASSWORD" in blockers["updater"]["summary"]
    assert updater["diagnostics"]["updater_key"]["missing_checks"] == [
        "password_env_present",
        "signing_probe",
    ]


def test_normalize_clean_machine_artifact_requires_all_checks(tmp_path):
    artifact = tmp_path / "clean-machine.json"
    artifact.write_text(json.dumps({
        "status": "PASS",
        "checks": {
            "codesign_verify": True,
        },
    }))

    clean = _normalize_clean_machine_artifact(artifact)

    assert clean["status"] == "FAIL"
    assert "first_run_models" in clean["missing_checks"]


def test_normalize_clean_machine_artifact_accepts_complete_pass(tmp_path):
    artifact = tmp_path / "clean-machine.json"
    artifact.write_text(json.dumps({
        "status": "PASS",
        "checks": {name: True for name in CLEAN_MACHINE_REQUIRED_CHECKS},
        "details": {
            "manual_evidence": {
                "first_run_models": {"ok": True},
                "crash_recovery": {"ok": True},
                "keyboard_only_review": {"ok": True},
                "computer_use_instructor_sweep": {"ok": True},
                "updater_path": {"ok": True},
            }
        },
    }))

    clean = _normalize_clean_machine_artifact(artifact)

    assert clean["status"] == "PASS"


def test_normalize_clean_machine_artifact_rejects_wrong_release_version(tmp_path):
    artifact = tmp_path / "clean-machine.json"
    artifact.write_text(json.dumps({
        "status": "PASS",
        "checks": {name: True for name in CLEAN_MACHINE_REQUIRED_CHECKS},
        "expected_version": "0.0.9",
        "details": {
            "release_provenance": {
                "expected_version": "0.0.9",
                "updater_manifest": {"ok": True, "version": "0.0.9"},
            },
            "manual_evidence": {
                "first_run_models": {"ok": True},
                "crash_recovery": {"ok": True},
                "keyboard_only_review": {"ok": True},
                "computer_use_instructor_sweep": {"ok": True},
                "updater_path": {"ok": True},
            },
        },
    }))

    clean = _normalize_clean_machine_artifact(artifact, expected_version="0.1.0")

    assert clean["status"] == "FAIL"
    assert clean["reason"] == "release artifact version mismatch"
    assert len(clean["version_mismatches"]) == 3


def test_normalize_clean_machine_artifact_rejects_missing_manual_evidence(tmp_path):
    artifact = tmp_path / "clean-machine.json"
    artifact.write_text(json.dumps({
        "status": "PASS",
        "checks": {name: True for name in CLEAN_MACHINE_REQUIRED_CHECKS},
    }))

    clean = _normalize_clean_machine_artifact(artifact)

    assert clean["status"] == "FAIL"
    assert clean["reason"] == "clean-machine PASS artifact is missing auditable manual evidence"
    assert "crash_recovery" in clean["missing_manual_evidence"]


def test_redact_secrets_masks_tokens():
    redacted = redact_secrets("CANVAS_TOKEN=secret APPLE_ID_PASSWORD=pass")
    assert "secret" not in redacted
    assert "pass" not in redacted


def test_write_reports(tmp_path):
    json_path, md_path = write_reports({"verdict": "alpha", "quality": {"status": "SKIP"}}, tmp_path)
    assert json.loads(json_path.read_text())["verdict"] == "alpha"
    markdown = md_path.read_text()
    assert "alpha" in markdown
    assert "End-to-End Testing and Evaluation Matrix" in markdown
    assert "quality-public-proxy-qwk" not in markdown
    assert "Open human-scored public-proxy quality gate" in markdown
    assert "Blocking Items" in markdown


def test_summarize_blockers_includes_fail_skip_and_blocked_reasons():
    blockers = summarize_blockers({
        "quality": {"status": "PASS"},
        "fairness": {"status": "FAIL", "output": "\n✗ FAIRNESS\n  incomplete perturbed_scores\n"},
        "canvas": {"status": "DRY_RUN", "summary": "pass --apply for live writes"},
        "citations": {"status": "SKIP", "reason": "missing artifact"},
        "updater": {"status": "BLOCKED_UPDATER_EVIDENCE", "failures": ["missing updater manifest latest.json"]},
        "clean_machine": {
            "status": "BLOCKED_CLEAN_MACHINE_EVIDENCE",
            "missing_checks": ["first_run_models"],
        },
    })

    by_gate = {blocker["gate"]: blocker for blocker in blockers}
    assert "quality" not in by_gate
    assert by_gate["fairness"]["status"] == "FAIL"
    assert by_gate["canvas"]["status"] == "DRY_RUN"
    assert "FAIRNESS" in by_gate["fairness"]["summary"]
    assert "incomplete perturbed_scores" in by_gate["fairness"]["summary"]
    assert by_gate["citations"]["summary"] == "missing artifact"
    assert by_gate["updater"]["summary"] == "failures: missing updater manifest latest.json"
    assert by_gate["clean_machine"]["status"] == "BLOCKED_CLEAN_MACHINE_EVIDENCE"
    assert by_gate["clean_machine"]["summary"] == "missing checks: first_run_models"
