from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.clean_machine_evidence import (
    MANUAL_CHECKS,
    REQUIRED_CHECKS,
    build_evidence,
    collect_checks,
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
    app.mkdir()
    dmg.write_text("fake dmg")

    checks, details = collect_checks(
        app_path=app,
        dmg_path=dmg,
        manual_passes=["first_run_models", "crash_recovery", "keyboard_only_review", "updater_path"],
        runner=_runner,
    )

    assert all(checks.values())
    assert details["codesign_verify"]["command"][:2] == ["codesign", "--verify"]
    assert details["stapler_validate"]["command"][:3] == ["xcrun", "stapler", "validate"]


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


def test_manual_evidence_runbook_includes_every_manual_check():
    runbook = manual_evidence_runbook()

    assert "Grader Clean-Machine Evidence Runbook" in runbook
    for name in MANUAL_CHECKS:
        assert f"### `{name}`" in runbook
    assert "--print-manual-template" in runbook
