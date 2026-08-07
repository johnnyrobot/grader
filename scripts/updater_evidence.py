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
import subprocess
import sys
import tempfile
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ports import CodeSigner, DiskImage, find_app_bundle, source_revision
REQUIRED_PLATFORMS = ("darwin-aarch64", "darwin-x86_64")


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., CompletedProcessLike]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_source_revision() -> dict[str, Any]:
    return source_revision(PROJECT_ROOT)


def _mtime_metadata(path: Path) -> dict[str, Any]:
    mtime = path.stat().st_mtime
    return {
        "mtime_epoch": mtime,
        "mtime": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
    }


def _source_freshness(paths: dict[str, Path]) -> tuple[list[str], dict[str, Any]]:
    source = _git_source_revision()
    details: dict[str, Any] = {
        "source_revision": source,
        "checked_artifacts": [],
        "stale_artifacts": [],
    }
    if source.get("available") is not True:
        details["ok"] = False
        details["reason"] = "could not determine current git source revision"
        return ["could not determine current git source revision for artifact freshness"], details

    commit_time = float(source["commit_time_epoch"])
    failures: list[str] = []
    stale: list[str] = []
    for name, path in paths.items():
        if not path.exists():
            continue
        mtime = path.stat().st_mtime
        row = {
            "name": name,
            "path": str(path),
            **_mtime_metadata(path),
            "current_commit": source.get("commit"),
            "current_commit_time": source.get("commit_time"),
        }
        details["checked_artifacts"].append(row)
        if mtime + 1 < commit_time:
            stale.append(name)

    if stale:
        failures.append(
            "release artifact timestamps predate the current git commit: " + ", ".join(stale)
        )
        details["reason"] = (
            "rebuild the release artifact and regenerate updater evidence from this checkout"
        )
    elif not details["checked_artifacts"]:
        failures.append("no updater artifacts were available for source freshness")
        details["reason"] = "no checked artifacts"

    details["stale_artifacts"] = stale
    details["ok"] = not failures
    return failures, details


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _run(command: list[str], *, runner: Runner = subprocess.run, timeout: int = 60) -> dict[str, Any]:
    try:
        proc = runner(command, text=True, capture_output=True, check=False, timeout=timeout)
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return {
            "command": command,
            "returncode": proc.returncode,
            "ok": proc.returncode == 0,
            "output": output[:2000],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": command,
            "returncode": 127,
            "ok": False,
            "output": str(exc)[:2000],
        }


def _verify_app_signature(app_path: Path, *, runner: Runner = subprocess.run) -> tuple[list[str], dict[str, Any]]:
    signer = CodeSigner(runner=runner)
    checks = {
        "codesign_verify": signer.verify_app(app_path),
        "spctl_assess": signer.assess_app(app_path),
    }
    failures = [
        f"app signature check failed: {name}"
        for name, result in checks.items()
        if not result.get("ok")
    ]
    return failures, checks


def _verify_dmg_artifact(path: Path, *, runner: Runner = subprocess.run) -> tuple[list[str], dict[str, Any]]:
    signer = CodeSigner(runner=runner)
    details: dict[str, Any] = {
        "stapler_validate": signer.validate_staple(path),
        "spctl_assess": signer.assess_installer(path),
    }
    failures = [
        f"DMG signature/notarization check failed: {name}"
        for name in ("stapler_validate", "spctl_assess")
        if not details[name].get("ok")
    ]

    # The app a teacher installs is the one inside the image. Verifying the one
    # left in the build directory can pass while the shipped bundle is unsigned.
    with DiskImage(path, runner=runner).mounted() as (mount_point, records):
        if mount_point is None:
            failures.append(str(records.get("reason") or "could not mount DMG"))
        else:
            app_path = find_app_bundle(mount_point)
            if app_path is None:
                failures.append("mounted DMG does not contain a .app bundle")
            else:
                details["bundled_app_path"] = str(app_path)
                app_failures, app_details = _verify_app_signature(app_path, runner=runner)
                details["bundled_app"] = app_details
                failures.extend(f"bundled {failure}" for failure in app_failures)
    details.update(records)

    return failures, details


def _verify_tar_gz_artifact(path: Path, *, runner: Runner = subprocess.run) -> tuple[list[str], dict[str, Any]]:
    details: dict[str, Any] = {}
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="grader-updater-tar-") as tmp:
        extract_dir = Path(tmp)
        try:
            with tarfile.open(path, "r:gz") as archive:
                archive.extractall(extract_dir, filter="data")
        except Exception as exc:
            return [f"could not extract updater .app.tar.gz artifact: {exc}"], details
        app_path = find_app_bundle(extract_dir)
        if app_path is None:
            return ["updater .app.tar.gz artifact does not contain a .app bundle"], details
        details["bundled_app_path"] = str(app_path)
        app_failures, app_details = _verify_app_signature(app_path, runner=runner)
        details["bundled_app"] = app_details
        failures.extend(app_failures)
    return failures, details


def _verify_local_artifact(path: Path, *, runner: Runner = subprocess.run) -> tuple[list[str], dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".dmg":
        return _verify_dmg_artifact(path, runner=runner)
    if suffix == ".app" and path.is_dir():
        return _verify_app_signature(path, runner=runner)
    if path.name.endswith(".app.tar.gz"):
        return _verify_tar_gz_artifact(path, runner=runner)
    return [f"unsupported updater artifact type for signature validation: {path.name}"], {}


def collect(
    *,
    config_path: Path,
    manifest_path: Path | None,
    artifact_path: Path | None,
    expected_version: str | None,
    runner: Runner = subprocess.run,
    verify_artifact_signature: bool = True,
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
        details["manifest_mtime"] = _mtime_metadata(manifest_path)["mtime"]
        details["manifest_mtime_epoch"] = _mtime_metadata(manifest_path)["mtime_epoch"]
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
            details.update({
                "artifact_mtime": _mtime_metadata(artifact_path)["mtime"],
                "artifact_mtime_epoch": _mtime_metadata(artifact_path)["mtime_epoch"],
            })
            manifest_urls = [
                str(entry.get("url") or "")
                for entry in (manifest.get("platforms") or {}).values()
                if isinstance(entry, dict)
            ]
            if manifest_urls and not any(url.endswith(artifact_path.name) for url in manifest_urls):
                failures.append("manifest urls do not reference the supplied artifact filename")
            if verify_artifact_signature:
                artifact_failures, artifact_details = _verify_local_artifact(artifact_path, runner=runner)
                details["artifact_signature_checks"] = artifact_details
                failures.extend(artifact_failures)
    else:
        warnings.append("no local updater artifact supplied; clean-machine download test still required")

    freshness_paths: dict[str, Path] = {}
    if manifest_path is not None:
        freshness_paths["manifest"] = manifest_path
    if artifact_path is not None:
        freshness_paths["artifact"] = artifact_path
    freshness_failures, freshness_details = _source_freshness(freshness_paths)
    details["source_freshness"] = freshness_details
    failures.extend(freshness_failures)

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
    parser.add_argument("--skip-artifact-signature-checks", action="store_true",
                        help="Only for local script development; public beta evidence requires artifact signature checks")
    args = parser.parse_args()

    evidence = collect(
        config_path=args.config,
        manifest_path=args.manifest,
        artifact_path=args.artifact,
        expected_version=args.version,
        verify_artifact_signature=not args.skip_artifact_signature_checks,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"status={evidence['status']}")
    print(f"json={args.out}")
    if evidence["failures"]:
        print("failures=" + "; ".join(evidence["failures"]))


if __name__ == "__main__":
    main()
