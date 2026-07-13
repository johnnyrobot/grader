#!/usr/bin/env python3
"""Build a clean-machine release-evidence handoff package.

This does not mark Grader public-beta ready. It creates a deterministic folder
for the clean-machine tester containing:

  - the signed/notarized release artifact and updater manifest, when supplied
  - the latest version/updater/clean-machine/release-readiness evidence JSON
  - the clean-machine manual runbook and evidence template
  - a package manifest with SHA-256 metadata and current blocker summaries

The output directory is intended to live under ``artifacts/`` and remain
gitignored. Do not add student data, Canvas tokens, Apple credentials, or
private keys to the package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.clean_machine_evidence import manual_evidence_runbook, manual_evidence_template
from scripts.github_release_secrets import setup_runbook as github_release_secrets_runbook
from scripts.updater_key_doctor import private_shell_runbook as updater_key_private_runbook


DEFAULT_OUT = Path("artifacts/release-evidence/clean-machine-handoff")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_metadata(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"present": False, "path": None}
    if not path.exists():
        return {"present": False, "path": str(path), "reason": "path does not exist"}
    return {
        "present": True,
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "mtime_epoch": path.stat().st_mtime,
        "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
    }


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        return {"status": "UNREADABLE", "reason": str(exc)}
    return payload if isinstance(payload, dict) else {"status": "UNREADABLE", "reason": "JSON root is not an object"}


def _safe_copy(src: Path | None, dst_dir: Path, *, copy_artifacts: bool) -> dict[str, Any]:
    metadata = _file_metadata(src)
    if metadata.get("present") is not True or src is None:
        metadata["copied_to"] = None
        return metadata
    if not copy_artifacts:
        metadata["copied_to"] = None
        return metadata
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    shutil.copy2(src, dst)
    metadata["copied_to"] = str(dst)
    return metadata


def _summaries(*, updater: dict[str, Any], clean_machine: dict[str, Any], readiness: dict[str, Any]) -> dict[str, Any]:
    blockers = readiness.get("blockers")
    if not isinstance(blockers, list):
        blockers = []
    return {
        "readiness_verdict": readiness.get("verdict", "unknown"),
        "blockers": blockers,
        "updater_status": updater.get("status", "MISSING"),
        "updater_failures": updater.get("failures", []),
        "clean_machine_status": clean_machine.get("status", "MISSING"),
        "clean_machine_missing_checks": clean_machine.get("missing_checks", []),
        "clean_machine_checks": clean_machine.get("checks", {}),
        "e2e_matrix_summary": readiness.get("e2e_matrix_summary", {}),
    }


def _readme(summary: dict[str, Any]) -> str:
    blockers = summary.get("blockers") or []
    blocker_lines = "\n".join(
        f"- `{row.get('gate', 'unknown')}`: `{row.get('status', 'MISSING')}` — {row.get('summary', '')}"
        for row in blockers
        if isinstance(row, dict)
    ) or "- No blocker rows were present in the supplied readiness report."
    return f"""# Grader clean-machine evidence handoff

Generated package for a clean-machine public-beta release check.

Current readiness verdict: `{summary.get('readiness_verdict', 'unknown')}`

## Current blockers

{blocker_lines}

## Tester workflow

1. Install the signed/notarized N release DMG from `release-artifacts/` on a clean supported Mac.
2. Before publishing N+1, dry-run `scripts/bump_release_version.py --target-version <N+1>`, apply
   it only in the release-prep commit, then require `scripts/release_version_guard.py --tag v<N+1>`
   to pass so the tag, app bundle, Rust crate/lockfile, frontend package/lockfile, Python project,
   engine launcher, and dev mock update fixture agree on the release version.
