import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.frontend_evidence import REQUIRED_CONTROL_LABELS, REQUIRED_VISUAL_SCREENS
from scripts.release_readiness import (
    BETA_PROFILE,
    CLEAN_MACHINE_REQUIRED_CHECKS,
    DRAFT_BIAS_ROW_ID,
    SHIP_V1_PROFILE,
    _augment_updater_with_key_diagnostic,
    _normalize_artifact,
    _normalize_citation_artifact,
    _normalize_clean_machine_artifact,
    _normalize_draft_bias_artifact,
    _normalize_updater_artifact,
    _normalize_updater_channel_artifact,
    _signing_preflight,
    build_e2e_matrix,
    build_next_actions,
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
        "release_version": {"status": "PASS"},
        "github_release": {"status": "PASS"},
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
        "release_version": {"status": "PASS"},
        "github_release": {"status": "PASS"},
        "updater": {"status": "PASS"},
        "updater_channel": {"status": "PASS"},
        "signing": {"status": "PASS"},
        "clean_machine": {"status": "PASS"},
    }
    assert decide_verdict(evidence) == "supervised_public_beta"


def test_decide_verdict_blocked_when_signing_blocked():
    evidence = {"quality": {"status": "PASS"}, "signing": {"status": "BLOCKED_SIGNING_CREDENTIALS"}}
    assert decide_verdict(evidence) == "blocked"


def test_decide_verdict_blocked_when_updater_channel_blocked():
    evidence = {
        "quality": {"status": "PASS"},
        "fairness": {"status": "PASS"},
        "citations": {"status": "PASS_EXPERIMENTAL", "mode": "experimental"},
        "canvas": {"status": "PASS"},
        "privacy": {"status": "PASS"},
        "performance": {"status": "PASS"},
        "frontend": {"status": "PASS"},
        "rust": {"status": "PASS"},
        "release_version": {"status": "PASS"},
        "github_release": {"status": "PASS"},
        "updater": {"status": "PASS"},
        "updater_channel": {"status": "BLOCKED_UPDATER_CHANNEL"},
        "signing": {"status": "PASS"},
        "clean_machine": {"status": "PASS"},
    }

    assert decide_verdict(evidence) == "blocked"
    blockers = summarize_blockers(evidence)
    assert any(blocker["gate"] == "updater_channel" for blocker in blockers)


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
                "visual_sweep_required_screens": len(REQUIRED_VISUAL_SCREENS),
                "visual_sweep_required_controls": len(REQUIRED_CONTROL_LABELS),
                "visual_sweep_screens": 17,
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
        "release_version": {
            "status": "PASS",
            "artifact": {"path": "artifacts/release-evidence/release-version.json"},
        },
        "github_release": {
            "status": "PASS",
            "artifact": {"path": "artifacts/release-evidence/github-release-preflight-current.json"},
        },
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
    assert summary["PASS"] == 18
    assert summary["PASS_EXPERIMENTAL"] == 1
    by_id = {row["id"]: row for row in matrix}
    assert by_id["ui-onboarding-queue-review-settings-models"]["status"] == "PASS"
    assert by_id["canvas-import-approve-post-readback"]["evidence"] == "artifacts/release-evidence/canvas-live.json"
    assert by_id["release-version-consistency"]["evidence"] == "artifacts/release-evidence/release-version.json"
    assert by_id["github-release-publication-preflight"]["evidence"] == "artifacts/release-evidence/github-release-preflight-current.json"
    assert by_id["updater-release-channel-preflight"]["evidence"] == "artifacts/release-evidence/updater-channel-current.json"


def test_a_stale_visual_sweep_floor_does_not_pass_the_ui_row():
    """The floor tracks the required-screen list, not a number typed once.

    16 screens and 47 controls were the requirement before the calibration screen
    landed. An artifact from that producer has not checked what is required now,
    and a `>=` against a stale constant would wave it through.
    """
    evidence = _passing_evidence(frontend={
        "status": "PASS",
        "summary": {
            "visual_sweep_validation": "PASS",
            "visual_sweep_required_screens": 16,
            "visual_sweep_required_controls": 47,
        },
    })

    by_id = {row["id"]: row for row in build_e2e_matrix(evidence)}

    assert by_id["ui-onboarding-queue-review-settings-models"]["status"] == "FAIL"


