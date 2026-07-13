#!/usr/bin/env python3
"""Read-only GitHub release publication preflight for Grader.

This script answers a narrower question than ``updater_channel_preflight.py``:
is the repository set up so a signed N+1 release *can* be published through the
configured GitHub Actions workflow? It intentionally performs no writes. It
does not create tags, releases, assets, or secrets.
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


EXPECTED_REPO = "johnnyrobot/grader"
DEFAULT_CONFIG = Path("app/src-tauri/tauri.conf.json")
DEFAULT_WORKFLOW = Path(".github/workflows/release.yml")
DEFAULT_OUT = Path("artifacts/release-evidence/github-release-preflight-current.json")
REQUIRED_ACTIONS_SECRETS = (
    "APPLE_SIGNING_IDENTITY",
    "APPLE_KEYCHAIN_PROFILE",
    "APPLE_ID",
    "APPLE_APP_SPECIFIC_PASSWORD",
    "APPLE_TEAM_ID",
    "TAURI_SIGNING_KEY",
    "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
)


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


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _configured_updater_repos(config: dict[str, Any]) -> list[str]:
    updater = config.get("plugins", {}).get("updater", {})
    endpoints = updater.get("endpoints") or []
    repos: list[str] = []
    for endpoint in endpoints:
        parsed = urlparse(str(endpoint))
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.netloc == "github.com" and len(parts) >= 2:
            repos.append(f"{parts[0]}/{parts[1]}")
    return repos


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _secret_names(payload: Any) -> set[str]:
    if not isinstance(payload, list):
        return set()
    names: set[str] = set()
    for row in payload:
        if isinstance(row, dict) and row.get("name"):
            names.add(str(row["name"]))
    return names


def _tag_to_version(tag: str) -> str:
    value = tag.strip()
    if value.startswith("refs/tags/"):
        value = value.removeprefix("refs/tags/")
    if value.startswith("v"):
        value = value[1:]
    return value


def _release_exists(
    *,
    runner: Runner,
    repo: str,
    tag: str,
) -> bool:
    proc = _run(
        runner,
        ["gh", "release", "view", tag, "--repo", repo, "--json", "tagName"],
        timeout=30,
    )
    return proc.returncode == 0


def _git_tag_exists(*, runner: Runner, tag: str) -> bool:
    proc = _run(
        runner,
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"],
        timeout=30,
    )
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def collect(
    *,
    repo: str = EXPECTED_REPO,
    config_path: Path = DEFAULT_CONFIG,
    workflow_path: Path = DEFAULT_WORKFLOW,
    required_secrets: tuple[str, ...] = REQUIRED_ACTIONS_SECRETS,
    candidate_tag: str | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {
        "read_only": True,
        "repo": repo,
        "config_path": str(config_path),
        "workflow_path": str(workflow_path),
        "candidate_tag": candidate_tag,
        "required_secrets": list(required_secrets),
    }
    checks: dict[str, bool | None] = {
        "gh_installed": None,
        "gh_authenticated": None,
        "repo_access": None,
        "release_workflow_present": None,
        "updater_endpoint_matches_repo": None,
        "required_actions_secrets_present": None,
        "candidate_release_absent": None,
        "candidate_git_tag_absent": None,
        "candidate_version_consistent": None,
    }

    gh_installed = _gh_available()
    checks["gh_installed"] = gh_installed
    if not gh_installed:
        failures.append("GitHub CLI `gh` is not installed or not on PATH")

    checks["release_workflow_present"] = workflow_path.exists()
    if not workflow_path.exists():
        failures.append(f"release workflow is missing at {workflow_path}")

    try:
        config = _load_config(config_path)
    except Exception as exc:
        config = {}
        failures.append(f"could not read Tauri config {config_path}: {exc}")
    configured_repos = _configured_updater_repos(config)
    details["configured_updater_repos"] = configured_repos
    checks["updater_endpoint_matches_repo"] = repo in configured_repos
    if repo not in configured_repos:
        failures.append(
            f"configured updater endpoint does not target expected repo {repo!r}"
        )

    if gh_installed:
        auth = _run(runner, ["gh", "auth", "status", "--hostname", "github.com"], timeout=30)
        checks["gh_authenticated"] = auth.returncode == 0
        details["auth_status_returncode"] = auth.returncode

        repo_proc = _run(
            runner,
            [
                "gh",
                "repo",
                "view",
                repo,
                "--json",
                "nameWithOwner,url,isPrivate,defaultBranchRef",
            ],
            timeout=30,
        )
        repo_payload = _json_from_proc(repo_proc, {})
        details["repo_view"] = repo_payload
        checks["repo_access"] = (
            repo_proc.returncode == 0 and repo_payload.get("nameWithOwner") == repo
        )
        if checks["repo_access"] is not True:
            failures.append(f"GitHub repo {repo!r} is not readable through gh")
            if auth.returncode != 0:
                failures.append("GitHub CLI is not authenticated for github.com")
        elif auth.returncode != 0:
            checks["gh_authenticated"] = True
            warnings.append(
                "gh auth status returned nonzero, but the active account can read the target repo"
            )

        secret_proc = _run(
            runner,
            ["gh", "secret", "list", "--repo", repo, "--app", "actions", "--json", "name"],
            timeout=30,
        )
        secret_payload = _json_from_proc(secret_proc, [])
        present = _secret_names(secret_payload)
        missing = [secret for secret in required_secrets if secret not in present]
        details["present_actions_secrets"] = sorted(present)
        details["missing_actions_secrets"] = missing
        details["secret_list_returncode"] = secret_proc.returncode
        checks["required_actions_secrets_present"] = secret_proc.returncode == 0 and not missing
        if secret_proc.returncode != 0:
            failures.append("could not list GitHub Actions secrets for the repository")
        elif missing:
            failures.append("missing GitHub Actions secrets: " + ", ".join(missing))

        releases_proc = _run(
            runner,
            [
                "gh",
                "release",
                "list",
                "--repo",
                repo,
                "--limit",
                "5",
                "--json",
                "tagName,isLatest,isPrerelease,publishedAt",
            ],
            timeout=30,
        )
        details["latest_releases"] = _json_from_proc(releases_proc, [])
        details["release_list_returncode"] = releases_proc.returncode

        if candidate_tag:
            release_exists = _release_exists(runner=runner, repo=repo, tag=candidate_tag)
            git_tag_exists = _git_tag_exists(runner=runner, tag=candidate_tag)
            checks["candidate_release_absent"] = not release_exists
            checks["candidate_git_tag_absent"] = not git_tag_exists
            if release_exists:
                failures.append(f"candidate GitHub release {candidate_tag!r} already exists")
            if git_tag_exists:
                failures.append(f"candidate git tag {candidate_tag!r} already exists on origin")

    if candidate_tag:
        version_result = collect_release_version(tag=candidate_tag)
        details["release_version_guard"] = version_result
        checks["candidate_version_consistent"] = version_result.get("status") == "PASS"
        if version_result.get("status") != "PASS":
            failures.append(
                "candidate tag/version consistency failed: "
                + "; ".join(str(item) for item in version_result.get("failures", [])[:5])
            )
    else:
        details["candidate_version"] = None
        warnings.append(
            "candidate tag not supplied; release/tag collision and version consistency checks were skipped"
        )

    status = "PASS" if not failures else "BLOCKED_GITHUB_RELEASE_PREFLIGHT"
    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "repo": repo,
        "candidate_tag": candidate_tag,
        "candidate_version": _tag_to_version(candidate_tag) if candidate_tag else None,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only GitHub release publication preflight"
    )
    parser.add_argument("--repo", default=EXPECTED_REPO)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--candidate-tag", default=None, help="Optional N+1 tag, e.g. v0.1.1")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-fail", action="store_true", help="Write diagnostics but exit 0 when blocked")
    args = parser.parse_args()

    result = collect(
        repo=args.repo,
        config_path=args.config,
        workflow_path=args.workflow,
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
