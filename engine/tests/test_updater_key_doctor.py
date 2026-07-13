import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.updater_key_doctor import collect_updater_key_diagnostics, redact_secrets


class Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_collect_updater_key_diagnostics_blocks_missing_key(tmp_path):
    result = collect_updater_key_diagnostics(signing_key=tmp_path / "missing.pem", env={})

    assert result["status"] == "BLOCKED_UPDATER_KEY"
    assert result["checks"]["signing_key_present"] is False
    assert "not found" in result["reason"]


def test_collect_updater_key_diagnostics_blocks_missing_password(tmp_path):
    key = tmp_path / "tauri-key.pem"
    key.write_text("fake encrypted key")

    def fake_run(command, **_kwargs):
        return Proc(1, stderr="incorrect updater private key password: Device not configured")

    result = collect_updater_key_diagnostics(signing_key=key, runner=fake_run, env={})

    assert result["status"] == "BLOCKED_UPDATER_KEY"
    assert result["checks"]["signing_key_present"] is True
    assert result["checks"]["password_env_present"] is False
    assert result["checks"]["signing_probe"] is False
    assert "TAURI_SIGNING_PRIVATE_KEY_PASSWORD" in result["reason"]


def test_collect_updater_key_diagnostics_passes_successful_probe(tmp_path):
    key = tmp_path / "tauri-key.pem"
    key.write_text("fake encrypted key")
    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(command)
        assert kwargs["env"]["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"] == "secret"
        return Proc(0, stdout="Signature: signed-update\n")

    result = collect_updater_key_diagnostics(
        signing_key=key,
        runner=fake_run,
        env={"TAURI_SIGNING_PRIVATE_KEY_PASSWORD": "secret"},
    )

    assert result["status"] == "PASS"
    assert result["checks"]["password_env_present"] is True
    assert result["checks"]["signing_probe"] is True
    assert seen[0][:5] == ["cargo", "tauri", "signer", "sign", "--private-key-path"]


def test_redact_secrets_masks_password_values():
    assert "secret" not in redact_secrets("TAURI_SIGNING_PRIVATE_KEY_PASSWORD=secret")


def test_result_is_json_serializable_for_artifact(tmp_path):
    key = tmp_path / "tauri-key.pem"
    key.write_text("fake encrypted key")
    result = collect_updater_key_diagnostics(signing_key=key, runner=lambda *_args, **_kwargs: Proc(0, stdout="Signature: ok\n"))

    json.dumps(result)