def test_the_ui_row_passes_at_the_counts_the_frontend_gate_currently_requires():
    by_id = {row["id"]: row for row in build_e2e_matrix(_passing_evidence())}

    assert by_id["ui-onboarding-queue-review-settings-models"]["status"] == "PASS"


def _passing_evidence(**overrides):
    """Every gate passing, so a test can vary the one thing it is about."""
    evidence = {
        "quality": {"status": "PASS"},
        "fairness": {"status": "PASS"},
        "citations": {"status": "PASS_EXPERIMENTAL"},
        "canvas": {"status": "PASS"},
        "privacy": {"status": "PASS"},
        "performance": {"status": "PASS"},
        "frontend": {
            "status": "PASS",
            "summary": {
                "visual_sweep_validation": "PASS",
                "visual_sweep_required_screens": len(REQUIRED_VISUAL_SCREENS),
                "visual_sweep_required_controls": len(REQUIRED_CONTROL_LABELS),
            },
        },
        "rust": {"status": "PASS"},
        "release_version": {"status": "PASS"},
        "github_release": {"status": "PASS"},
        "updater": {"status": "PASS"},
        "updater_channel": {"status": "PASS"},
        "signing": {"status": "PASS"},
        "clean_machine": {
            "status": "PASS",
            "checks": {name: True for name in CLEAN_MACHINE_REQUIRED_CHECKS},
        },
    }
    evidence.update(overrides)
    return evidence


def test_the_beta_profile_does_not_carry_the_draft_bias_row():
    """The risk was settled as not a reason to hold the beta (ADR-0001)."""
    matrix = build_e2e_matrix(_passing_evidence(), profile=BETA_PROFILE)

    assert DRAFT_BIAS_ROW_ID not in {row["id"] for row in matrix}
    assert summarize_e2e_matrix(matrix)["blocking"] == 0


def test_the_ship_v1_profile_adds_the_draft_bias_row_to_every_beta_row():
    beta = build_e2e_matrix(_passing_evidence(), profile=BETA_PROFILE)
    ship = build_e2e_matrix(_passing_evidence(), profile=SHIP_V1_PROFILE)

    ship_ids = [row["id"] for row in ship]
    assert set(row["id"] for row in beta) < set(ship_ids)
    assert DRAFT_BIAS_ROW_ID in ship_ids


def test_the_draft_bias_row_names_what_satisfies_it():
    evidence = _passing_evidence(
        draft_bias={
            "status": "BLOCKED_TEACHER_APPROVALS",
            "artifact": {"path": "artifacts/release-evidence/draft-bias-teacher-local.json"},
        }
    )

    row = {r["id"]: r for r in build_e2e_matrix(evidence, profile=SHIP_V1_PROFILE)}[DRAFT_BIAS_ROW_ID]

    assert "draft-bias-teacher-local.json" in row["evidence"]
    assert "teacher_local" in row["evidence"]
    # Both halves, each named where it is actually checked — the calibration
    # screen in the visual sweep, the review-screen line in the e2e suite.
    assert "15-calibration" in row["evidence"]
    assert "neuroinclusive-ui.spec.ts" in row["evidence"]
    assert "20" in row["requirement"]


def test_the_draft_bias_row_blocks_until_real_teacher_approvals_exist():
    evidence = _passing_evidence(draft_bias={"status": "BLOCKED_TEACHER_APPROVALS"})

    ship = {row["id"]: row for row in build_e2e_matrix(evidence, profile=SHIP_V1_PROFILE)}

    assert ship[DRAFT_BIAS_ROW_ID]["status"] == "BLOCKED_TEACHER_APPROVALS"
    assert ship[DRAFT_BIAS_ROW_ID]["blocking"] is True


