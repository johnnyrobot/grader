import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import scripts.github_release_preflight as preflight


def _proc(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def _write_config(path: Path, repo: str = preflight.RELEASE_REPO) -> None:
    owner, name = repo.split("/", 1)
    path.write_text(json.dumps({
        "version": "0.1.1",
        "plugins": {
            "updater": {
                "endpoints": [
                    f"https://github.com/{owner}/{name}/releases/latest/download/latest.json"
                ]
            }
        },
    }))


def _write_workflow(path: Path, *, hosted_secrets: bool = False) -> None:
    suffix = "\nAPPLE_CERTIFICATE_PASSWORD\nsoftprops/action-gh-release\n" if hosted_secrets else ""
    path.write_text(
        "name: Release Source Verification\n"
        "permissions:\n  contents: read\n"
        "run: uv sync\n"
        "run: uv run pytest engine/tests/ -q\n"
        "run: npm test -- --run\n"
        "run: cargo check\n"
        "run: npx tauri build --bundles app --no-sign --ci\n"
        + suffix
    )


def _write_versions(root: Path, version: str) -> None:
    (root / "app" / "src-tauri").mkdir(parents=True)
    (root / "app").mkdir(exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "app" / "src-tauri" / "tauri.conf.json").write_text(json.dumps({"version": version}))
    (root / "app" / "src-tauri" / "Cargo.toml").write_text(
        f'[package]\nname = "grader-app"\nversion = "{version}"\n'
    )
    (root / "app" / "src-tauri" / "Cargo.lock").write_text(
        f'[[package]]\nname = "grader-app"\nversion = "{version}"\n'
    )
    (root / "app" / "package.json").write_text(json.dumps({"version": version}))
    (root / "app" / "package-lock.json").write_text(
        json.dumps({"version": version, "packages": {"": {"version": version}}})
    )
    (root / "pyproject.toml").write_text(f'[project]\nname = "grader"\nversion = "{version}"\n')
    (root / "scripts" / "start_engine.sh").write_text(f'export GRADER_APP_VERSION="{version}"\n')
    (root / "scripts" / "dev_mock_engine.py").write_text(
        f'self._send_json({{"current_version": "{version}"}})\n'
    )


def _fake_runner(*, release_public: bool = True, release_exists: bool = False, source_tag: bool = False):
    def fake_run(command, **_kwargs):
        if command[:3] == ["gh", "auth", "status"]:
            return _proc(command, 0, "Logged in\n")
        if command[:3] == ["gh", "repo", "view"]:
            repo = command[3]
            return _proc(command, 0, json.dumps({
                "nameWithOwner": repo,
                "visibility": "PUBLIC" if repo == preflight.RELEASE_REPO and release_public else "PRIVATE",
                "isPrivate": not (repo == preflight.RELEASE_REPO and release_public),
            }))
        if command[:3] == ["gh", "release", "list"]:
            return _proc(command, 0, "[]")
        if command[:3] == ["gh", "release", "view"]:
            return _proc(command, 0 if release_exists else 1, json.dumps({"tagName": "v0.1.1"}) if release_exists else "")
        if command[:3] == ["git", "ls-remote", "--tags"]:
            return _proc(command, 0, "abc\trefs/tags/v0.1.1\n" if source_tag else "")
        raise AssertionError(f"unexpected command: {command}")

    return fake_run


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    config = tmp_path / "tauri.conf.json"
    workflow = tmp_path / "release.yml"
    publisher = tmp_path / "publish_local_release.py"
    _write_config(config)
    _write_workflow(workflow)
    publisher.write_text("# publisher\n")
    return config, workflow, publisher


def test_local_release_preflight_passes_for_public_assets_repo(tmp_path, monkeypatch):
    config, workflow, publisher = _paths(tmp_path)
    _write_versions(tmp_path, "0.1.1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    result = preflight.collect(
        config_path=config,
        workflow_path=workflow,
        publisher_path=publisher,
        candidate_tag="v0.1.1",
        runner=_fake_runner(),
    )

    assert result["status"] == "PASS"
    assert result["details"]["required_actions_secrets"] == []
    assert result["checks"]["release_repo_public"] is True
    assert result["checks"]["candidate_version_consistent"] is True


def test_local_release_preflight_blocks_private_release_repo(tmp_path, monkeypatch):
    config, workflow, publisher = _paths(tmp_path)
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "/usr/bin/gh")

    result = preflight.collect(
        config_path=config,
        workflow_path=workflow,
        publisher_path=publisher,
        runner=_fake_runner(release_public=False),
    )

    assert result["status"] == "BLOCKED_GITHUB_RELEASE_PREFLIGHT"
    assert result["checks"]["release_repo_public"] is False
    assert any("must be public" in item for item in result["failures"])


def test_local_release_preflight_blocks_wrong_updater_repo(tmp_path, monkeypatch):
    config, workflow, publisher = _paths(tmp_path)
    _write_config(config, "someone/else")
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "/usr/bin/gh")

    result = preflight.collect(
        config_path=config,
        workflow_path=workflow,
        publisher_path=publisher,
        runner=_fake_runner(),
    )

    assert result["checks"]["updater_endpoint_matches_release_repo"] is False
    assert any("public release repo" in item for item in result["failures"])


def test_local_release_preflight_blocks_hosted_apple_secret_workflow(tmp_path, monkeypatch):
    config, workflow, publisher = _paths(tmp_path)
    _write_workflow(workflow, hosted_secrets=True)
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "/usr/bin/gh")

    result = preflight.collect(
        config_path=config,
        workflow_path=workflow,
        publisher_path=publisher,
        runner=_fake_runner(),
    )

    assert result["checks"]["release_workflow_avoids_hosted_signing_secrets"] is False
    assert any("hosted Apple signing secrets" in item for item in result["failures"])


def test_local_release_preflight_blocks_existing_release_and_source_tag(tmp_path, monkeypatch):
    config, workflow, publisher = _paths(tmp_path)
    _write_versions(tmp_path, "0.1.1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "/usr/bin/gh")

    result = preflight.collect(
        config_path=config,
        workflow_path=workflow,
        publisher_path=publisher,
        candidate_tag="v0.1.1",
        runner=_fake_runner(release_exists=True, source_tag=True),
    )

    assert result["checks"]["candidate_release_absent"] is False
    assert result["checks"]["candidate_source_tag_absent"] is False
    assert sum("already exists" in item for item in result["failures"]) == 2