3. Configure GitHub Actions release secrets in a private shell with
   `scripts/github_release_secrets.py`; see `github-release-secrets-runbook.md`. The workflow
   imports the Developer ID `.p12` into a temporary runner keychain before signing. If the encrypted
   updater-key password (`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`) is stored in macOS Keychain, use the runbook's
   `--updater-password-keychain-service ... --updater-password-keychain-account ...` path so the
   password does not need to be pasted or exported.
4. Run `scripts/github_release_preflight.py --candidate-tag v<N+1>` and require `status=PASS`
   before creating the tag or publishing assets. This verifies GitHub auth, the configured updater
   endpoint, release workflow, required Actions secrets, candidate version consistency, and
   tag/release availability without writing to GitHub.
5. Publish a signed N+1 DMG and signed `latest.json` to the configured GitHub release channel:
   `https://github.com/johnnyrobot/grader/releases/latest/download/latest.json`.
   Follow `updater-key-private-runbook.md` to verify the updater key/password and regenerate
   signed updater evidence without printing or committing the password.
6. Run `uv run python scripts/updater_channel_preflight.py --current-version <installed-N-version>`
   and require `status=PASS` before starting the clean-machine updater-path test.
7. Launch N, wait for the update banner, use the in-app `Install update and restart` action, and
   confirm the relaunched app is N+1. Do not manually replace the app for this check.
8. Fill `clean-machine-manual-evidence.json` while following `clean-machine-runbook.md`.
9. Run `scripts/computer_use_sweep_evidence.py` against the signed-app Computer Use capture folder and
   place the PASS JSON in the evidence root as `computer_use_instructor_sweep.json`.
10. Run `scripts/clean_machine_evidence.py` with the filled manual evidence, release DMG, signed
   updater manifest, and expected app version.
11. Rerun `scripts/release_readiness.py` and require verdict `supervised_public_beta` before public
   beta claims.

