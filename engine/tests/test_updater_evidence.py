import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import scripts.updater_evidence as updater_evidence
from scripts.updater_evidence import collect


def _write_config(path: Path) -> None:
    path.write_text(json.dumps({
        "version": "0.1.0",
        "plugins": {
            "updater": {
                "active": True,
                "endpoints": ["https://example.invalid/latest.json"],
                "pubkey": "public-key",
            }
        },
    }))


def test_updater_evidence_blocks_without_manifest(tmp_path):
    config = tmp_path / "tauri.conf.json"
    _write_config(config)

    evidence = collect(
        config_path=config,
        manifest_path=tmp_path / "missing-latest.json",
        artifact_path=None,
        expected_version=None,
    )

    assert evidence["status"] == "BLOCKED_UPDATER_EVIDENCE"
    assert "missing updater manifest latest.json" in evidence["failures"]


def test_updater_evidence_passes_signed_manifest_and_artifact(tmp_path):
    config = tmp_path / "tauri.conf.json"
    artifact = tmp_path / "Grader.app.tar.gz"
    manifest = tmp_path / "latest.json"
    _write_config(config)
    app = tmp_path / "Grader.app"
    executable = app / "Contents" / "MacOS" / "Grader"
    executable.parent.mkdir(parents=True)
    executable.write_text("binary")
    with tarfile.open(artifact, "w:gz") as archive:
        archive.add(app, arcname=app.name)
    manifest.write_text(json.dumps({
        "version": "0.1.0",
        "pub_date": "2026-07-13T00:00:00Z",
        "platforms": {
            "darwin-aarch64": {
                "signature": "signed-aarch64",
                "url": "https://example.invalid/releases/Grader.app.tar.gz",
            },
            "darwin-x86_64": {
                "signature": "signed-x86",
                "url": "https://example.invalid/releases/Grader.app.tar.gz",
            },
        },
    }))

    def fake_run(command, **_kwargs):
        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        assert command[0] in {"codesign", "spctl"}
        return Proc()

    evidence = collect(
        config_path=config,
        manifest_path=manifest,
        artifact_path=artifact,
        expected_version=None,
        runner=fake_run,
    )

    assert evidence["status"] == "PASS"
    assert evidence["failures"] == []
    assert evidence["details"]["artifact_size_bytes"] == artifact.stat().st_size
    assert evidence["details"]["artifact_signature_checks"]["bundled_app"]["codesign_verify"]["ok"] is True


def test_updater_evidence_blocks_when_local_artifact_signature_fails(tmp_path):
    config = tmp_path / "tauri.conf.json"
    artifact = tmp_path / "Grader.app.tar.gz"
    manifest = tmp_path / "latest.json"
    _write_config(config)
    app = tmp_path / "Grader.app"
    executable = app / "Contents" / "MacOS" / "Grader"
    executable.parent.mkdir(parents=True)
    executable.write_text("binary")
    with tarfile.open(artifact, "w:gz") as archive:
        archive.add(app, arcname=app.name)
    manifest.write_text(json.dumps({
        "version": "0.1.0",
        "pub_date": "2026-07-13T00:00:00Z",
        "platforms": {
            "darwin-aarch64": {"signature": "signed", "url": "https://example.invalid/Grader.app.tar.gz"},
            "darwin-x86_64": {"signature": "signed", "url": "https://example.invalid/Grader.app.tar.gz"},
        },
    }))

    def fake_run(command, **_kwargs):
        class Proc:
            stdout = ""
            stderr = ""

            def __init__(self, returncode: int, stderr: str = ""):
                self.returncode = returncode
                self.stderr = stderr

        if command[0] == "codesign":
            return Proc(1, "code has no resources but signature indicates they must be present")
        if command[0] == "spctl":
            return Proc(0)
        raise AssertionError(command)

    evidence = collect(
        config_path=config,
        manifest_path=manifest,
        artifact_path=artifact,
        expected_version=None,
        runner=fake_run,
    )

    assert evidence["status"] == "BLOCKED_UPDATER_EVIDENCE"
    assert "app signature check failed: codesign_verify" in evidence["failures"]


def test_updater_evidence_blocks_unsigned_manifest(tmp_path):
    config = tmp_path / "tauri.conf.json"
    manifest = tmp_path / "latest.json"
    _write_config(config)
    manifest.write_text(json.dumps({
        "version": "0.1.0",
        "pub_date": "2026-07-13T00:00:00Z",
        "platforms": {
            "darwin-aarch64": {"signature": "", "url": "https://example.invalid/Grader.dmg"},
            "darwin-x86_64": {"signature": "", "url": "https://example.invalid/Grader.dmg"},
        },
    }))

    evidence = collect(
        config_path=config,
        manifest_path=manifest,
        artifact_path=None,
        expected_version=None,
    )

    assert evidence["status"] == "BLOCKED_UPDATER_EVIDENCE"
    assert "manifest platform darwin-aarch64 is missing signature" in evidence["failures"]


def test_updater_evidence_blocks_artifacts_older_than_source_commit(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    artifact = tmp_path / "Grader.app.tar.gz"
    manifest = tmp_path / "latest.json"
    _write_config(config)
    app = tmp_path / "Grader.app"
    executable = app / "Contents" / "MacOS" / "Grader"
    executable.parent.mkdir(parents=True)
    executable.write_text("binary")
    with tarfile.open(artifact, "w:gz") as archive:
        archive.add(app, arcname=app.name)
    manifest.write_text(json.dumps({
        "version": "0.1.0",
        "pub_date": "2026-07-13T00:00:00Z",
        "platforms": {
            "darwin-aarch64": {"signature": "signed", "url": "https://example.invalid/Grader.app.tar.gz"},
            "darwin-x86_64": {"signature": "signed", "url": "https://example.invalid/Grader.app.tar.gz"},
        },
    }))
    monkeypatch.setattr(
        updater_evidence,
        "_git_source_revision",
        lambda: {
            "available": True,
            "commit": "abc123",
            "commit_time_epoch": 2_000_000_000,
            "commit_time": "2033-05-18T03:33:20+00:00",
        },
    )

    def fake_run(command, **_kwargs):
        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        assert command[0] in {"codesign", "spctl"}
        return Proc()

    evidence = collect(
        config_path=config,
        manifest_path=manifest,
        artifact_path=artifact,
        expected_version=None,
        runner=fake_run,
    )

    assert evidence["status"] == "BLOCKED_UPDATER_EVIDENCE"
    assert any("predate the current git commit" in failure for failure in evidence["failures"])
    assert evidence["details"]["source_freshness"]["stale_artifacts"]
