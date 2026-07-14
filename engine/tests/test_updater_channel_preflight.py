import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import scripts.updater_channel_preflight as preflight


def _write_config(path: Path) -> None:
    path.write_text(json.dumps({
        "version": "0.1.0",
        "plugins": {
            "updater": {
                "endpoints": ["https://github.com/johnnyrobot/grader-releases/releases/latest/download/latest.json"],
            }
        },
    }))


def test_updater_channel_preflight_passes_newer_signed_manifest(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    _write_config(config)

    class ManifestResponse:
        status_code = 200
        url = "https://github.com/johnnyrobot/grader-releases/releases/latest/download/latest.json"

        def json(self):
            return {
                "version": "0.1.1",
                "pub_date": "2026-07-13T00:00:00Z",
                "platforms": {
                    "darwin-aarch64": {
                        "signature": "signed",
                        "url": "https://github.com/johnnyrobot/grader-releases/releases/download/v0.1.1/Grader.app.tar.gz",
                    },
                    "darwin-x86_64": {
                        "signature": "signed",
                        "url": "https://github.com/johnnyrobot/grader-releases/releases/download/v0.1.1/Grader.app.tar.gz",
                    },
                },
            }

    class HeadResponse:
        status_code = 200
        url = "https://github.com/johnnyrobot/grader-releases/releases/download/v0.1.1/Grader.app.tar.gz"
        headers = {"content-length": "123"}

    monkeypatch.setattr(preflight.httpx, "get", lambda *_args, **_kwargs: ManifestResponse())
    monkeypatch.setattr(preflight.httpx, "head", lambda *_args, **_kwargs: HeadResponse())

    result = preflight.collect(config_path=config, current_version="0.1.0")

    assert result["status"] == "PASS"
    assert result["failures"] == []


def test_updater_channel_preflight_blocks_missing_release(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    _write_config(config)

    class Response:
        status_code = 404
        url = "https://github.com/johnnyrobot/grader-releases/releases/latest/download/latest.json"

        def json(self):
            return {}

    monkeypatch.setattr(preflight.httpx, "get", lambda *_args, **_kwargs: Response())

    result = preflight.collect(config_path=config, current_version="0.1.0")

    assert result["status"] == "BLOCKED_UPDATER_CHANNEL"
    assert "manifest returned HTTP 404" in result["failures"]


def test_updater_channel_preflight_blocks_not_newer_version(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    _write_config(config)

    class ManifestResponse:
        status_code = 200
        url = "https://github.com/johnnyrobot/grader-releases/releases/latest/download/latest.json"

        def json(self):
            return {
                "version": "0.1.0",
                "platforms": {
                    "darwin-aarch64": {"signature": "signed", "url": "https://github.com/johnnyrobot/grader-releases/releases/download/v0.1.0/Grader.app.tar.gz"},
                    "darwin-x86_64": {"signature": "signed", "url": "https://github.com/johnnyrobot/grader-releases/releases/download/v0.1.0/Grader.app.tar.gz"},
                },
            }

    class HeadResponse:
        status_code = 200
        url = "https://github.com/johnnyrobot/grader-releases/releases/download/v0.1.0/Grader.app.tar.gz"
        headers = {"content-length": "123"}

    monkeypatch.setattr(preflight.httpx, "get", lambda *_args, **_kwargs: ManifestResponse())
    monkeypatch.setattr(preflight.httpx, "head", lambda *_args, **_kwargs: HeadResponse())

    result = preflight.collect(config_path=config, current_version="0.1.0")

    assert result["status"] == "BLOCKED_UPDATER_CHANNEL"
    assert "manifest version '0.1.0' is not newer than current version '0.1.0'" in result["failures"]


def test_updater_channel_preflight_blocks_missing_artifact_url(tmp_path, monkeypatch):
    config = tmp_path / "tauri.conf.json"
    _write_config(config)

    class ManifestResponse:
        status_code = 200
        url = "https://github.com/johnnyrobot/grader-releases/releases/latest/download/latest.json"

        def json(self):
            return {
                "version": "0.1.1",
                "platforms": {
                    "darwin-aarch64": {
                        "signature": "signed",
                        "url": "https://github.com/johnnyrobot/grader-releases/releases/download/v0.1.1/Grader.app.tar.gz",
                    },
                    "darwin-x86_64": {
                        "signature": "signed",
                        "url": "https://github.com/johnnyrobot/grader-releases/releases/download/v0.1.1/Grader.app.tar.gz",
                    },
                },
            }

    class HeadResponse:
        status_code = 404
        url = "https://github.com/johnnyrobot/grader-releases/releases/download/v0.1.1/Grader.app.tar.gz"
        headers = {}

    monkeypatch.setattr(preflight.httpx, "get", lambda *_args, **_kwargs: ManifestResponse())
    monkeypatch.setattr(preflight.httpx, "head", lambda *_args, **_kwargs: HeadResponse())

    result = preflight.collect(config_path=config, current_version="0.1.0")

    assert result["status"] == "BLOCKED_UPDATER_CHANNEL"
    assert "manifest platform darwin-aarch64 artifact URL is not reachable (HTTP 404)" in result["failures"]
    assert "manifest platform darwin-x86_64 artifact URL is not reachable (HTTP 404)" in result["failures"]
