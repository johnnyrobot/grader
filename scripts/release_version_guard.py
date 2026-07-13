#!/usr/bin/env python3
"""Fail closed when release tag/version metadata drift.

The Tauri updater compares the installed app version with the version advertised
by the signed updater manifest. A GitHub release tagged vN+1 while the app
bundle still reports N can create a release channel that looks newer but
relaunches to the old version. This guard keeps tag, package metadata, and
runtime update-reporting versions aligned before release assets are published.
"""

from __future__ import annotations

import argparse
import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_VERSION_FILES = {
    "tauri_config": Path("app/src-tauri/tauri.conf.json"),
    "tauri_cargo": Path("app/src-tauri/Cargo.toml"),
    "frontend_package": Path("app/package.json"),
    "python_project": Path("pyproject.toml"),
    "engine_launcher": Path("scripts/start_engine.sh"),
    "dev_mock_update_fixture": Path("scripts/dev_mock_engine.py"),
}


def _normalize_tag(value: str) -> str:
    tag = value.strip()
    if tag.startswith("refs/tags/"):
        tag = tag.removeprefix("refs/tags/")
    if tag.startswith("v"):
        tag = tag[1:]
    return tag


def _read_json_version(path: Path) -> str | None:
    payload = json.loads(path.read_text())
    version = payload.get("version")
    return str(version) if version else None


def _read_toml_version(path: Path) -> str | None:
    payload = tomllib.loads(path.read_text())
    version = payload.get("package", {}).get("version") or payload.get("project", {}).get("version")
    return str(version) if version else None


def _read_regex_version(path: Path, pattern: str) -> str | None:
    import re

    match = re.search(pattern, path.read_text(), flags=re.MULTILINE)
    return match.group("version") if match else None


def _read_version(label: str, path: Path) -> str | None:
    if not path.exists():
        return None
    if path.suffix == ".json":
        return _read_json_version(path)
    if path.suffix == ".toml":
        return _read_toml_version(path)
    if label == "engine_launcher":
        return _read_regex_version(path, r'^export GRADER_APP_VERSION="(?P<version>[^"]+)"')
    if label == "dev_mock_update_fixture":
        return _read_regex_version(path, r'"current_version":\s*"(?P<version>[^"]+)"')
    raise ValueError(f"unsupported version file for {label}: {path}")


def collect(
    *,
    tag: str | None = None,
    expected_version: str | None = None,
    version_files: dict[str, Path] | None = None,
) -> dict[str, Any]:
    raw_tag = tag or os.environ.get("GITHUB_REF_NAME") or ""
    normalized_tag = _normalize_tag(raw_tag) if raw_tag else ""
    expected = expected_version or normalized_tag
    files = version_files or DEFAULT_VERSION_FILES

    failures: list[str] = []
    versions: dict[str, dict[str, Any]] = {}
    if not expected:
        failures.append("expected release version is missing; pass --tag or --expected-version")

    for label, path in files.items():
        try:
            version = _read_version(label, path)
        except Exception as exc:
            version = None
            failures.append(f"{label} version could not be read from {path}: {exc}")
        versions[label] = {"path": str(path), "version": version}
        if version is None:
            failures.append(f"{label} version is missing from {path}")
        elif expected and version != expected:
            failures.append(f"{label} version {version!r} does not match expected {expected!r}")

    status = "PASS" if not failures else "BLOCKED_RELEASE_VERSION"
    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tag": raw_tag,
        "normalized_tag": normalized_tag,
        "expected_version": expected,
        "versions": versions,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate release tag/version consistency")
    parser.add_argument("--tag", default=None, help="Release tag, e.g. v0.1.0; defaults to GITHUB_REF_NAME")
    parser.add_argument("--expected-version", default=None, help="Version to require when no tag is available")
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/release-version.json"))
    parser.add_argument("--no-fail", action="store_true", help="Write diagnostics but exit 0 when blocked")
    args = parser.parse_args()

    result = collect(tag=args.tag, expected_version=args.expected_version)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"status={result['status']}")
    print(f"json={args.out}")
    if result["failures"]:
        print("failures=" + "; ".join(result["failures"]))
    if result["status"] != "PASS" and not args.no_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
