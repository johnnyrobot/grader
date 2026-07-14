#!/usr/bin/env python3
"""Read-only preflight for locally signed Grader releases.

Apple signing and notarization happen on the maintainer Mac using the existing
Developer ID identity and ``notarytool`` Keychain profile. Public artifacts are
published to a separate releases-only repository so beta testers and Tauri can
read the update channel without access to the private source repository.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.release_version_guard import collect as collect_release_version


SOURCE_REPO = "johnnyrobot/grader"
RELEASE_REPO = "johnnyrobot/grader-releases"
# Backward-compatible names used by the optional hosted-runner secret helper.
EXPECTED_REPO = SOURCE_REPO
REQUIRED_ACTIONS_SECRETS = (
    "APPLE_CERTIFICATE_P12_BASE64",
    "APPLE_CERTIFICATE_PASSWORD",
    "APPLE_SIGNING_IDENTITY",
    "APPLE_ID",
    "APPLE_APP_SPECIFIC_PASSWORD",
    "APPLE_TEAM_ID",
    "TAURI_SIGNING_KEY",
    "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
)
DEFAULT_CONFIG = Path("app/src-tauri/tauri.conf.json")
DEFAULT_WORKFLOW = Path(".github/workflows/release.yml")
DEFAULT_PUBLISHER = Path("scripts/publish_local_release.py")
DEFAULT_OUT = Path("artifacts/release-evidence/github-release-preflight-current.json")

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(
    runner: Runner,
    command: list[str],
    *,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    return runner(command, text=True, capture_output=True, timeout=timeout, check=False)


def _json_from_proc(proc: subprocess.CompletedProcess[str], default: Any) -> Any:
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return default
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return default


def _configured_updater_repos(config: dict[str, Any]) -> list[str]:
    endpoints = config.get("plugins", {}).get("updater", {}).get("endpoints") or []
    repos: list[str] = []
    for endpoint in endpoints:
        parsed = urlparse(str(endpoint))
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.netloc == "github.com" and len(parts) >= 2:
            repos.append(f"{parts[0]}/{parts[1]}")
    return repos


def _workflow_has_release_test_gates(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text()
    required = (
        "uv sync --extra dev --locked",
        "uv run pytest engine/tests/ -q",
        "npm test -- --run",
        "cargo check",
        "npx tauri build --bundles app --no-sign --ci",
    )
    return all(item in text for item in required)


def _workflow_is_source_verification_only(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text()
    forbidden = (
        "APPLE_CERTIFICATE_P12_BASE64",
        "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_APP_SPECIFIC_PASSWORD",
        "softprops/action-gh-release",
    )
    return all(item not in text for item in forbidden)


def _tag_to_version(tag: str) -> str:
    value = tag.strip()
    if value.startswith("refs/tags/"):
        value = value.removeprefix("refs/tags/")
    return value.removeprefix("v")


def _release_exists(*, runner: Runner, repo: str, tag: str) -> bool:
    proc = _run(
        runner,
        ["gh", "release", "view", tag, "--repo", repo, "--json", "tagName"],
    )
    return proc.returncode == 0


def _source_tag_exists(*, runner: Runner, tag: str) -> bool:
    proc = _run(
        runner,
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"],
    )
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def collect(
    *,
    source_repo: str = SOURCE_REPO,
    release_repo: str = RELEASE_REPO,
    config_path: Path = DEFAULT_CONFIG,
    workflow_path: Path = DEFAULT_WORKFLOW,
    publisher_path: Path = DEFAULT_PUBLISHER,
    candidate_tag: str | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool | None] = {
        "gh_installed": None,
        "gh_authenticated": None,
        "source_repo_access": None,
        "release_repo_access": None,
        "release_repo_public": None,
        "updater_endpoint_matches_release_repo": None,
        "release_workflow_present": None,
        "release_workflow_test_gates": None,
        "release_workflow_avoids_hosted_signing_secrets": None,
        "local_publisher_present": None,
        "candidate_release_absent": None,
        "candidate_source_tag_absent": None,
        "candidate_version_consistent": None,
    }
    details: dict[str, Any] = {
        "publication_mode": "local_sign_and_notarize_public_assets_repo",
        "source_repo": source_repo,
        "release_repo": release_repo,
        "config_path": str(config_path),
        "workflow_path": str(workflow_path),
        "publisher_path": str(publisher_path),
        "candidate_tag": candidate_tag,
        "required_actions_secrets": [],
    }

    checks["gh_installed"] = shutil.which("gh") is not None
    if checks["gh_installed"] is not True:
        failures.append("GitHub CLI `gh` is not installed or not on PATH")

    checks["release_workflow_present"] = workflow_path.exists()
    checks["release_workflow_test_gates"] = _workflow_has_release_test_gates(workflow_path)
    checks["release_workflow_avoids_hosted_signing_secrets"] = (
        _workflow_is_source_verification_only(workflow_path)
    )
    checks["local_publisher_present"] = publisher_path.exists()
    if checks["release_workflow_present"] is not True:
        failures.append(f"release-source verification workflow is missing at {workflow_path}")
    elif checks["release_workflow_test_gates"] is not True:
        failures.append("release workflow must run Python, frontend, Rust, and Tauri build gates")
    elif checks["release_workflow_avoids_hosted_signing_secrets"] is not True:
        failures.append("release workflow still depends on hosted Apple signing secrets")
    if checks["local_publisher_present"] is not True:
        failures.append(f"local release publisher is missing at {publisher_path}")

    try:
        config = json.loads(config_path.read_text())
    except Exception as exc:
        config = {}
        failures.append(f"could not read Tauri config {config_path}: {exc}")
    configured_repos = _configured_updater_repos(config)
    details["configured_updater_repos"] = configured_repos
    checks["updater_endpoint_matches_release_repo"] = release_repo in configured_repos
    if checks["updater_endpoint_matches_release_repo"] is not True:
        failures.append(
            f"configured updater endpoint does not target public release repo {release_repo!r}"
        )

    if checks["gh_installed"]:
        auth = _run(runner, ["gh", "auth", "status", "--hostname", "github.com"])
        checks["gh_authenticated"] = auth.returncode == 0
        details["auth_status_returncode"] = auth.returncode

        repo_payloads: dict[str, Any] = {}
        for label, repo in (("source", source_repo), ("release", release_repo)):
            proc = _run(
                runner,
                ["gh", "repo", "view", repo, "--json", "nameWithOwner,url,visibility,isPrivate,defaultBranchRef"],
            )
            payload = _json_from_proc(proc, {})
            repo_payloads[label] = payload
            ok = proc.returncode == 0 and payload.get("nameWithOwner") == repo
            checks[f"{label}_repo_access"] = ok
            if not ok:
                failures.append(f"GitHub repo {repo!r} is not readable through gh")
        details["repos"] = repo_payloads
        release_payload = repo_payloads.get("release", {})
        release_public = (
            release_payload.get("visibility") == "PUBLIC"
            or release_payload.get("isPrivate") is False
        )
        checks["release_repo_public"] = release_public
        if not release_public:
            failures.append(f"release repo {release_repo!r} must be public for beta updater access")
        if auth.returncode != 0 and all(
            checks.get(key) is True for key in ("source_repo_access", "release_repo_access")
        ):
            checks["gh_authenticated"] = True
            warnings.append("gh auth status returned nonzero, but both repositories are readable")

        releases_proc = _run(
            runner,
            ["gh", "release", "list", "--repo", release_repo, "--limit", "5", "--json", "tagName,isLatest,isPrerelease,publishedAt"],
        )
        details["latest_releases"] = _json_from_proc(releases_proc, [])
        if releases_proc.returncode != 0:
            failures.append(f"could not list releases for {release_repo!r}")

        if candidate_tag:
            release_exists = _release_exists(runner=runner, repo=release_repo, tag=candidate_tag)
            source_tag_exists = _source_tag_exists(runner=runner, tag=candidate_tag)
            checks["candidate_release_absent"] = not release_exists
            checks["candidate_source_tag_absent"] = not source_tag_exists
            if release_exists:
                failures.append(f"candidate public release {candidate_tag!r} already exists")
            if source_tag_exists:
                failures.append(f"candidate source tag {candidate_tag!r} already exists on origin")

    if candidate_tag:
        version_result = collect_release_version(tag=candidate_tag)
        details["release_version_guard"] = version_result
        checks["candidate_version_consistent"] = version_result.get("status") == "PASS"
        if checks["candidate_version_consistent"] is not True:
            failures.append(
                "candidate tag/version consistency failed: "
                + "; ".join(str(item) for item in version_result.get("failures", [])[:5])
            )
    else:
        warnings.append("candidate tag not supplied; collision and version checks were skipped")

    status = "PASS" if not failures else "BLOCKED_GITHUB_RELEASE_PREFLIGHT"
    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "source_repo": source_repo,
        "release_repo": release_repo,
        "candidate_tag": candidate_tag,
        "candidate_version": _tag_to_version(candidate_tag) if candidate_tag else None,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight the local-sign/public-assets release path")
    parser.add_argument("--source-repo", default=SOURCE_REPO)
    parser.add_argument("--release-repo", default=RELEASE_REPO)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--publisher", type=Path, default=DEFAULT_PUBLISHER)
    parser.add_argument("--candidate-tag", default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-fail", action="store_true")
    args = parser.parse_args()

    result = collect(
        source_repo=args.source_repo,
        release_repo=args.release_repo,
        config_path=args.config,
        workflow_path=args.workflow,
        publisher_path=args.publisher,
        candidate_tag=args.candidate_tag,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"status={result['status']}")
    print(f"json={args.out}")
    if result["failures"]:
        print("failures=" + "; ".join(result["failures"]))
    if result["warnings"]:
        print("warnings=" + "; ".join(result["warnings"]))
    if result["status"] != "PASS" and not args.no_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
