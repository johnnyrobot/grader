import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.release_readiness import (
    CLEAN_MACHINE_REQUIRED_CHECKS,
    _normalize_citation_artifact,
    _normalize_clean_machine_artifact,
    _signing_preflight,
    decide_verdict,
    redact_secrets,
    summarize_blockers,
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
        "signing": {"status": "PASS"},
        "clean_machine": {"status": "PASS"},
    }
    assert decide_verdict(evidence) == "supervised_public_beta"


def test_decide_verdict_blocked_when_signing_blocked():
    evidence = {"quality": {"status": "PASS"}, "signing": {"status": "BLOCKED_SIGNING_CREDENTIALS"}}
    assert decide_verdict(evidence) == "blocked"


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
    }))

    clean = _normalize_clean_machine_artifact(artifact)

    assert clean["status"] == "PASS"


def test_redact_secrets_masks_tokens():
    redacted = redact_secrets("CANVAS_TOKEN=secret APPLE_ID_PASSWORD=pass")
    assert "secret" not in redacted
    assert "pass" not in redacted


def test_write_reports(tmp_path):
    json_path, md_path = write_reports({"verdict": "alpha", "quality": {"status": "SKIP"}}, tmp_path)
    assert json.loads(json_path.read_text())["verdict"] == "alpha"
    markdown = md_path.read_text()
    assert "alpha" in markdown
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
