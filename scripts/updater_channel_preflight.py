#!/usr/bin/env python3
"""Preflight the live updater channel before the N-to-N+1 clean-machine test.

This script does not install an update and does not replace the required
clean-machine updater-path evidence. It catches the common release-channel
failures before a tester starts the app:

  - configured updater endpoint is missing or unreachable
  - latest.json is unsigned or malformed
  - advertised version is not newer than the installed/current version
  - platform URLs do not point at a release asset
  - advertised release assets are missing or empty
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PLATFORMS = ("darwin-aarch64", "darwin-x86_64")


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _version_parts(value: str) -> list[int]:
    parts: list[int] = []
    for token in value.split("."):
        numeric = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(numeric or "0"))
    return parts


def _version_gt(candidate: str, current: str) -> bool:
    a = _version_parts(candidate)
    b = _version_parts(current)
    while len(a) < len(b):
        a.append(0)
    while len(b) < len(a):
        b.append(0)
    return a > b


def _artifact_name_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _github_release_repo(url: str) -> str | None:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc != "github.com" or len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def _content_length(headers: httpx.Headers) -> int | None:
    raw = headers.get("content-length")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _probe_artifact_url(url: str, *, timeout: float) -> dict[str, Any]:
    """Check artifact reachability without downloading the full update payload."""
    result: dict[str, Any] = {
        "reachable": False,
        "method": None,
        "http_status": None,
        "final_url": None,
        "content_length": None,
    }
    try:
        response = httpx.head(url, timeout=timeout, follow_redirects=True)
        result.update({
            "method": "HEAD",
            "http_status": response.status_code,
            "final_url": str(response.url),
            "content_length": _content_length(response.headers),
        })
        if response.status_code == 200:
            result["reachable"] = True
            return result
        if response.status_code not in {403, 405}:
            return result

        fallback = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"Range": "bytes=0-0"},
        )
        result.update({
            "method": "GET_RANGE",
            "http_status": fallback.status_code,
            "final_url": str(fallback.url),
            "content_length": _content_length(fallback.headers),
        })
        result["reachable"] = fallback.status_code in {200, 206}
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def collect(
    *,
    config_path: Path = Path("app/src-tauri/tauri.conf.json"),
    current_version: str | None = None,
    manifest_url: str | None = None,
    expected_artifact_name: str = "Grader.dmg",
    timeout: float = 20,
    probe_artifacts: bool = True,
) -> dict[str, Any]:
    config = _load_config(config_path)
    configured_version = str(config.get("version") or "")
    current = current_version or configured_version
    updater = config.get("plugins", {}).get("updater", {})
    endpoints = updater.get("endpoints") or []
    url = manifest_url or (endpoints[0] if endpoints else "")
    release_repo = _github_release_repo(url)

    failures: list[str] = []
    details: dict[str, Any] = {
        "config_path": str(config_path),
        "config_version": configured_version,
        "current_version": current,
        "configured_endpoints": endpoints,
        "manifest_url": url,
        "release_repo": release_repo,
        "expected_artifact_name": expected_artifact_name,
        "probe_artifacts": probe_artifacts,
    }

    if not url:
        failures.append("updater manifest URL is not configured")
        manifest: dict[str, Any] = {}
    elif not release_repo:
        failures.append("updater manifest URL is not a GitHub release channel")
        manifest = {}
    else:
        try:
            response = httpx.get(url, timeout=timeout, follow_redirects=True)
            details["http_status"] = response.status_code
            details["final_url"] = str(response.url)
            if response.status_code != 200:
                failures.append(f"manifest returned HTTP {response.status_code}")
                manifest = {}
            else:
                manifest = response.json()
        except Exception as exc:
            failures.append(f"could not fetch updater manifest: {exc}")
            manifest = {}

    latest = str(manifest.get("version") or "")
    details["manifest_version"] = latest
    details["pub_date"] = manifest.get("pub_date")
    if not latest:
        failures.append("manifest version is missing")
    elif not _version_gt(latest, current):
        failures.append(f"manifest version {latest!r} is not newer than current version {current!r}")

    platforms = manifest.get("platforms") if isinstance(manifest, dict) else None
    if not isinstance(platforms, dict):
        failures.append("manifest platforms object is missing")
        platforms = {}
    platform_details: dict[str, Any] = {}
    for platform in REQUIRED_PLATFORMS:
        entry = platforms.get(platform)
        if not isinstance(entry, dict):
            failures.append(f"manifest platform {platform} is missing")
            continue
        signature = str(entry.get("signature") or "")
        artifact_url = str(entry.get("url") or "")
        artifact_name = _artifact_name_from_url(artifact_url) if artifact_url else ""
        platform_details[platform] = {
            "url": artifact_url,
            "artifact_name": artifact_name,
            "signature_present": bool(signature),
        }
        if not signature:
            failures.append(f"manifest platform {platform} is missing signature")
        if not artifact_url:
            failures.append(f"manifest platform {platform} is missing artifact URL")
        elif expected_artifact_name and artifact_name != expected_artifact_name:
            failures.append(
                f"manifest platform {platform} artifact {artifact_name!r} does not match expected {expected_artifact_name!r}"
            )
        expected_prefix = f"https://github.com/{release_repo}/releases/download/" if release_repo else ""
        if not expected_prefix or expected_prefix not in artifact_url:
            failures.append(
                f"manifest platform {platform} URL is not in the configured {release_repo or 'GitHub'} release channel"
            )
        if artifact_url and probe_artifacts:
            probe = _probe_artifact_url(artifact_url, timeout=timeout)
            platform_details[platform]["artifact_probe"] = probe
            if not probe.get("reachable"):
                failures.append(
                    f"manifest platform {platform} artifact URL is not reachable"
                    f" (HTTP {probe.get('http_status') or 'unknown'})"
                )
            elif probe.get("content_length") == 0:
                failures.append(f"manifest platform {platform} artifact URL returned empty content")
    details["platforms"] = platform_details

    status = "PASS" if not failures else "BLOCKED_UPDATER_CHANNEL"
    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "failures": failures,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight the live Tauri updater channel")
    parser.add_argument("--config", type=Path, default=Path("app/src-tauri/tauri.conf.json"))
    parser.add_argument("--current-version", default=None)
    parser.add_argument("--manifest-url", default=None)
    parser.add_argument("--expected-artifact-name", default="Grader.dmg")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--skip-artifact-probe", action="store_true", help="Validate latest.json only; do not HEAD/Range-check release assets")
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/updater-channel-current.json"))
    parser.add_argument("--no-fail", action="store_true", help="Write diagnostics but exit 0 when blocked")
    args = parser.parse_args()

    result = collect(
        config_path=args.config,
        current_version=args.current_version,
        manifest_url=args.manifest_url,
        expected_artifact_name=args.expected_artifact_name,
        timeout=args.timeout,
        probe_artifacts=not args.skip_artifact_probe,
    )
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