def test_the_draft_bias_row_needs_the_app_surface_as_well_as_the_measurement():
    """Measured but unsurfaced is the automation-bias risk unmitigated."""
    evidence = _passing_evidence(
        draft_bias={"status": "PASS", "n": 24, "draft_bias": -0.066},
        frontend={"status": "FAIL"},
    )

    ship = {row["id"]: row for row in build_e2e_matrix(evidence, profile=SHIP_V1_PROFILE)}

    assert ship[DRAFT_BIAS_ROW_ID]["blocking"] is True
    assert "app" in ship[DRAFT_BIAS_ROW_ID]["summary"]


def test_the_draft_bias_row_passes_when_measured_and_surfaced():
    evidence = _passing_evidence(
        draft_bias={"status": "PASS", "n": 24, "draft_bias": -0.066},
    )

    ship = {row["id"]: row for row in build_e2e_matrix(evidence, profile=SHIP_V1_PROFILE)}

    assert ship[DRAFT_BIAS_ROW_ID]["status"] == "PASS"
    assert ship[DRAFT_BIAS_ROW_ID]["blocking"] is False
    assert summarize_e2e_matrix(build_e2e_matrix(evidence, profile=SHIP_V1_PROFILE))["blocking"] == 0


def test_the_beta_verdict_is_unchanged_by_a_blocked_draft_bias_row():
    evidence = _passing_evidence(
        citations={"status": "PASS_EXPERIMENTAL", "mode": "experimental"},
        draft_bias={"status": "BLOCKED_TEACHER_APPROVALS"},
    )

    assert decide_verdict(evidence) == "supervised_public_beta"
    assert not [b for b in summarize_blockers(evidence) if b["gate"] == "draft_bias"]


def test_missing_draft_bias_evidence_blocks_on_the_absent_approvals(tmp_path):
    bias = _normalize_draft_bias_artifact(tmp_path / "absent.json")

    assert bias["status"] == "BLOCKED_TEACHER_APPROVALS"
    assert "real teacher Approvals" in bias["reason"]

    ship = build_e2e_matrix(_passing_evidence(draft_bias=bias), profile=SHIP_V1_PROFILE)
    assert "real teacher Approvals" in {r["id"]: r for r in ship}[DRAFT_BIAS_ROW_ID]["summary"]


def test_draft_bias_evidence_below_the_evidence_floor_fails(tmp_path):
    artifact = tmp_path / "draft-bias.json"
    artifact.write_text(json.dumps({
        "status": "PASS",
        "evidence_tier": "teacher_local",
        "n": 19,
        "draft_bias": -0.066,
    }))

    bias = _normalize_draft_bias_artifact(artifact)

    assert bias["status"] == "FAIL"
    assert any("19" in failure and "20" in failure for failure in bias["failures"])


def test_draft_bias_evidence_without_a_direction_fails(tmp_path):
    """n at the floor but no figure means nothing was measured."""
    artifact = tmp_path / "draft-bias.json"
    artifact.write_text(json.dumps({
        "status": "PASS",
        "evidence_tier": "teacher_local",
        "n": 24,
        "draft_bias": None,
    }))

    bias = _normalize_draft_bias_artifact(artifact)

    assert bias["status"] == "FAIL"
    assert any("draft_bias" in failure for failure in bias["failures"])


def test_draft_bias_evidence_from_the_public_proxy_is_not_teacher_approvals(tmp_path):
    artifact = tmp_path / "draft-bias.json"
    artifact.write_text(json.dumps({
        "status": "PASS",
        "evidence_tier": "public_proxy",
        "n": 120,
        "draft_bias": -0.066,
    }))

    bias = _normalize_draft_bias_artifact(artifact)

    assert bias["status"] == "FAIL"
    assert any("teacher_local" in failure for failure in bias["failures"])


