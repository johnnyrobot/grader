import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import scripts.github_release_preflight as preflight


def _proc(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def _write_config(path: Path, repo: str = "johnnyrobot/grader") -> None:
    owner, name = repo.split("/", 1)
    path.write_text(json.dumps({
        "version": "0.1.0",
        "plugins": {
            "updater": {
                "endpoints": [
                    f"https://github.com/{owner}/{name}/releases/latest/download/latest.json"
                ]
            }
        },
    }))


def _write_workflow(path: Path, *, contents_write: bool = True) -> None:
    permissions = "permissions:\n  contents: write\n\n" if contents_write else ""
    path.write_text(
        "name: Release\n\n"
        f"{permissions}"
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - run: uv sync\n"
        "      - run: uv run pytest engine/tests/ -q\n"
        "      - run: npm test -- --run\n"
        "      - run: cargo check\n"
        "      - run: npx tauri build --bundles app --no-sign --ci\n"
        "      - uses: softprops/action-gh-release@v2\n"
    )


def _write_versions(root: Path, version: str) -> None:
    (root / "app" / "src-tauri").mkdir(parents=True)
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "src-tauri" / "tauri.conf.json").write_text(json.dumps({"version": version}))
    (root / "app" / "src-tauri" / "Cargo.toml").write_text(
        f'[package]\nname = "grader-app"\nversion = "{version}"\n'
    )
    (root / "app" / "package.json").write_text(json.dumps({"version": version}))
    (root / "pyproject.toml").write_text(f'[project]\nname = "grader"\nversion = "{version}"\n')


