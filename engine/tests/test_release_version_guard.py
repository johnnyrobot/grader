import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.release_version_guard import collect


def _write_version_files(root: Path, version: str) -> dict[str, Path]:
    tauri_config = root / "tauri.conf.json"
    cargo = root / "Cargo.toml"
    package = root / "package.json"
    pyproject = root / "pyproject.toml"
    tauri_config.write_text(json.dumps({"version": version}))
    cargo.write_text(f'[package]\nname = "grader-app"\nversion = "{version}"\n')
    package.write_text(json.dumps({"version": version}))
    pyproject.write_text(f'[project]\nname = "grader"\nversion = "{version}"\n')
    return {
        "tauri_config": tauri_config,
        "tauri_cargo": cargo,
        "frontend_package": package,
        "python_project": pyproject,
    }


def test_release_version_guard_passes_when_tag_matches_all_versions(tmp_path):
    files = _write_version_files(tmp_path, "0.1.0")

    result = collect(tag="v0.1.0", version_files=files)

    assert result["status"] == "PASS"
    assert result["normalized_tag"] == "0.1.0"
    assert result["failures"] == []


def test_release_version_guard_accepts_refs_tags_prefix(tmp_path):
    files = _write_version_files(tmp_path, "0.1.0")

    result = collect(tag="refs/tags/v0.1.0", version_files=files)

    assert result["status"] == "PASS"
    assert result["normalized_tag"] == "0.1.0"


def test_release_version_guard_blocks_mismatched_app_version(tmp_path):
    files = _write_version_files(tmp_path, "0.1.0")

    result = collect(tag="v0.1.1", version_files=files)

    assert result["status"] == "BLOCKED_RELEASE_VERSION"
    assert "tauri_config version '0.1.0' does not match expected '0.1.1'" in result["failures"]


def test_release_version_guard_blocks_missing_expected_version(tmp_path):
    files = _write_version_files(tmp_path, "0.1.0")

    result = collect(version_files=files)

    assert result["status"] == "BLOCKED_RELEASE_VERSION"
    assert "expected release version is missing; pass --tag or --expected-version" in result["failures"]


def test_release_version_guard_checks_runtime_version_surfaces(tmp_path):
    engine_launcher = tmp_path / "start_engine.sh"
    dev_mock = tmp_path / "dev_mock_engine.py"
    engine_launcher.write_text('export GRADER_APP_VERSION="0.1.0"\n')
    dev_mock.write_text('self._send_json({"current_version": "0.1.0"})\n')

    result = collect(
        tag="v0.1.1",
        version_files={
            "engine_launcher": engine_launcher,
            "dev_mock_update_fixture": dev_mock,
        },
    )

    assert result["status"] == "BLOCKED_RELEASE_VERSION"
    assert "engine_launcher version '0.1.0' does not match expected '0.1.1'" in result["failures"]
    assert (
        "dev_mock_update_fixture version '0.1.0' does not match expected '0.1.1'"
        in result["failures"]
    )


def test_release_version_guard_checks_lockfile_version_surfaces(tmp_path):
    package_lock = tmp_path / "package-lock.json"
    cargo_lock = tmp_path / "Cargo.lock"
    package_lock.write_text(
        json.dumps(
            {
                "name": "grader-app",
                "version": "0.1.0",
                "packages": {"": {"name": "grader-app", "version": "0.1.0"}},
            }
        )
    )
    cargo_lock.write_text('[[package]]\nname = "grader-app"\nversion = "0.1.0"\n')

    result = collect(
        tag="v0.1.1",
        version_files={
            "frontend_package_lock": package_lock,
            "tauri_cargo_lock": cargo_lock,
        },
    )

    assert result["status"] == "BLOCKED_RELEASE_VERSION"
    assert "frontend_package_lock version '0.1.0' does not match expected '0.1.1'" in result["failures"]
    assert "tauri_cargo_lock version '0.1.0' does not match expected '0.1.1'" in result["failures"]