def test_draft_bias_evidence_passes_at_the_floor_on_real_approvals(tmp_path):
    artifact = tmp_path / "draft-bias.json"
    artifact.write_text(json.dumps({
        "status": "PASS",
        "evidence_tier": "teacher_local",
        "n": 20,
        "draft_bias": -0.066,
    }))

    bias = _normalize_draft_bias_artifact(artifact)

    assert bias["status"] == "PASS"


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
        "release_version": {"status": "BLOCKED_RELEASE_VERSION", "failures": ["tag v0.1.1 does not match app version"]},
        "github_release": {"status": "BLOCKED_GITHUB_RELEASE_PREFLIGHT", "failures": ["missing GitHub Actions secrets: APPLE_SIGNING_IDENTITY"]},
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
    assert by_id["release-version-consistency"]["blocking"] is True
    assert by_id["github-release-publication-preflight"]["blocking"] is True
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


def test_normalize_updater_channel_accepts_older_installed_baseline(tmp_path):
    artifact = tmp_path / "updater-channel.json"
    artifact.write_text(json.dumps({
        "status": "PASS",
        "details": {
            "config_version": "0.1.1",
            "current_version": "0.1.0",
            "manifest_version": "0.1.1",
        },
    }))

    channel = _normalize_updater_channel_artifact(artifact, "0.1.1")

    assert channel["status"] == "PASS"


def test_a_missing_updater_key_artifact_is_a_non_blocking_skip(tmp_path):
    """The updater key is optional evidence, so its absence must not block."""
    artifact = tmp_path / "missing-updater-key.json"

    updater_key = _normalize_artifact(artifact, "SKIP")

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
        "github_release": {"status": "PASS"},
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
    json_path, md_path = write_reports({
        "verdict": "alpha",
        "expected_version": "0.1.1",
        "quality": {"status": "SKIP"},
        "github_release": {"status": "BLOCKED_GITHUB_RELEASE_PREFLIGHT"},
        "updater": {"status": "BLOCKED_UPDATER_EVIDENCE"},
        "updater_channel": {"status": "BLOCKED_UPDATER_CHANNEL"},
        "clean_machine": {"status": "BLOCKED_CLEAN_MACHINE_EVIDENCE"},
    }, tmp_path)
    assert json.loads(json_path.read_text())["verdict"] == "alpha"
    markdown = md_path.read_text()
    assert "alpha" in markdown
    assert "End-to-End Testing and Evaluation Matrix" in markdown
    assert "quality-public-proxy-qwk" not in markdown
    assert "Open human-scored public-proxy quality gate" in markdown
    assert "Blocking Items" in markdown
    assert "Next Actions" in markdown
    assert "Ship v1.0" in markdown
    assert "Draft bias" in markdown
    assert "publish_local_release.py" in markdown
    assert "--password-keychain-service" in markdown


def test_build_next_actions_maps_release_blockers_to_secret_safe_commands():
    actions = build_next_actions({
        "expected_version": "0.1.1",
        "github_release": {"status": "BLOCKED_GITHUB_RELEASE_PREFLIGHT"},
        "updater": {"status": "BLOCKED_UPDATER_EVIDENCE"},
        "updater_channel": {"status": "BLOCKED_UPDATER_CHANNEL"},
        "signing": {"status": "BLOCKED_SIGNING_CREDENTIALS"},
        "clean_machine": {"status": "BLOCKED_CLEAN_MACHINE_EVIDENCE"},
    })

    by_gate = {action["gate"]: action for action in actions}
    assert set(by_gate) == {"github_release", "updater", "updater_channel", "signing", "clean_machine"}
    assert any("--candidate-tag v0.1.1" in command for command in by_gate["github_release"]["commands"])
    assert any("--password-keychain-service TAURI_SIGNING_PRIVATE_KEY_PASSWORD" in command for command in by_gate["updater"]["commands"])
    assert any("updater_channel_preflight.py" in command for command in by_gate["updater_channel"]["commands"])
    assert any("signing_doctor.py --print-repair-runbook" in command for command in by_gate["signing"]["commands"])
    assert "errSecInternalComponent" in " ".join(by_gate["signing"]["notes"])
    assert any("release_evidence_package.py" in command for command in by_gate["clean_machine"]["commands"])
    assert "manual app replacement" in " ".join(by_gate["clean_machine"]["notes"])


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
