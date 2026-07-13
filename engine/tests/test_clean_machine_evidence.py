from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.clean_machine_evidence import (
    REQUIRED_CHECKS,
    build_evidence,
    collect_checks,
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
