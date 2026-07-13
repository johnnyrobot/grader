from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import scripts.clean_machine_evidence as clean_machine_evidence
from scripts.clean_machine_evidence import (
    MANUAL_CHECKS,
    REQUIRED_CHECKS,
    build_evidence,
    collect_checks,
    collect_release_provenance,
    manual_evidence_template,
    manual_evidence_runbook,
    validate_manual_evidence,
)


def _runner(command, text, capture_output, check):
    return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")


def test_build_evidence_blocks_until_all_required_checks_pass():
    evidence = build_evidence(checks={"codesign_verify": True})

    assert evidence["status"] == "BLOCKED_CLEAN_MACHINE_EVIDENCE"
    assert "first_run_models" in evidence["missing_checks"]


def test_build_evidence_passes_when_all_required_checks_pass():
    evidence = build_evidence(checks={name: True for name in REQUIRED_CHECKS})

    assert evidence["status"] == "PASS"
    assert evidence["missing_checks"] == []


def test_collect_checks_runs_app_and_dmg_commands(tmp_path):
    app = tmp_path / "Grader.app"
    dmg = tmp_path / "Grader.dmg"
    manifest = tmp_path / "latest.json"
    app.mkdir()
    dmg.write_text("fake dmg")
    manifest.write_text('{"version":"0.1.0","pub_date":"2026-07-13T00:00:00Z","platforms":{"darwin-aarch64":{"signature":"sig","url":"https://example.invalid/Grader.dmg"}}}')

    checks, details = collect_checks(
        app_path=app,
        dmg_path=dmg,
        updater_manifest=manifest,
        expected_version="0.1.0",
        manual_passes=[
            "first_run_models",
            "crash_recovery",
            "keyboard_only_review",
            "computer_use_instructor_sweep",
            "updater_path",
        ],
        runner=_runner,
    )

    assert all(checks.values())
    assert details["app_signature"]["codesign_verify"]["command"][:2] == ["codesign", "--verify"]
    assert details["app_signature_source"] == "app_path"
    assert details["stapler_validate"]["command"][:3] == ["xcrun", "stapler", "validate"]
    assert details["release_provenance"]["app"]["sha256_tree"]
    assert details["release_provenance"]["dmg"]["sha256"]
    assert details["release_provenance"]["updater_manifest"]["ok"] is True


