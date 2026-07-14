import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import scripts.publish_local_release as publish_local_release


def _proc(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def _fixture(tmp_path: Path):
    dmg = tmp_path / "Grader.dmg"
    manifest = tmp_path / "latest.json"
    dmg.write_bytes(b"signed dmg")
    manifest.write_text(json.dumps({
        "version": "0.1.1",
        "platforms": {
            platform: {
                "signature": "signed",
                "url": "https://github.com/johnnyrobot/grader-releases/releases/download/v0.1.1/Grader.dmg",
            }
            for platform in publish_local_release.REQUIRED_PLATFORMS
        },
    }))
    return dmg, manifest


def _runner(commands):
    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[:3] == ["gh", "repo", "view"]:
            return _proc(command, 0, json.dumps({
                "nameWithOwner": publish_local_release.RELEASE_REPO,
                "visibility": "PUBLIC",
                "isPrivate": False,
            }))
        if command[:3] == ["gh", "release", "view"]:
            return _proc(command, 1, "", "not found")
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return _proc(command, 0, "abc123\n")
        if command[:3] == ["gh", "release", "create"]:
            return _proc(command, 0, "https://github.com/johnnyrobot/grader-releases/releases/tag/v0.1.1\n")
        raise AssertionError(command)

    return fake_run


def test_collect_passes_for_signed_public_channel_manifest(tmp_path, monkeypatch):
    dmg, manifest = _fixture(tmp_path)
    commands = []
    monkeypatch.setattr(publish_local_release.shutil, "which", lambda _name: "/usr/bin/gh")

    result = publish_local_release.collect(
        dmg=dmg,
        manifest=manifest,
        version="0.1.1",
        tag="v0.1.1",
        runner=_runner(commands),
    )

    assert result["status"] == "PASS"
    assert result["source_commit"] == "abc123"
    assert result["checks"]["manifest_platforms_valid"] is True


def test_collect_blocks_wrong_release_url(tmp_path, monkeypatch):
    dmg, manifest = _fixture(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["platforms"]["darwin-aarch64"]["url"] = "https://example.invalid/Grader.dmg"
    manifest.write_text(json.dumps(payload))
    monkeypatch.setattr(publish_local_release.shutil, "which", lambda _name: "/usr/bin/gh")

    result = publish_local_release.collect(
        dmg=dmg,
        manifest=manifest,
        version="0.1.1",
        tag="v0.1.1",
        runner=_runner([]),
    )

    assert result["status"] == "BLOCKED_LOCAL_RELEASE_PUBLICATION"
    assert any("wrong release URL" in item for item in result["failures"])


def test_publish_creates_release_with_checksums_and_provenance(tmp_path, monkeypatch):
    dmg, manifest = _fixture(tmp_path)
    commands = []
    runner = _runner(commands)
    monkeypatch.setattr(publish_local_release.shutil, "which", lambda _name: "/usr/bin/gh")
    result = publish_local_release.collect(
        dmg=dmg,
        manifest=manifest,
        version="0.1.1",
        tag="v0.1.1",
        runner=runner,
    )

    applied = publish_local_release.publish(result, out_dir=tmp_path / "publication", runner=runner)

    assert applied["status"] == "PASS"
    assert (tmp_path / "publication" / "SHA256SUMS").is_file()
    assert (tmp_path / "publication" / "release-provenance.json").is_file()
    create = next(command for command in commands if command[:3] == ["gh", "release", "create"])
    assert "--latest" in create
    assert str(dmg) in create
