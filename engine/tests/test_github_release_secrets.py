import base64
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.github_release_secrets import collect, collect_secret_values


def _proc(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def test_collect_secret_values_reads_env_and_files_without_exposing_values(tmp_path):
    cert = tmp_path / "cert.p12"
    key = tmp_path / "tauri.pem"
    cert.write_bytes(b"fake-p12")
    key.write_text("private updater key")
    env = {
        "APPLE_CERTIFICATE_PASSWORD": "cert-pass",
        "APPLE_ID": "teacher@example.edu",
        "APPLE_APP_SPECIFIC_PASSWORD": "app-pass",
        "APPLE_TEAM_ID": "TEAM123",
        "TAURI_SIGNING_PRIVATE_KEY_PASSWORD": "updater-pass",
    }

    values = collect_secret_values(
        env=env,
        certificate_p12=cert,
        tauri_signing_key=key,
        signing_identity="Developer ID Application: Example (TEAM123)",
    )
    by_name = {item.name: item for item in values}

    assert by_name["APPLE_CERTIFICATE_P12_BASE64"].value == base64.b64encode(b"fake-p12").decode("ascii")
    assert by_name["TAURI_SIGNING_KEY"].value == "private updater key"
    assert by_name["APPLE_SIGNING_IDENTITY"].source == "--signing-identity"

    result = collect(
        env=env,
        certificate_p12=cert,
        tauri_signing_key=key,
        signing_identity="Developer ID Application: Example (TEAM123)",
    )

    assert result["status"] == "PASS"
    assert result["dry_run"] is True
    assert result["missing"] == []
    assert "cert-pass" not in str(result)
    assert result["available"]["APPLE_CERTIFICATE_PASSWORD"]["present"] is True


def test_collect_blocks_when_required_values_are_missing():
    result = collect(env={})

    assert result["status"] == "BLOCKED_GITHUB_RELEASE_SECRETS"
    assert "APPLE_CERTIFICATE_P12_BASE64" in result["missing"]
    assert "TAURI_SIGNING_PRIVATE_KEY_PASSWORD" in result["missing"]
    assert result["applied"] is False


def test_apply_uses_stdin_not_command_arguments(tmp_path):
    cert = tmp_path / "cert.p12"
    key = tmp_path / "tauri.pem"
    cert.write_bytes(b"fake-p12")
    key.write_text("private updater key")
    env = {
        "APPLE_CERTIFICATE_PASSWORD": "cert-pass",
        "APPLE_ID": "teacher@example.edu",
        "APPLE_APP_SPECIFIC_PASSWORD": "app-pass",
        "APPLE_TEAM_ID": "TEAM123",
        "TAURI_SIGNING_PRIVATE_KEY_PASSWORD": "updater-pass",
    }
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs.get("input")))
        return _proc(command, 0, "✓ Set secret\n")

    result = collect(
        env=env,
        certificate_p12=cert,
        tauri_signing_key=key,
        signing_identity="Developer ID Application: Example (TEAM123)",
        apply=True,
        runner=fake_run,
    )

    assert result["status"] == "PASS"
    assert result["applied"] is True
    assert calls
    for command, secret_input in calls:
        assert "--body" not in command
        assert secret_input