def test_collect_checks_can_use_app_inside_dmg_when_loose_app_signature_fails(tmp_path):
    loose_app = tmp_path / "target" / "Grader.app"
    mounted = tmp_path / "mounted"
    mounted_app = mounted / "Grader.app"
    dmg = tmp_path / "Grader.dmg"
    manifest = tmp_path / "latest.json"
    loose_app.mkdir(parents=True)
    mounted_app.mkdir(parents=True)
    dmg.write_text("fake dmg")
    manifest.write_text('{"version":"0.1.0","pub_date":"2026-07-13T00:00:00Z","platforms":{"darwin-aarch64":{"signature":"sig","url":"https://example.invalid/Grader.dmg"}}}')
    attach_plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>system-entities</key><array><dict><key>mount-point</key><string>{mounted}</string></dict></array></dict></plist>"""

    def runner(command, text, capture_output, check):
        if command[:2] == ["hdiutil", "attach"]:
            return subprocess.CompletedProcess(command, 0, stdout=attach_plist, stderr="")
        if command[:2] == ["hdiutil", "detach"]:
            return subprocess.CompletedProcess(command, 0, stdout="ejected", stderr="")
        if command[:1] == ["codesign"] and str(loose_app) in command:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="loose app failed")
        if command[:1] == ["spctl"] and str(loose_app) in command:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="loose app rejected")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    checks, details = collect_checks(
        app_path=loose_app,
        dmg_path=dmg,
        updater_manifest=manifest,
        expected_version="0.1.0",
        manual_passes=[
            "first_run_models",
            "crash_recovery",
            "keyboard_only_review",
            "computer_use_instructor_sweep",
            "updater_path",
        ],
        runner=runner,
    )

    assert all(checks.values())
    assert details["app_signature"]["codesign_verify"]["ok"] is False
    assert details["app_signature_source"] == "mounted_dmg_app"
    assert details["mounted_dmg_app_signature"]["app_path"] == str(mounted_app)
    assert details["mounted_dmg_app_signature"]["app_signature"]["codesign_verify"]["ok"] is True


def test_validate_manual_evidence_requires_notes_and_files(tmp_path):
    screenshot = tmp_path / "crash-recovery.png"
    screenshot.write_text("fake image bytes")
    evidence = {
        "crash_recovery": {
            "passed": True,
            "performed_at": "2026-07-13T12:00:00Z",
            "tester": "QA",
            "notes": "Killed the engine and used Restart local engine.",
            "evidence_files": ["crash-recovery.png"],
        }
    }

    passed, details = validate_manual_evidence(evidence, evidence_root=tmp_path)

    assert "crash_recovery" in passed
    assert details["crash_recovery"]["ok"] is True
    assert "first_run_models" not in passed
    assert details["first_run_models"]["ok"] is False


def test_validate_manual_evidence_rejects_missing_files(tmp_path):
    evidence = {
        name: {
            "passed": True,
            "performed_at": "2026-07-13T12:00:00Z",
            "tester": "QA",
            "notes": f"{name} checked",
            "evidence_files": [f"{name}.png"],
        }
        for name in MANUAL_CHECKS
    }

    passed, details = validate_manual_evidence(evidence, evidence_root=tmp_path)

    assert passed == []
    assert details["first_run_models"]["missing_files"]


def test_manual_evidence_template_covers_every_manual_check():
    template = manual_evidence_template()

    assert set(template) == set(MANUAL_CHECKS)
    for name, row in template.items():
        assert row["title"]
        assert row["objective"]
        assert row["steps"]
        assert row["expected_result"]
        assert row["passed"] is False
        assert row["notes"] == ""
        assert row["evidence_files"]


def test_validate_manual_evidence_rejects_missing_tester_and_timestamp(tmp_path):
    screenshot = tmp_path / "first-run.png"
    screenshot.write_text("fake image bytes")
    evidence = {
        "first_run_models": {
            "passed": True,
            "notes": "First run completed.",
            "evidence_files": ["first-run.png"],
        }
    }

    passed, details = validate_manual_evidence(evidence, evidence_root=tmp_path)

    assert "first_run_models" not in passed
    assert details["first_run_models"]["ok"] is False
    assert details["first_run_models"]["tester"] == ""
    assert details["first_run_models"]["performed_at"] == ""


def test_validate_manual_evidence_requires_passing_computer_use_sweep_json(tmp_path):
    sweep = tmp_path / "computer_use_instructor_sweep.json"
    sweep.write_text('{"status":"FAIL_COMPUTER_USE_SWEEP"}')
    screenshot = tmp_path / "computer_use_instructor_sweep.png"
    screenshot.write_text("fake image bytes")
    evidence = {
        "computer_use_instructor_sweep": {
            "passed": True,
            "performed_at": "2026-07-13T12:00:00Z",
            "tester": "QA",
            "notes": "Computer Use sweep completed.",
            "evidence_files": ["computer_use_instructor_sweep.json", "computer_use_instructor_sweep.png"],
        }
    }

    passed, details = validate_manual_evidence(evidence, evidence_root=tmp_path)

    assert "computer_use_instructor_sweep" not in passed
    assert details["computer_use_instructor_sweep"]["ok"] is False
    assert details["computer_use_instructor_sweep"]["computer_use_sweep_status"] == "FAIL_COMPUTER_USE_SWEEP"


def test_validate_manual_evidence_accepts_passing_computer_use_sweep_json(tmp_path):
    sweep = tmp_path / "computer_use_instructor_sweep.json"
    sweep.write_text('{"status":"PASS"}')
    screenshot = tmp_path / "computer_use_instructor_sweep.png"
    screenshot.write_text("fake image bytes")
    evidence = {
        "computer_use_instructor_sweep": {
            "passed": True,
            "performed_at": "2026-07-13T12:00:00Z",
            "tester": "QA",
            "notes": "Computer Use sweep completed.",
            "evidence_files": ["computer_use_instructor_sweep.json", "computer_use_instructor_sweep.png"],
        }
    }

    passed, details = validate_manual_evidence(evidence, evidence_root=tmp_path)

    assert "computer_use_instructor_sweep" in passed
    assert details["computer_use_instructor_sweep"]["ok"] is True
    assert details["computer_use_instructor_sweep"]["computer_use_sweep_status"] == "PASS"


def test_manual_evidence_runbook_includes_every_manual_check():
    runbook = manual_evidence_runbook()

    assert "Grader Clean-Machine Evidence Runbook" in runbook
    for name in MANUAL_CHECKS:
        assert f"### `{name}`" in runbook
    assert "--print-manual-template" in runbook
    assert "--updater-manifest" in runbook


def test_collect_release_provenance_requires_artifact_hashes_and_signed_manifest(tmp_path):
    app = tmp_path / "Grader.app"
    dmg = tmp_path / "Grader.dmg"
    manifest = tmp_path / "latest.json"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("plist")
    dmg.write_text("fake dmg")
    manifest.write_text('{"version":"0.1.0","pub_date":"2026-07-13T00:00:00Z","platforms":{"darwin-aarch64":{"signature":"sig","url":"https://example.invalid/Grader.dmg"},"darwin-x86_64":{"signature":"sig","url":"https://example.invalid/Grader.dmg"}}}')

    ok, details = collect_release_provenance(
        app_path=app,
        dmg_path=dmg,
        updater_manifest=manifest,
        expected_version="0.1.0",
    )

    assert ok is True
    assert details["app"]["sha256_tree"]
    assert details["dmg"]["sha256"]
    assert details["updater_manifest"]["version"] == "0.1.0"


def test_collect_release_provenance_rejects_unsigned_or_wrong_version_manifest(tmp_path):
    app = tmp_path / "Grader.app"
    dmg = tmp_path / "Grader.dmg"
    manifest = tmp_path / "latest.json"
    app.mkdir()
    dmg.write_text("fake dmg")
    manifest.write_text('{"version":"0.0.9","pub_date":"2026-07-13T00:00:00Z","platforms":{"darwin-aarch64":{"signature":"","url":"https://example.invalid/Grader.dmg"}}}')

    ok, details = collect_release_provenance(
        app_path=app,
        dmg_path=dmg,
        updater_manifest=manifest,
        expected_version="0.1.0",
    )

    assert ok is False
    assert details["missing_provenance"] == ["updater_manifest"]
    assert details["updater_manifest"]["ok"] is False


def test_collect_release_provenance_rejects_artifacts_older_than_source_commit(tmp_path, monkeypatch):
    app = tmp_path / "Grader.app"
    dmg = tmp_path / "Grader.dmg"
    manifest = tmp_path / "latest.json"
    app.mkdir()
    dmg.write_text("fake dmg")
    manifest.write_text('{"version":"0.1.0","pub_date":"2026-07-13T00:00:00Z","platforms":{"darwin-aarch64":{"signature":"sig","url":"https://example.invalid/Grader.dmg"},"darwin-x86_64":{"signature":"sig","url":"https://example.invalid/Grader.dmg"}}}')
    monkeypatch.setattr(
        clean_machine_evidence,
        "_git_source_revision",
        lambda: {
            "available": True,
            "commit": "abc123",
            "commit_time_epoch": 2_000_000_000,
            "commit_time": "2033-05-18T03:33:20+00:00",
        },
    )

    ok, details = collect_release_provenance(
        app_path=app,
        dmg_path=dmg,
        updater_manifest=manifest,
        expected_version="0.1.0",
    )

    assert ok is False
    assert "source_freshness" in details["missing_provenance"]
    assert details["source_freshness"]["stale_artifacts"]
