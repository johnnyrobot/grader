#!/usr/bin/env python3
"""Validate Tauri updater release evidence for Grader.

This does not replace a clean-machine N->N+1 update test. It verifies the
static release artifacts that must exist before that manual test can be honest:

  - updater is enabled in ``tauri.conf.json``
  - updater public key is configured
  - latest.json exists and targets the current app version unless overridden
  - each configured platform has a URL and a non-empty signature
  - optional local artifact metadata matches the manifest URL filename
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_PLATFORMS = ("darwin-aarch64", "darwin-x86_64")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def collect(
    *,
    config_path: Path,
    manifest_path: Path | None,
    artifact_path: Path | None,
    expected_version: str | None,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {}

    if not config_path.exists():
        failures.append(f"missing Tauri config: {config_path}")
        config: dict[str, Any] = {}
    else:
        config = _load_json(config_path)

    updater = config.get("plugins", {}).get("updater", {})
    config_version = str(config.get("version") or "")
    expected = expected_version or config_version
    details["config_version"] = config_version
    details["expected_version"] = expected
    details["configured_endpoints"] = updater.get("endpoints") or []

    if updater.get("active") is not True:
        failures.append("Tauri updater is not active")
    if not updater.get("pubkey"):
        failures.append("Tauri updater pubkey is missing")
    if not updater.get("endpoints"):
        failures.append("Tauri updater endpoints are missing")

    manifest: dict[str, Any] = {}
    if manifest_path is None or not manifest_path.exists():
        failures.append("missing updater manifest latest.json")
    else:
        manifest = _load_json(manifest_path)
        details["manifest_path"] = str(manifest_path)
        details["manifest_version"] = manifest.get("version")
        if str(manifest.get("version") or "") != expected:
            failures.append(
                f"manifest version {manifest.get('version')!r} does not match expected {expected!r}"
            )
        if not manifest.get("pub_date"):
            failures.append("manifest pub_date is missing")
        platforms = manifest.get("platforms") or {}
        for platform in REQUIRED_PLATFORMS:
            entry = platforms.get(platform) or {}
            if not entry.get("url"):
                failures.append(f"manifest platform {platform} is missing url")
            if not entry.get("signature"):
                failures.append(f"manifest platform {platform} is missing signature")

    if artifact_path is not None:
        if not artifact_path.exists():
            failures.append(f"missing updater artifact: {artifact_path}")
        else:
            details["artifact_path"] = str(artifact_path)
            details["artifact_size_bytes"] = artifact_path.stat().st_size
            details["artifact_sha256"] = _sha256(artifact_path)
            manifest_urls = [
                str(entry.get("url") or "")
                for entry in (manifest.get("platforms") or {}).values()
                if isinstance(entry, dict)
            ]
            if manifest_urls and not any(url.endswith(artifact_path.name) for url in manifest_urls):
                failures.append("manifest urls do not reference the supplied artifact filename")
    else:
        warnings.append("no local updater artifact supplied; clean-machine download test still required")

    status = "PASS" if not failures else "BLOCKED_UPDATER_EVIDENCE"
    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "failures": failures,
        "warnings": warnings,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Tauri updater release evidence")
    parser.add_argument("--config", type=Path, default=Path("app/src-tauri/tauri.conf.json"))
    parser.add_argument("--manifest", type=Path, default=None, help="Path to latest.json")
    parser.add_argument("--artifact", type=Path, default=None, help="Path to signed update artifact")
    parser.add_argument("--version", default=None, help="Expected version; defaults to tauri.conf.json")
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/updater.json"))
    args = parser.parse_args()

    evidence = collect(
        config_path=args.config,
        manifest_path=args.manifest,
        artifact_path=args.artifact,
        expected_version=args.version,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"status={evidence['status']}")
    print(f"json={args.out}")
    if evidence["failures"]:
        print("failures=" + "; ".join(evidence["failures"]))


if __name__ == "__main__":
    main()