Do not add student work, Canvas tokens, Apple credentials, updater private keys, or private-key
passwords to this package.
"""


def build_package(
    *,
    out_dir: Path,
    dmg: Path | None = None,
    updater_manifest: Path | None = None,
    updater_evidence: Path | None = None,
    clean_machine_evidence: Path | None = None,
    readiness_report: Path | None = None,
    signing_evidence: Path | None = None,
    release_version_evidence: Path | None = None,
    github_release_secrets_evidence: Path | None = None,
    github_release_evidence: Path | None = None,
    updater_channel_evidence: Path | None = None,
    updater_key_evidence: Path | None = None,
    copy_artifacts: bool = True,
) -> dict[str, Any]:
    release_artifacts_dir = out_dir / "release-artifacts"
    evidence_dir = out_dir / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)

    updater_payload = _load_json(updater_evidence)
    clean_machine_payload = _load_json(clean_machine_evidence)
    readiness_payload = _load_json(readiness_report)
    summary = _summaries(
        updater=updater_payload,
        clean_machine=clean_machine_payload,
        readiness=readiness_payload,
    )

    files = {
        "dmg": _safe_copy(dmg, release_artifacts_dir, copy_artifacts=copy_artifacts),
        "updater_manifest": _safe_copy(updater_manifest, release_artifacts_dir, copy_artifacts=copy_artifacts),
        "updater_evidence": _safe_copy(updater_evidence, evidence_dir, copy_artifacts=True),
        "clean_machine_evidence": _safe_copy(clean_machine_evidence, evidence_dir, copy_artifacts=True),
        "readiness_report": _safe_copy(readiness_report, evidence_dir, copy_artifacts=True),
        "signing_evidence": _safe_copy(signing_evidence, evidence_dir, copy_artifacts=True),
        "release_version_evidence": _safe_copy(release_version_evidence, evidence_dir, copy_artifacts=True),
        "github_release_secrets_evidence": _safe_copy(github_release_secrets_evidence, evidence_dir, copy_artifacts=True),
        "github_release_evidence": _safe_copy(github_release_evidence, evidence_dir, copy_artifacts=True),
        "updater_channel_evidence": _safe_copy(updater_channel_evidence, evidence_dir, copy_artifacts=True),
        "updater_key_evidence": _safe_copy(updater_key_evidence, evidence_dir, copy_artifacts=True),
    }

    runbook = out_dir / "clean-machine-runbook.md"
    github_runbook = out_dir / "github-release-secrets-runbook.md"
    updater_runbook = out_dir / "updater-key-private-runbook.md"
    template = out_dir / "clean-machine-manual-evidence.json"
    readme = out_dir / "README.md"
    manifest_path = out_dir / "release-evidence-package.json"

    runbook.write_text(manual_evidence_runbook())
    github_runbook.write_text(github_release_secrets_runbook())
    updater_runbook.write_text(updater_key_private_runbook())
    template.write_text(json.dumps(manual_evidence_template(), indent=2, sort_keys=True) + "\n")
    readme.write_text(_readme(summary))

    manifest = {
        "status": "PASS_PACKAGE_READY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
        "copy_release_artifacts": copy_artifacts,
        "summary": summary,
        "files": files,
        "generated_files": {
            "runbook": _file_metadata(runbook),
            "github_release_secrets_runbook": _file_metadata(github_runbook),
            "updater_key_private_runbook": _file_metadata(updater_runbook),
            "manual_template": _file_metadata(template),
            "readme": _file_metadata(readme),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    manifest["generated_files"]["manifest"] = _file_metadata(manifest_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a clean-machine release evidence handoff package")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dmg", type=Path, default=Path("app/src-tauri/target/release/bundle/dmg/Grader.dmg"))
    parser.add_argument("--updater-manifest", type=Path, default=Path("latest.json"))
    parser.add_argument("--updater-evidence", type=Path, default=Path("artifacts/release-evidence/updater-current.json"))
    parser.add_argument("--clean-machine-evidence", type=Path, default=Path("artifacts/release-evidence/clean-machine-local-current.json"))
    parser.add_argument("--readiness-report", type=Path, default=Path("artifacts/release-readiness/release-readiness.json"))
    parser.add_argument("--signing-evidence", type=Path, default=Path("artifacts/release-evidence/signing-current.json"))
    parser.add_argument("--release-version-evidence", type=Path, default=Path("artifacts/release-evidence/release-version.json"))
    parser.add_argument("--github-release-secrets-evidence", type=Path, default=Path("artifacts/release-evidence/github-release-secrets-current.json"))
    parser.add_argument("--github-release-evidence", type=Path, default=Path("artifacts/release-evidence/github-release-preflight-current.json"))
    parser.add_argument("--updater-channel-evidence", type=Path, default=Path("artifacts/release-evidence/updater-channel-current.json"))
    parser.add_argument("--updater-key-evidence", type=Path, default=Path("artifacts/release-evidence/updater-key-current.json"))
    parser.add_argument("--no-copy-release-artifacts", action="store_true",
                        help="Write metadata only for DMG/latest.json instead of copying them into the package")
    args = parser.parse_args()

    manifest = build_package(
        out_dir=args.out_dir,
        dmg=args.dmg,
        updater_manifest=args.updater_manifest,
        updater_evidence=args.updater_evidence,
        clean_machine_evidence=args.clean_machine_evidence,
        readiness_report=args.readiness_report,
        signing_evidence=args.signing_evidence,
        release_version_evidence=args.release_version_evidence,
        github_release_secrets_evidence=args.github_release_secrets_evidence,
        github_release_evidence=args.github_release_evidence,
        updater_channel_evidence=args.updater_channel_evidence,
        updater_key_evidence=args.updater_key_evidence,
        copy_artifacts=not args.no_copy_release_artifacts,
    )
    print(f"status={manifest['status']}")
    print(f"package={args.out_dir}")
    print(f"manifest={args.out_dir / 'release-evidence-package.json'}")
    print(f"verdict={manifest['summary'].get('readiness_verdict')}")


if __name__ == "__main__":
    main()
