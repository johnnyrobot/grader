import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts import generate_appcast


class Proc:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.stderr = ""


def test_generate_manifest_requires_signing_key_for_release(tmp_path):
    artifact = tmp_path / "Grader.dmg"
    output = tmp_path / "latest.json"
    artifact.write_bytes(b"signed dmg")

    with pytest.raises(SystemExit) as exc:
        generate_appcast.generate_manifest(
            artifact_path=artifact,
            version="0.1.0",
            output=output,
        )

    assert exc.value.code == 1
    assert not output.exists()


def test_sign_file_uses_tauri_v2_private_key_path_command(tmp_path, monkeypatch):
    artifact = tmp_path / "Grader.dmg"
    key = tmp_path / "tauri-key.pem"
    artifact.write_bytes(b"signed dmg")
    key.write_text("fake key")
    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(command)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is True
        return Proc("Signature: abc123\n")

    monkeypatch.setattr(generate_appcast.subprocess, "run", fake_run)

    signature = generate_appcast._sign_file(artifact, key)

    assert signature == "abc123"
    assert seen == [[
        "cargo",
        "tauri",
        "signer",
        "sign",
        "--private-key-path",
        str(key),
        str(artifact),
    ]]


def test_generate_manifest_writes_signed_platform_manifest(tmp_path, monkeypatch):
    artifact = tmp_path / "Grader.dmg"
    output = tmp_path / "latest.json"
    key = tmp_path / "tauri-key.pem"
    artifact.write_bytes(b"signed dmg")
    key.write_text("fake key")
    monkeypatch.setattr(generate_appcast, "_sign_file", lambda path, key_path: "signed-update")

    generate_appcast.generate_manifest(
        artifact_path=artifact,
        version="0.1.0",
        output=output,
        signing_key=key,
        release_url="https://example.invalid/Grader.dmg",
    )

    manifest = json.loads(output.read_text())
    assert manifest["version"] == "0.1.0"
    assert manifest["platforms"]["darwin-aarch64"]["signature"] == "signed-update"
    assert manifest["platforms"]["darwin-x86_64"]["url"] == "https://example.invalid/Grader.dmg"


def test_generate_manifest_allows_unsigned_smoke_fixture(tmp_path):
    artifact = tmp_path / "Grader.dmg"
    output = tmp_path / "latest.json"
    artifact.write_bytes(b"unsigned fixture")

    generate_appcast.generate_manifest(
        artifact_path=artifact,
        version="0.1.0",
        output=output,
        allow_unsigned=True,
    )

    manifest = json.loads(output.read_text())
    assert manifest["platforms"]["darwin-aarch64"]["signature"] == ""


def test_zip_app_preserves_app_bundle_parent(tmp_path):
    app = tmp_path / "Grader.app"
    executable = app / "Contents" / "MacOS" / "Grader"
    executable.parent.mkdir(parents=True)
    executable.write_text("binary")
    output = tmp_path / "Grader.zip"

    zip_path = generate_appcast._zip_app(app, output)

    assert zip_path == output
    import zipfile

    with zipfile.ZipFile(output) as archive:
        assert "Grader.app/Contents/MacOS/Grader" in archive.namelist()
