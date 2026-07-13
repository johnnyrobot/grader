#!/usr/bin/env python3
"""Diagnose macOS release signing and notarization readiness.

The checks here intentionally avoid printing secrets. They verify that the
configured release bundle has hardened runtime/updater settings, that a
Developer ID Application certificate is visible, that codesign can actually use
the private key, and that the notarytool keychain profile works before the
release script starts mutating a built app.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(TOKEN|PASSWORD|SECRET|PRIVATE_KEY|API_KEY)[A-Z0-9_]*)=([^\s]+)"
)
BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., CompletedProcessLike]


def redact_secrets(value: str) -> str:
    redacted = SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    return BEARER_RE.sub("Bearer <redacted>", redacted)


def _run(
    runner: Runner,
    command: list[str],
    *,
    timeout: int | None = None,
) -> tuple[int, str]:
    try:
        proc = runner(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        return proc.returncode, redact_secrets((proc.stdout or "") + (proc.stderr or ""))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, redact_secrets(str(exc))


def _find_developer_id_identity(cert_output: str, signing_identity: str | None) -> str:
    if signing_identity:
        return signing_identity
    identity_match = re.search(r'"([^"]*Developer ID Application[^"]*)"', cert_output)
    return identity_match.group(1) if identity_match else ""


def _probe_codesign_private_key(
    signing_identity: str,
    *,
    runner: Runner,
) -> tuple[bool, str]:
    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", prefix="grader-codesign-probe-", delete=False) as probe:
            probe.write("#!/bin/sh\nexit 0\n")
            probe_path = Path(probe.name)
        probe_path.chmod(0o700)
        returncode, output = _run(
            runner,
            [
                "codesign",
                "--force",
                "--sign",
                signing_identity,
                "--timestamp=none",
                "--options",
                "runtime",
                str(probe_path),
            ],
            timeout=20,
        )
        return returncode == 0, output
    finally:
        try:
            if probe_path is not None:
                probe_path.unlink()
        except Exception:
            pass


def _probe_notary_profile(
    keychain_profile: str,
    *,
    runner: Runner,
) -> tuple[bool, str]:
    returncode, output = _run(
        runner,
        ["xcrun", "notarytool", "history", "--keychain-profile", keychain_profile],
        timeout=20,
    )
    return returncode == 0, output


def _remediation(require_notary: bool) -> list[str]:
    steps = [
        "Open Keychain Access and confirm the Developer ID Application certificate has its private key.",
        "Unlock the login keychain before running the release script.",
        "If codesign reports errSecInternalComponent, repair private-key ACLs with security set-key-partition-list for the login keychain.",
    ]
    if require_notary:
        steps.append(
            "Store notarization credentials with: xcrun notarytool store-credentials grader --apple-id <apple-id> --team-id <team-id> --password <app-specific-password>"
        )
    return steps


def collect_signing_diagnostics(
    *,
    config_path: Path = Path("app/src-tauri/tauri.conf.json"),
    keychain_profile: str | None = None,
    signing_identity: str | None = None,
    require_notary: bool = True,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    keychain_profile = keychain_profile or os.environ.get("KEYCHAIN_PROFILE", "grader")
    signing_identity = signing_identity or os.environ.get("SIGNING_IDENTITY") or ""
    checks: dict[str, Any] = {
        "hardened_runtime": False,
        "updater_active": False,
        "updater_pubkey_present": False,
        "keychain_profile": bool(keychain_profile),
        "signing_identity_provided": bool(signing_identity),
        "developer_id_certificate": False,
        "codesign_private_key_access": False,
        "notary_required": require_notary,
        "notary_keychain_profile": not require_notary,
    }

    if not config_path.exists():
        return {
            "status": "FAIL",
            "checks": checks,
            "reason": f"missing {config_path}",
            "remediation": ["Build from the repository root or pass --config to the Tauri config file."],
        }

    config = json.loads(config_path.read_text())
    macos = config.get("bundle", {}).get("macOS", {})
    updater = config.get("plugins", {}).get("updater", {})
    checks.update({
        "hardened_runtime": bool(macos.get("hardenedRuntime")),
        "updater_active": bool(updater.get("active")),
        "updater_pubkey_present": bool(updater.get("pubkey")),
    })

    _, cert_output = _run(runner, ["security", "find-identity", "-v", "-p", "codesigning"])
    signing_identity = _find_developer_id_identity(cert_output, signing_identity)
    checks["signing_identity_provided"] = bool(signing_identity)
    checks["developer_id_certificate"] = (
        "Developer ID Application" in cert_output or "Developer ID Application" in signing_identity
    )
    if not checks["developer_id_certificate"]:
        return {
            "status": "BLOCKED_SIGNING_CREDENTIALS",
            "checks": checks,
            "reason": "no Developer ID Application certificate found in keychain",
            "output": cert_output[:2000],
            "remediation": _remediation(require_notary),
        }

    codesign_ok, codesign_output = _probe_codesign_private_key(signing_identity, runner=runner)
    checks["codesign_private_key_access"] = codesign_ok

    notary_ok = True
    notary_output = ""
    if require_notary:
        notary_ok, notary_output = _probe_notary_profile(keychain_profile, runner=runner)
        checks["notary_keychain_profile"] = notary_ok

    credential_failures: list[str] = []
    output_parts: list[str] = []
    if not codesign_ok:
        credential_failures.append("codesign cannot use the Developer ID private key")
        output_parts.append(codesign_output)
    if require_notary and not notary_ok:
        credential_failures.append(f"notarytool keychain profile {keychain_profile!r} is unavailable or invalid")
        output_parts.append(notary_output)
    if credential_failures:
        return {
            "status": "BLOCKED_SIGNING_CREDENTIALS",
            "checks": checks,
            "reason": "; ".join(credential_failures),
            "output": "\n".join(part for part in output_parts if part)[:2000],
            "remediation": _remediation(require_notary),
        }

    missing_config = [
        name
        for name in ("hardened_runtime", "updater_active", "updater_pubkey_present")
        if checks.get(name) is not True
    ]
    if missing_config:
        return {
            "status": "FAIL",
            "checks": checks,
            "reason": "release signing/updater config incomplete",
            "missing_checks": missing_config,
            "remediation": [
                "Set hardenedRuntime=true and configure the Tauri updater pubkey/endpoints before public release."
            ],
        }

    return {
        "status": "PASS",
        "checks": checks,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "remediation": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Grader macOS signing/notarization readiness")
    parser.add_argument("--config", type=Path, default=Path("app/src-tauri/tauri.conf.json"))
    parser.add_argument("--keychain-profile", default=os.environ.get("KEYCHAIN_PROFILE", "grader"))
    parser.add_argument("--signing-identity", default=os.environ.get("SIGNING_IDENTITY", ""))
    parser.add_argument("--skip-notary", action="store_true", help="Do not require notarytool profile access")
    parser.add_argument("--out", type=Path, default=None, help="Optional JSON evidence path to write")
    parser.add_argument("--no-fail", action="store_true", help="Write diagnostics but exit 0 when blocked")
    args = parser.parse_args()

    result = collect_signing_diagnostics(
        config_path=args.config,
        keychain_profile=args.keychain_profile,
        signing_identity=args.signing_identity,
        require_notary=not args.skip_notary,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("status") != "PASS" and not args.no_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
