import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.signing_doctor import collect_signing_diagnostics, redact_secrets


class Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_config(tmp_path: Path, *, hardened: bool = True, updater: bool = True, pubkey: bool = True) -> Path:
    config = tmp_path / "tauri.conf.json"
    config.write_text(json.dumps({
        "bundle": {"macOS": {"hardenedRuntime": hardened}},
        "plugins": {"updater": {"active": updater, "pubkey": "PUBKEY" if pubkey else ""}},
    }))
    return config


def test_collect_signing_diagnostics_blocks_when_certificate_missing(tmp_path):
    config = _write_config(tmp_path)

    def fake_run(command, **_kwargs):
        if command[:2] == ["security", "find-identity"]:
            return Proc(0, stdout="0 valid identities found")
        raise AssertionError(f"unexpected command: {command}")

    result = collect_signing_diagnostics(config_path=config, runner=fake_run)

    assert result["status"] == "BLOCKED_SIGNING_CREDENTIALS"
    assert result["checks"]["developer_id_certificate"] is False
    assert "Developer ID Application" in result["reason"]
    assert result["remediation"]


def test_collect_signing_diagnostics_blocks_when_codesign_key_unavailable(tmp_path):
    config = _write_config(tmp_path)

    def fake_run(command, **_kwargs):
        if command[:2] == ["security", "find-identity"]:
            return Proc(0, stdout='1) ABC "Developer ID Application: Example (TEAMID)"')
        if command[:1] == ["codesign"]:
            return Proc(1, stderr="errSecInternalComponent")
        if command[:2] == ["xcrun", "notarytool"]:
            return Proc(0)
        raise AssertionError(f"unexpected command: {command}")

    result = collect_signing_diagnostics(config_path=config, runner=fake_run)

    assert result["status"] == "BLOCKED_SIGNING_CREDENTIALS"
    assert result["checks"]["codesign_private_key_access"] is False
    assert result["checks"]["notary_keychain_profile"] is True
    assert "codesign cannot use" in result["reason"]
    assert "errSecInternalComponent" in result["output"]


def test_collect_signing_diagnostics_blocks_when_notary_profile_missing(tmp_path):
    config = _write_config(tmp_path)

    def fake_run(command, **_kwargs):
        if command[:2] == ["security", "find-identity"]:
            return Proc(0, stdout='1) ABC "Developer ID Application: Example (TEAMID)"')
        if command[:1] == ["codesign"]:
            return Proc(0)
        if command[:2] == ["xcrun", "notarytool"]:
            return Proc(69, stderr="No Keychain password item found for profile: grader")
        raise AssertionError(f"unexpected command: {command}")

    result = collect_signing_diagnostics(config_path=config, runner=fake_run)

    assert result["status"] == "BLOCKED_SIGNING_CREDENTIALS"
    assert result["checks"]["codesign_private_key_access"] is True
    assert result["checks"]["notary_keychain_profile"] is False
    assert "notarytool keychain profile" in result["reason"]


def test_collect_signing_diagnostics_skip_notary_allows_sign_only_probe(tmp_path):
    config = _write_config(tmp_path)
    seen: list[list[str]] = []

    def fake_run(command, **_kwargs):
        seen.append(command)
        if command[:2] == ["security", "find-identity"]:
            return Proc(0, stdout='1) ABC "Developer ID Application: Example (TEAMID)"')
        if command[:1] == ["codesign"]:
            return Proc(0)
        raise AssertionError(f"unexpected command: {command}")

    result = collect_signing_diagnostics(config_path=config, require_notary=False, runner=fake_run)

    assert result["status"] == "PASS"
    assert result["checks"]["notary_required"] is False
    assert result["checks"]["notary_keychain_profile"] is True
    assert not any(command[:2] == ["xcrun", "notarytool"] for command in seen)


def test_collect_signing_diagnostics_fails_incomplete_release_config(tmp_path):
    config = _write_config(tmp_path, pubkey=False)

    def fake_run(command, **_kwargs):
        if command[:2] == ["security", "find-identity"]:
            return Proc(0, stdout='1) ABC "Developer ID Application: Example (TEAMID)"')
        if command[:1] == ["codesign"]:
            return Proc(0)
        if command[:2] == ["xcrun", "notarytool"]:
            return Proc(0)
        raise AssertionError(f"unexpected command: {command}")

    result = collect_signing_diagnostics(config_path=config, runner=fake_run)

    assert result["status"] == "FAIL"
    assert result["missing_checks"] == ["updater_pubkey_present"]


def test_collect_signing_diagnostics_passes_when_all_probes_pass(tmp_path):
    config = _write_config(tmp_path)

    def fake_run(command, **_kwargs):
        if command[:2] == ["security", "find-identity"]:
            return Proc(0, stdout='1) ABC "Developer ID Application: Example (TEAMID)"')
        if command[:1] == ["codesign"]:
            return Proc(0)
        if command[:2] == ["xcrun", "notarytool"]:
            return Proc(0)
        raise AssertionError(f"unexpected command: {command}")

    result = collect_signing_diagnostics(config_path=config, runner=fake_run)

    assert result["status"] == "PASS"
    assert result["checks"]["developer_id_certificate"] is True
    assert result["checks"]["codesign_private_key_access"] is True
    assert result["checks"]["notary_keychain_profile"] is True


def test_redact_secrets_masks_env_and_bearer_values():
    redacted = redact_secrets("APPLE_ID_PASSWORD=secret CANVAS_TOKEN=abc Bearer long.token")

    assert "secret" not in redacted
    assert "abc" not in redacted
    assert "long.token" not in redacted
