import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.bump_release_version import apply_plan, collect_plan


def _write_release_surfaces(root: Path, version: str = "0.1.0") -> None:
    (root / "app/src-tauri").mkdir(parents=True)
    (root / "app").mkdir(exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "app/src-tauri/tauri.conf.json").write_text(
        json.dumps({"productName": "Grader", "version": version}, indent=2) + "\n"
    )
    (root / "app/package.json").write_text(
        json.dumps({"name": "grader-app", "version": version}, indent=2) + "\n"
    )
    (root / "app/src-tauri/Cargo.toml").write_text(
        f'[package]\nname = "grader"\nversion = "{version}"\n\n[dependencies]\nserde = "1"\n'
    )
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "grader"\nversion = "{version}"\n'
    )
    (root / "scripts/start_engine.sh").write_text(
        f'#!/bin/bash\nexport GRADER_APP_VERSION="{version}"\n'
    )
    (root / "scripts/dev_mock_engine.py").write_text(
        f'self._send_json({{"update_available": False, "current_version": "{version}"}})\n'
    )


def test_collect_plan_reports_all_release_surfaces_without_writing(tmp_path):
    _write_release_surfaces(tmp_path, "0.1.0")

    result = collect_plan(target_version="v0.1.1", root=tmp_path)

    assert result["status"] == "PASS"
    assert result["read_only"] is True
    assert result["target_version"] == "0.1.1"
    assert set(result["surfaces"]) == {
        "tauri_config",
        "frontend_package",
        "tauri_cargo",
        "python_project",
        "engine_launcher",
        "dev_mock_update_fixture",
    }
    assert all(row["will_change"] for row in result["surfaces"].values())
    assert '"version": "0.1.0"' in (tmp_path / "app/package.json").read_text()


def test_apply_plan_updates_all_release_surfaces(tmp_path):
    _write_release_surfaces(tmp_path, "0.1.0")

    result = apply_plan(target_version="0.1.1", root=tmp_path)

    assert result["status"] == "APPLIED"
    assert result["applied"] is True
    assert json.loads((tmp_path / "app/src-tauri/tauri.conf.json").read_text())["version"] == "0.1.1"
    assert json.loads((tmp_path / "app/package.json").read_text())["version"] == "0.1.1"
    assert 'version = "0.1.1"' in (tmp_path / "app/src-tauri/Cargo.toml").read_text()
    assert 'version = "0.1.1"' in (tmp_path / "pyproject.toml").read_text()
    assert 'GRADER_APP_VERSION="0.1.1"' in (tmp_path / "scripts/start_engine.sh").read_text()
    assert '"current_version": "0.1.1"' in (tmp_path / "scripts/dev_mock_engine.py").read_text()


def test_collect_plan_blocks_no_op_without_allow_same(tmp_path):
    _write_release_surfaces(tmp_path, "0.1.1")

    result = collect_plan(target_version="0.1.1", root=tmp_path)

    assert result["status"] == "BLOCKED_VERSION_BUMP"
    assert "all version surfaces already report 0.1.1" in result["failures"][0]


def test_collect_plan_allows_no_op_for_idempotent_release_docs(tmp_path):
    _write_release_surfaces(tmp_path, "0.1.1")

    result = collect_plan(target_version="0.1.1", root=tmp_path, allow_same=True)

    assert result["status"] == "PASS"
    assert all(not row["will_change"] for row in result["surfaces"].values())


def test_collect_plan_warns_when_current_surfaces_are_already_inconsistent(tmp_path):
    _write_release_surfaces(tmp_path, "0.1.0")
    package = tmp_path / "app/package.json"
    payload = json.loads(package.read_text())
    payload["version"] = "0.1.9"
    package.write_text(json.dumps(payload) + "\n")

    result = collect_plan(target_version="0.1.1", root=tmp_path)

    assert result["status"] == "PASS"
    assert "current version surfaces are already inconsistent" in result["warnings"][0]


def test_collect_plan_rejects_non_semver_targets(tmp_path):
    _write_release_surfaces(tmp_path, "0.1.0")

    try:
        collect_plan(target_version="next", root=tmp_path)
    except ValueError as exc:
        assert "semantic version" in str(exc)
    else:
        raise AssertionError("expected non-semver target to be rejected")
