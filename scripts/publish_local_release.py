#!/usr/bin/env python3
"""Publish already signed/notarized Grader assets to the public update channel.

This script never handles Apple credentials. It expects the local release
pipeline to have produced and verified the DMG, the signed ``.app.tar.gz``
updater bundle, and signed ``latest.json``.
Dry-run is the default; ``--apply`` is required to create the GitHub release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SOURCE_REPO = "johnnyrobot/grader"
RELEASE_REPO = "johnnyrobot/grader-releases"
REQUIRED_PLATFORMS = ("darwin-aarch64",)
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Metadata queries answer in seconds. The upload does not: the release carries the DMG
# and the updater tarball, which together run to roughly a gigabyte, so the ceiling has
# to be an upload budget rather than a query budget. At 120s the upload was killed
# mid-flight and left a draft release holding only the small files.
QUERY_TIMEOUT_SECONDS = 120
UPLOAD_TIMEOUT_SECONDS = 3600


def _run(
    runner: Runner,
    command: list[str],
    *,
    timeout: float = QUERY_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    return runner(command, text=True, capture_output=True, timeout=timeout, check=False)


def collect(
    *,
    dmg: Path,
    updater_bundle: Path,
    manifest: Path,
    version: str,
    tag: str,
    release_repo: str = RELEASE_REPO,
    source_repo: str = SOURCE_REPO,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    failures: list[str] = []
    checks: dict[str, bool] = {
        "gh_installed": shutil.which("gh") is not None,
        "dmg_present": dmg.is_file(),
        "updater_bundle_present": updater_bundle.is_file(),
        "manifest_present": manifest.is_file(),
    }
    if not checks["gh_installed"]:
        failures.append("GitHub CLI `gh` is unavailable")
    if not checks["dmg_present"]:
        failures.append(f"DMG is missing: {dmg}")
    if not checks["updater_bundle_present"]:
        failures.append(f"updater bundle is missing: {updater_bundle}")
    elif not updater_bundle.name.endswith(".app.tar.gz"):
        failures.append(f"updater bundle must end in .app.tar.gz: {updater_bundle}")
    if not checks["manifest_present"]:
        failures.append(f"updater manifest is missing: {manifest}")

    payload: dict[str, Any] = {}
    if checks["manifest_present"]:
        try:
            payload = json.loads(manifest.read_text())
        except Exception as exc:
            failures.append(f"updater manifest is invalid JSON: {exc}")
    checks["manifest_version_matches"] = payload.get("version") == version
    if payload and not checks["manifest_version_matches"]:
        failures.append(
            f"manifest version {payload.get('version')!r} does not match release version {version!r}"
        )

    expected_prefix = f"https://github.com/{release_repo}/releases/download/{tag}/"
    platforms = payload.get("platforms") if isinstance(payload, dict) else {}
    platforms = platforms if isinstance(platforms, dict) else {}
    for platform in sorted(platforms):
        if platform not in REQUIRED_PLATFORMS:
            failures.append(
                f"manifest advertises {platform} but every Grader build is arm64-only; "
                "an update offered there installs an app that cannot launch"
            )
    platform_checks: dict[str, Any] = {}
    for platform in REQUIRED_PLATFORMS:
        entry = platforms.get(platform) if isinstance(platforms, dict) else None
        signature = str(entry.get("signature") or "") if isinstance(entry, dict) else ""
        url = str(entry.get("url") or "") if isinstance(entry, dict) else ""
        ok = bool(signature) and url == expected_prefix + updater_bundle.name
        platform_checks[platform] = {
            "signature_present": bool(signature),
            "url": url,
            "valid": ok,
        }
        if not ok:
            failures.append(f"manifest platform {platform} is unsigned or targets the wrong release URL")
    checks["manifest_platforms_valid"] = all(
        row["valid"] for row in platform_checks.values()
    )

    repo_payload: dict[str, Any] = {}
    release_exists = False
    if checks["gh_installed"]:
        repo_proc = _run(
            runner,
            ["gh", "repo", "view", release_repo, "--json", "nameWithOwner,visibility,isPrivate,url"],
        )
        if repo_proc.returncode == 0 and (repo_proc.stdout or "").strip():
            try:
                repo_payload = json.loads(repo_proc.stdout)
            except json.JSONDecodeError:
                repo_payload = {}
        checks["release_repo_access"] = (
            repo_proc.returncode == 0 and repo_payload.get("nameWithOwner") == release_repo
        )
        checks["release_repo_public"] = (
            repo_payload.get("visibility") == "PUBLIC" or repo_payload.get("isPrivate") is False
        )
        if not checks["release_repo_access"]:
            failures.append(f"release repo {release_repo!r} is not accessible")
        elif not checks["release_repo_public"]:
            failures.append(f"release repo {release_repo!r} is not public")
        release_proc = _run(
            runner,
            ["gh", "release", "view", tag, "--repo", release_repo, "--json", "tagName"],
        )
        release_exists = release_proc.returncode == 0
        checks["release_absent"] = not release_exists
        if release_exists:
            failures.append(f"release {tag!r} already exists in {release_repo}")

    source_commit_proc = _run(runner, ["git", "rev-parse", "HEAD"])
    source_commit = (source_commit_proc.stdout or "").strip() if source_commit_proc.returncode == 0 else ""
    checks["source_commit_present"] = bool(source_commit)
    if not source_commit:
        failures.append("could not determine the source commit")

    hashes = {
        str(dmg): _sha256(dmg) if dmg.is_file() else None,
        str(updater_bundle): _sha256(updater_bundle) if updater_bundle.is_file() else None,
        str(manifest): _sha256(manifest) if manifest.is_file() else None,
    }
    return {
        "status": "PASS" if not failures else "BLOCKED_LOCAL_RELEASE_PUBLICATION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_repo": source_repo,
        "source_commit": source_commit,
        "release_repo": release_repo,
        "version": version,
        "tag": tag,
        "dmg": str(dmg),
        "updater_bundle": str(updater_bundle),
        "manifest": str(manifest),
        "hashes": hashes,
        "checks": checks,
        "platforms": platform_checks,
        "failures": failures,
    }


def _write_publication_files(result: dict[str, Any], out_dir: Path) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    provenance = out_dir / "release-provenance.json"
    checksums = out_dir / "SHA256SUMS"
    notes = out_dir / "release-notes.md"
    provenance.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    dmg = Path(result["dmg"])
    updater_bundle = Path(result["updater_bundle"])
    manifest = Path(result["manifest"])
    checksums.write_text(
        f"{result['hashes'][str(dmg)]}  {dmg.name}\n"
        f"{result['hashes'][str(updater_bundle)]}  {updater_bundle.name}\n"
        f"{result['hashes'][str(manifest)]}  {manifest.name}\n"
    )
    notes.write_text(
        f"# Grader {result['version']} Public Beta\n\n"
        "Signed and notarized macOS beta build. Grader keeps student work on the instructor's Mac "
        "and requires explicit teacher approval before posting a Canvas grade.\n\n"
        "Citation verification is experimental and must not be treated as authoritative. "
        "Teacher-local grading accuracy requires institution-authorized calibration evidence.\n\n"
        f"Source provenance: `{result['source_repo']}@{result['source_commit']}`.\n"
    )
    return provenance, checksums, notes


def publish(
    result: dict[str, Any],
    *,
    out_dir: Path,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    if result.get("status") != "PASS":
        return result
    provenance, checksums, notes = _write_publication_files(result, out_dir)
    command = [
        "gh", "release", "create", result["tag"],
        "--repo", result["release_repo"],
        "--title", f"Grader {result['version']} Public Beta",
        "--notes-file", str(notes),
        "--latest",
        result["dmg"],
        result["updater_bundle"],
        result["manifest"],
        str(checksums),
        str(provenance),
    ]
    applied = dict(result)
    try:
        proc = _run(runner, command, timeout=UPLOAD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # A timed-out upload does not leave nothing behind: gh creates the release as a
        # draft and attaches whatever it managed to send. Say so, because the next run
        # will refuse the tag as already taken and the reason would otherwise be unclear.
        applied["apply"] = {
            "returncode": None,
            "timed_out_after_seconds": UPLOAD_TIMEOUT_SECONDS,
            "assets": [],
        }
        applied["status"] = "BLOCKED_LOCAL_RELEASE_PUBLICATION"
        applied["failures"] = [
            *result.get("failures", []),
            f"gh release create timed out after {UPLOAD_TIMEOUT_SECONDS}s; "
            f"delete the partial draft release {result['tag']!r} before retrying",
        ]
        return applied
    applied["apply"] = {
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
        "assets": [result["dmg"], result["updater_bundle"], result["manifest"], str(checksums), str(provenance)],
    }
    if proc.returncode != 0:
        applied["status"] = "BLOCKED_LOCAL_RELEASE_PUBLICATION"
        applied["failures"] = [*result.get("failures", []), "gh release create failed"]
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish locally signed Grader release assets")
    parser.add_argument("--dmg", type=Path, required=True)
    parser.add_argument("--updater-bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("latest.json"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", default=None)
    parser.add_argument("--release-repo", default=RELEASE_REPO)
    parser.add_argument("--source-repo", default=SOURCE_REPO)
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/release-evidence/publication"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/local-release-publication-current.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    tag = args.tag or f"v{args.version}"
    result = collect(
        dmg=args.dmg,
        updater_bundle=args.updater_bundle,
        manifest=args.manifest,
        version=args.version,
        tag=tag,
        release_repo=args.release_repo,
        source_repo=args.source_repo,
    )
    if args.apply:
        result = publish(result, out_dir=args.out_dir)
    else:
        result["dry_run"] = True
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"status={result['status']}")
    print(f"json={args.out}")
    if not args.apply:
        print("dry_run=true (pass --apply to create the public release)")
    if result.get("failures"):
        print("failures=" + "; ".join(result["failures"]))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
