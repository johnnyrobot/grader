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


def test_collect_secret_values_reads_updater_password_from_keychain(tmp_path):
    cert = tmp_path / "cert.p12"
    key = tmp_path / "tauri.pem"
    cert.write_bytes(b"fake-p12")
    key.write_text("private updater key")
    env = {
        "APPLE_CERTIFICATE_PASSWORD": "cert-pass",
        "APPLE_ID": "teacher@example.edu",
        "APPLE_APP_SPECIFIC_PASSWORD": "app-pass",
        "APPLE_TEAM_ID": "TEAM123",
    }

    def fake_run(command, **_kwargs):
        assert command == [
            "security",
            "find-generic-password",
            "-w",
            "-s",
            "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
            "-a",
            "grader-updater",
        ]
        return _proc(command, 0, "updater-pass-from-keychain\n")

    values = collect_secret_values(
        env=env,
        certificate_p12=cert,
        tauri_signing_key=key,
        signing_identity="Developer ID Application: Example (TEAM123)",
        updater_password_keychain_service="TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
        updater_password_keychain_account="grader-updater",
        runner=fake_run,
    )
    by_name = {item.name: item for item in values}

    assert by_name["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"].value == "updater-pass-from-keychain"
    assert by_name["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"].source == (
        "keychain:TAURI_SIGNING_PRIVATE_KEY_PASSWORD:grader-updater"
    )

    result = collect(
        env=env,
        certificate_p12=cert,
        tauri_signing_key=key,
        signing_identity="Developer ID Application: Example (TEAM123)",
        updater_password_keychain_service="TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
        updater_password_keychain_account="grader-updater",
        runner=fake_run,
    )

    assert result["status"] == "PASS"
    assert result["missing"] == []
    assert "updater-pass-from-keychain" not in str(result)
    assert result["available"]["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"]["source"] == (
        "keychain:TAURI_SIGNING_PRIVATE_KEY_PASSWORD:grader-updater"
    )
    assert result["diagnostics"]["updater_password_keychain"]["ok"] is True
    assert "updater-pass-from-keychain" not in str(result["diagnostics"])


def test_collect_records_failed_keychain_lookup_without_secret_values(tmp_path):
    key = tmp_path / "tauri.pem"
    key.write_text("private updater key")

    def fake_run(command, **_kwargs):
        return _proc(command, 44, "", "The specified item could not be found in the keychain.\n")

    result = collect(
        env={},
        tauri_signing_key=key,
        updater_password_keychain_service="TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
        updater_password_keychain_account="grader-updater",
        runner=fake_run,
    )

    diagnostic = result["diagnostics"]["updater_password_keychain"]
    assert result["status"] == "BLOCKED_GITHUB_RELEASE_SECRETS"
    assert "TAURI_SIGNING_PRIVATE_KEY_PASSWORD" in result["missing"]
    assert diagnostic["ok"] is False
    assert diagnostic["returncode"] == 44
    assert diagnostic["service"] == "TAURI_SIGNING_PRIVATE_KEY_PASSWORD"
    assert diagnostic["account"] == "grader-updater"
    assert "could not be found" in diagnostic["stderr"]
    assert "private updater key" not in str(result)


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
