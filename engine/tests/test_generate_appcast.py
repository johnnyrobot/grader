import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts import generate_appcast


class Proc:
    def __init__(self, stdout: str, stderr: str = ""):
        self.stdout = stdout
        self.stderr = stderr


def test_generate_manifest_requires_signing_key_for_release(tmp_path):
    artifact = tmp_path / "Grader.app.tar.gz"
    output = tmp_path / "latest.json"
    artifact.write_bytes(b"signed updater")

    with pytest.raises(SystemExit) as exc:
        generate_appcast.generate_manifest(
            artifact_path=artifact,
            version="0.1.0",
            output=output,
        )

    assert exc.value.code == 1
    assert not output.exists()


def test_sign_file_uses_tauri_v2_private_key_path_command(tmp_path, monkeypatch):
    artifact = tmp_path / "Grader.app.tar.gz"
    key = tmp_path / "tauri-key.pem"
    artifact.write_bytes(b"signed updater")
    key.write_text("fake key")
    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(command)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is True
        assert kwargs["env"] is None
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


def test_sign_file_accepts_tauri_public_signature_output(tmp_path, monkeypatch):
    artifact = tmp_path / "Grader.app.tar.gz"
    key = tmp_path / "tauri-key.pem"
    artifact.write_bytes(b"signed updater")
    key.write_text("fake key")

    def fake_run(command, **_kwargs):
        return Proc("Your file was signed successfully\n\nPublic signature:\nabc123\n")

    monkeypatch.setattr(generate_appcast.subprocess, "run", fake_run)

    assert generate_appcast._sign_file(artifact, key) == "abc123"


def test_sign_file_passes_private_env_without_printing_password(tmp_path, monkeypatch):
    artifact = tmp_path / "Grader.app.tar.gz"
    key = tmp_path / "tauri-key.pem"
    artifact.write_bytes(b"signed updater")
    key.write_text("fake key")

    def fake_run(command, **kwargs):
        assert kwargs["env"]["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"] == "secret"
        return Proc("Signature: abc123\n")

    monkeypatch.setattr(generate_appcast.subprocess, "run", fake_run)

    assert generate_appcast._sign_file(
        artifact,
        key,
        env={"TAURI_SIGNING_PRIVATE_KEY_PASSWORD": "secret"},
    ) == "abc123"


def test_sign_file_hints_when_private_key_password_missing(tmp_path, monkeypatch, capsys):
    artifact = tmp_path / "Grader.app.tar.gz"
    key = tmp_path / "tauri-key.pem"
    artifact.write_bytes(b"signed updater")
    key.write_text("fake key")

    def fake_run(command, **_kwargs):
        raise generate_appcast.subprocess.CalledProcessError(
            returncode=1,
            cmd=command,
            stderr="incorrect updater private key password: Device not configured",
        )

    monkeypatch.setattr(generate_appcast.subprocess, "run", fake_run)

    assert generate_appcast._sign_file(artifact, key) is None
    output = capsys.readouterr().out
    assert "TAURI_SIGNING_PRIVATE_KEY_PASSWORD" in output


def test_generate_manifest_writes_signed_platform_manifest(tmp_path, monkeypatch):
    artifact = tmp_path / "Grader.app.tar.gz"
    output = tmp_path / "latest.json"
    key = tmp_path / "tauri-key.pem"
    artifact.write_bytes(b"signed updater")
    key.write_text("fake key")
    monkeypatch.setattr(generate_appcast, "_sign_file", lambda path, key_path, env=None: "signed-update")

    generate_appcast.generate_manifest(
        artifact_path=artifact,
        version="0.1.0",
        output=output,
        signing_key=key,
        release_url="https://example.invalid/Grader.app.tar.gz",
    )

    manifest = json.loads(output.read_text())
    assert manifest["version"] == "0.1.0"
    assert manifest["platforms"]["darwin-aarch64"]["signature"] == "signed-update"
    assert manifest["platforms"]["darwin-aarch64"]["url"] == "https://example.invalid/Grader.app.tar.gz"
    # Intel is never offered an arm64-only build: it could not launch what it downloaded.
    assert list(manifest["platforms"]) == ["darwin-aarch64"]


def test_load_password_from_keychain_returns_secret_without_logging(monkeypatch):
    def fake_run(command, **kwargs):
        assert command == [
            "security",
            "find-generic-password",
            "-w",
            "-s",
            "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
            "-a",
            "grader-updater",
        ]
        assert kwargs["capture_output"] is True
        return Proc("secret\n")

    monkeypatch.setattr(generate_appcast.subprocess, "run", fake_run)

    assert generate_appcast._load_password_from_keychain(
        "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
        "grader-updater",
    ) == "secret"


def test_generate_manifest_allows_unsigned_smoke_fixture(tmp_path):
    artifact = tmp_path / "Grader.app.tar.gz"
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


def test_package_app_preserves_app_bundle_parent(tmp_path):
    app = tmp_path / "Grader.app"
    executable = app / "Contents" / "MacOS" / "Grader"
    executable.parent.mkdir(parents=True)
    executable.write_text("binary")
    output = tmp_path / "Grader.app.tar.gz"

    bundle_path = generate_appcast._package_app(app, output)

    assert bundle_path == output
    import tarfile

    with tarfile.open(output, "r:gz") as archive:
        assert "Grader.app/Contents/MacOS/Grader" in archive.getnames()


def test_generate_manifest_rejects_dmg_as_updater_artifact(tmp_path):
    artifact = tmp_path / "Grader.dmg"
    artifact.write_bytes(b"signed dmg")

    with pytest.raises(SystemExit) as exc:
        generate_appcast.generate_manifest(
            artifact_path=artifact,
            version="0.1.0",
            output=tmp_path / "latest.json",
            allow_unsigned=True,
        )

    assert exc.value.code == 1