def test_github_release_preflight_passes_with_repo_secrets_and_candidate(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    workflow = tmp_path / "release.yml"
    _write_config(config)
    _write_workflow(workflow)
    _write_versions(tmp_path, "0.1.1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    secrets = [{"name": name} for name in preflight.REQUIRED_ACTIONS_SECRETS]

    def fake_run(command, **_kwargs):
        if command[:3] == ["gh", "auth", "status"]:
            return _proc(command, 0, "Logged in\n")
        if command[:3] == ["gh", "repo", "view"]:
            return _proc(command, 0, json.dumps({"nameWithOwner": "johnnyrobot/grader"}))
        if command[:3] == ["gh", "secret", "list"]:
            return _proc(command, 0, json.dumps(secrets))
        if command[:3] == ["gh", "release", "list"]:
            return _proc(command, 0, json.dumps([]))
        if command[:3] == ["gh", "release", "view"]:
            return _proc(command, 1, "", "not found")
        if command[:3] == ["git", "ls-remote", "--tags"]:
            return _proc(command, 0, "")
        raise AssertionError(f"unexpected command: {command}")

    result = preflight.collect(
        config_path=config,
        workflow_path=workflow,
        candidate_tag="v0.1.1",
        runner=fake_run,
    )

    assert result["status"] == "PASS"
    assert result["read_only"] is True
    assert result["failures"] == []
    assert result["checks"]["candidate_version_consistent"] is True


def test_github_release_preflight_blocks_missing_actions_secrets(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    workflow = tmp_path / "release.yml"
    _write_config(config)
    _write_workflow(workflow)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    def fake_run(command, **_kwargs):
        if command[:3] == ["gh", "auth", "status"]:
            return _proc(command, 0, "Logged in\n")
        if command[:3] == ["gh", "repo", "view"]:
            return _proc(command, 0, json.dumps({"nameWithOwner": "johnnyrobot/grader"}))
        if command[:3] == ["gh", "secret", "list"]:
            return _proc(command, 0, json.dumps([]))
        if command[:3] == ["gh", "release", "list"]:
            return _proc(command, 0, json.dumps([]))
        raise AssertionError(f"unexpected command: {command}")

    result = preflight.collect(config_path=config, workflow_path=workflow, runner=fake_run)

    assert result["status"] == "BLOCKED_GITHUB_RELEASE_PREFLIGHT"
    assert result["checks"]["required_actions_secrets_present"] is False
    assert "APPLE_SIGNING_IDENTITY" in result["failures"][0]
    assert result["read_only"] is True


def test_github_release_preflight_blocks_wrong_updater_repo(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    workflow = tmp_path / "release.yml"
    _write_config(config, repo="someone/else")
    _write_workflow(workflow)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    def fake_run(command, **_kwargs):
        if command[:3] == ["gh", "auth", "status"]:
            return _proc(command, 0)
        if command[:3] == ["gh", "repo", "view"]:
            return _proc(command, 0, json.dumps({"nameWithOwner": "johnnyrobot/grader"}))
        if command[:3] == ["gh", "secret", "list"]:
            return _proc(command, 0, json.dumps([{"name": name} for name in preflight.REQUIRED_ACTIONS_SECRETS]))
        if command[:3] == ["gh", "release", "list"]:
            return _proc(command, 0, json.dumps([]))
        raise AssertionError(f"unexpected command: {command}")

    result = preflight.collect(config_path=config, workflow_path=workflow, runner=fake_run)

    assert result["status"] == "BLOCKED_GITHUB_RELEASE_PREFLIGHT"
    assert result["checks"]["updater_endpoint_matches_repo"] is False
    assert "configured updater endpoint" in result["failures"][0]


def test_github_release_preflight_blocks_existing_candidate_tag(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    workflow = tmp_path / "release.yml"
    _write_config(config)
    _write_workflow(workflow)
    _write_versions(tmp_path, "0.1.1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    def fake_run(command, **_kwargs):
        if command[:3] == ["gh", "auth", "status"]:
            return _proc(command, 0)
        if command[:3] == ["gh", "repo", "view"]:
            return _proc(command, 0, json.dumps({"nameWithOwner": "johnnyrobot/grader"}))
        if command[:3] == ["gh", "secret", "list"]:
            return _proc(command, 0, json.dumps([{"name": name} for name in preflight.REQUIRED_ACTIONS_SECRETS]))
        if command[:3] == ["gh", "release", "list"]:
            return _proc(command, 0, json.dumps([]))
        if command[:3] == ["gh", "release", "view"]:
            return _proc(command, 0, json.dumps({"tagName": "v0.1.1"}))
        if command[:3] == ["git", "ls-remote", "--tags"]:
            return _proc(command, 0, "abc\trefs/tags/v0.1.1\n")
        raise AssertionError(f"unexpected command: {command}")

    result = preflight.collect(
        config_path=config,
        workflow_path=workflow,
        candidate_tag="v0.1.1",
        runner=fake_run,
    )

    assert result["status"] == "BLOCKED_GITHUB_RELEASE_PREFLIGHT"
    assert result["checks"]["candidate_release_absent"] is False
    assert result["checks"]["candidate_git_tag_absent"] is False
    assert any("already exists" in failure for failure in result["failures"])


def test_github_release_preflight_blocks_workflow_without_publish_permission(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    workflow = tmp_path / "release.yml"
    _write_config(config)
    _write_workflow(workflow, contents_write=False)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    def fake_run(command, **_kwargs):
        if command[:3] == ["gh", "auth", "status"]:
            return _proc(command, 0)
        if command[:3] == ["gh", "repo", "view"]:
            return _proc(command, 0, json.dumps({"nameWithOwner": "johnnyrobot/grader"}))
        if command[:3] == ["gh", "secret", "list"]:
            return _proc(command, 0, json.dumps([{"name": name} for name in preflight.REQUIRED_ACTIONS_SECRETS]))
        if command[:3] == ["gh", "release", "list"]:
            return _proc(command, 0, json.dumps([]))
        raise AssertionError(f"unexpected command: {command}")

    result = preflight.collect(config_path=config, workflow_path=workflow, runner=fake_run)

    assert result["status"] == "BLOCKED_GITHUB_RELEASE_PREFLIGHT"
    assert result["checks"]["release_workflow_publish_permission"] is False
    assert any("contents=write" in failure for failure in result["failures"])


def test_github_release_preflight_blocks_workflow_without_release_tests(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    workflow = tmp_path / "release.yml"
    _write_config(config)
    workflow.write_text(
        "name: Release\n\n"
        "permissions:\n  contents: write\n\n"
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - run: npx tauri build --bundles app --no-sign --ci\n"
        "      - uses: softprops/action-gh-release@v2\n"
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    def fake_run(command, **_kwargs):
        if command[:3] == ["gh", "auth", "status"]:
            return _proc(command, 0)
        if command[:3] == ["gh", "repo", "view"]:
            return _proc(command, 0, json.dumps({"nameWithOwner": "johnnyrobot/grader"}))
        if command[:3] == ["gh", "secret", "list"]:
            return _proc(command, 0, json.dumps([{"name": name} for name in preflight.REQUIRED_ACTIONS_SECRETS]))
        if command[:3] == ["gh", "release", "list"]:
            return _proc(command, 0, json.dumps([]))
        raise AssertionError(f"unexpected command: {command}")

    result = preflight.collect(config_path=config, workflow_path=workflow, runner=fake_run)

    assert result["status"] == "BLOCKED_GITHUB_RELEASE_PREFLIGHT"
    assert result["checks"]["release_workflow_test_gates"] is False
    assert any("Python tests" in failure for failure in result["failures"])
