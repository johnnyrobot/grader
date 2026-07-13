import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

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
    artifact = tmp_path / "Grader.dmg"
    manifest = tmp_path / "latest.json"
    _write_config(config)
    artifact.write_bytes(b"fake signed dmg")
    manifest.write_text(json.dumps({
        "version": "0.1.0",
        "pub_date": "2026-07-13T00:00:00Z",
        "platforms": {
            "darwin-aarch64": {
                "signature": "signed-aarch64",
                "url": "https://example.invalid/releases/Grader.dmg",
            },
            "darwin-x86_64": {
                "signature": "signed-x86",
                "url": "https://example.invalid/releases/Grader.dmg",
            },
        },
    }))

    evidence = collect(
        config_path=config,
        manifest_path=manifest,
        artifact_path=artifact,
        expected_version=None,
    )

    assert evidence["status"] == "PASS"
    assert evidence["failures"] == []
    assert evidence["details"]["artifact_size_bytes"] == len(b"fake signed dmg")


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
