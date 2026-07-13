import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.release_evidence_package import build_package


def test_build_package_copies_artifacts_and_writes_runbook_template(tmp_path):
    dmg = tmp_path / "Grader.dmg"
    manifest = tmp_path / "latest.json"
    updater = tmp_path / "updater.json"
    clean = tmp_path / "clean-machine.json"
    readiness = tmp_path / "release-readiness.json"
    signing = tmp_path / "signing.json"
    release_version = tmp_path / "release-version.json"
    updater_channel = tmp_path / "updater-channel-current.json"
    updater_key = tmp_path / "updater-key.json"
    dmg.write_bytes(b"signed dmg")
    manifest.write_text('{"version":"0.1.0","platforms":{"darwin-aarch64":{"signature":""}}}')
    updater.write_text(json.dumps({
        "status": "BLOCKED_UPDATER_EVIDENCE",
        "failures": ["manifest platform darwin-aarch64 is missing signature"],
    }))
    clean.write_text(json.dumps({
        "status": "BLOCKED_CLEAN_MACHINE_EVIDENCE",
        "missing_checks": ["updater_path"],
        "checks": {"codesign_verify": True, "updater_path": False},
    }))
    readiness.write_text(json.dumps({
        "verdict": "blocked",
        "blockers": [{"gate": "updater", "status": "BLOCKED_UPDATER_EVIDENCE", "summary": "unsigned"}],
        "e2e_matrix_summary": {"blocking": 1, "passing": 15},
    }))
    signing.write_text(json.dumps({"status": "PASS"}))
    release_version.write_text(json.dumps({"status": "PASS"}))
    updater_channel.write_text(json.dumps({"status": "BLOCKED_UPDATER_CHANNEL"}))
    updater_key.write_text(json.dumps({"status": "BLOCKED_UPDATER_KEY"}))

    out = tmp_path / "handoff"
    package = build_package(
        out_dir=out,
        dmg=dmg,
        updater_manifest=manifest,
        updater_evidence=updater,
        clean_machine_evidence=clean,
        readiness_report=readiness,
        signing_evidence=signing,
        release_version_evidence=release_version,
        updater_channel_evidence=updater_channel,
        updater_key_evidence=updater_key,
    )

    assert package["status"] == "PASS_PACKAGE_READY"
    assert package["summary"]["readiness_verdict"] == "blocked"
    assert package["summary"]["updater_status"] == "BLOCKED_UPDATER_EVIDENCE"
    assert (out / "release-artifacts" / "Grader.dmg").exists()
    assert (out / "release-artifacts" / "latest.json").exists()
    assert (out / "evidence" / "updater.json").exists()
    assert (out / "evidence" / "release-version.json").exists()
    assert (out / "evidence" / "updater-channel-current.json").exists()
    assert (out / "evidence" / "updater-key.json").exists()
    assert "first_run_models" in (out / "clean-machine-runbook.md").read_text()
    template = json.loads((out / "clean-machine-manual-evidence.json").read_text())
    assert template["computer_use_instructor_sweep"]["assertions"]
    readme = (out / "README.md").read_text()
    assert "TAURI_SIGNING_PRIVATE_KEY_PASSWORD" in readme
    assert "release_version_guard.py" in readme
    assert "updater_channel_preflight.py" in readme
    manifest_payload = json.loads((out / "release-evidence-package.json").read_text())
    assert manifest_payload["files"]["dmg"]["sha256"] == package["files"]["dmg"]["sha256"]


def test_build_package_can_leave_release_artifacts_in_place(tmp_path):
    dmg = tmp_path / "Grader.dmg"
    dmg.write_bytes(b"signed dmg")

    out = tmp_path / "handoff"
    package = build_package(out_dir=out, dmg=dmg, copy_artifacts=False)

    assert package["files"]["dmg"]["present"] is True
    assert package["files"]["dmg"]["copied_to"] is None
    assert not (out / "release-artifacts" / "Grader.dmg").exists()
    assert (out / "clean-machine-runbook.md").exists()
