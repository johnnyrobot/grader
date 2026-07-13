#!/usr/bin/env python3
"""Prepare or set GitHub Actions secrets for the Grader release workflow.

By default this is a dry-run inventory: it reports which required secret values
are available from environment variables or explicitly supplied local files,
without printing secret values. Passing ``--apply`` writes the available values
to GitHub with ``gh secret set``. This script never creates tags or releases.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.github_release_preflight import EXPECTED_REPO, REQUIRED_ACTIONS_SECRETS


@dataclass(frozen=True)
class SecretValue:
    name: str
    value: str | None
    source: str
    required: bool = True


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _file_text(path: Path | None) -> str | None:
    if path is None:
        return None
    if not path.exists():
        return None
    return path.read_text().strip()


def _file_base64(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _first_env(env: dict[str, str], *names: str) -> tuple[str | None, str]:
    for name in names:
        value = env.get(name)
        if value:
            return value, f"env:{name}"
    return None, ""


def collect_secret_values(
    *,
    env: dict[str, str] | None = None,
    certificate_p12: Path | None = None,
    tauri_signing_key: Path | None = None,
    signing_identity: str | None = None,
) -> list[SecretValue]:
    env = dict(os.environ if env is None else env)
    values: list[SecretValue] = []

    cert_value, cert_source = _first_env(env, "APPLE_CERTIFICATE_P12_BASE64")
    if cert_value is None and certificate_p12 is not None:
        cert_value = _file_base64(certificate_p12)
        cert_source = f"file-base64:{certificate_p12}" if cert_value else ""
    values.append(SecretValue("APPLE_CERTIFICATE_P12_BASE64", cert_value, cert_source))

    cert_password, cert_password_source = _first_env(env, "APPLE_CERTIFICATE_PASSWORD")
    values.append(SecretValue("APPLE_CERTIFICATE_PASSWORD", cert_password, cert_password_source))

    identity = signing_identity or env.get("APPLE_SIGNING_IDENTITY") or env.get("SIGNING_IDENTITY")
    identity_source = (
        "--signing-identity"
        if signing_identity
        else ("env:APPLE_SIGNING_IDENTITY" if env.get("APPLE_SIGNING_IDENTITY") else ("env:SIGNING_IDENTITY" if env.get("SIGNING_IDENTITY") else ""))
    )
    values.append(SecretValue("APPLE_SIGNING_IDENTITY", identity, identity_source))

    apple_id, apple_id_source = _first_env(env, "APPLE_ID")
    values.append(SecretValue("APPLE_ID", apple_id, apple_id_source))

    app_password, app_password_source = _first_env(env, "APPLE_APP_SPECIFIC_PASSWORD")
    values.append(SecretValue("APPLE_APP_SPECIFIC_PASSWORD", app_password, app_password_source))

    team_id, team_id_source = _first_env(env, "APPLE_TEAM_ID")
    values.append(SecretValue("APPLE_TEAM_ID", team_id, team_id_source))

    updater_key, updater_key_source = _first_env(env, "TAURI_SIGNING_KEY")
    if updater_key is None and tauri_signing_key is not None:
        updater_key = _file_text(tauri_signing_key)
        updater_key_source = f"file:{tauri_signing_key}" if updater_key else ""
    values.append(SecretValue("TAURI_SIGNING_KEY", updater_key, updater_key_source))

    updater_password, updater_password_source = _first_env(env, "TAURI_SIGNING_PRIVATE_KEY_PASSWORD")
    values.append(SecretValue("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", updater_password, updater_password_source))

    known = {item.name for item in values}
    for required in REQUIRED_ACTIONS_SECRETS:
        if required not in known:
            values.append(SecretValue(required, None, ""))
    return values


def _summary(values: list[SecretValue]) -> tuple[dict[str, Any], list[str]]:
    available = {}
    missing: list[str] = []
    for item in values:
        present = bool(item.value)
        available[item.name] = {
            "present": present,
            "source": item.source if present else "",
            "required": item.required,
        }
        if item.required and not present:
            missing.append(item.name)
    return available, missing


def _set_secret(
    *,
    runner: Runner,
    repo: str,
    name: str,
    value: str,
) -> tuple[bool, str]:
    proc = runner(
        ["gh", "secret", "set", name, "--repo", repo, "--app", "actions"],
        input=value,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, output


def collect(
    *,
    repo: str = EXPECTED_REPO,
    env: dict[str, str] | None = None,
    certificate_p12: Path | None = None,
    tauri_signing_key: Path | None = None,
    signing_identity: str | None = None,
    apply: bool = False,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    values = collect_secret_values(
        env=env,
        certificate_p12=certificate_p12,
        tauri_signing_key=tauri_signing_key,
        signing_identity=signing_identity,
    )
    available, missing = _summary(values)
    applied: dict[str, dict[str, Any]] = {}
    failures: list[str] = []

    if apply and missing:
        failures.append("refusing to apply because required secret values are missing: " + ", ".join(missing))
    elif apply:
        for item in values:
            if not item.value:
                continue
            ok, output = _set_secret(runner=runner, repo=repo, name=item.name, value=item.value)
            applied[item.name] = {"ok": ok, "output_present": bool(output)}
            if not ok:
                failures.append(f"gh secret set failed for {item.name}")

    status = "PASS" if not missing and not failures else "BLOCKED_GITHUB_RELEASE_SECRETS"
    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "applied": apply and not failures and not missing,
        "dry_run": not apply,
        "available": available,
        "missing": missing,
        "failures": failures,
        "applied_secrets": applied,
        "notes": [
            "Secret values are not included in this artifact.",
            "APPLE_CERTIFICATE_P12_BASE64 is the base64-encoded Developer ID Application .p12.",
            "The release workflow generates its temporary keychain password on the GitHub runner.",
        ],
    }


def setup_runbook() -> str:
    return """# GitHub release secret setup for Grader

Do this in a private shell. Do not paste secret values into chat or commit them.

Required GitHub Actions secrets:

- APPLE_CERTIFICATE_P12_BASE64: base64 of the Developer ID Application .p12 export.
- APPLE_CERTIFICATE_PASSWORD: password for that .p12 export.
- APPLE_SIGNING_IDENTITY: Developer ID Application identity string.
- APPLE_ID: Apple ID used for notarization.
- APPLE_APP_SPECIFIC_PASSWORD: Apple app-specific password for notarization.
- APPLE_TEAM_ID: Apple Developer Team ID.
- TAURI_SIGNING_KEY: contents of the Tauri updater private key PEM.
- TAURI_SIGNING_PRIVATE_KEY_PASSWORD: password for the encrypted updater private key.

Dry-run inventory:

```bash
uv run python scripts/github_release_secrets.py \\
  --certificate-p12 /private/path/DeveloperIDApplication.p12 \\
  --tauri-signing-key tauri-updater-key.pem \\
  --signing-identity 'Developer ID Application: Example (TEAMID)'
```

Apply to GitHub after the dry-run reports no missing values:

```bash
export APPLE_CERTIFICATE_PASSWORD='<p12-password>'
export APPLE_ID='<apple-id>'
export APPLE_APP_SPECIFIC_PASSWORD='<app-specific-password>'
export APPLE_TEAM_ID='<team-id>'
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD='<updater-key-password>'

uv run python scripts/github_release_secrets.py \\
  --certificate-p12 /private/path/DeveloperIDApplication.p12 \\
  --tauri-signing-key tauri-updater-key.pem \\
  --signing-identity 'Developer ID Application: Example (TEAMID)' \\
  --apply
```

Then run:

```bash
uv run python scripts/github_release_preflight.py --candidate-tag v<N+1>
```
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory or set GitHub Actions release secrets")
    parser.add_argument("--repo", default=EXPECTED_REPO)
    parser.add_argument("--certificate-p12", type=Path, default=None)
    parser.add_argument("--tauri-signing-key", type=Path, default=None)
    parser.add_argument("--signing-identity", default=None)
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/github-release-secrets-current.json"))
    parser.add_argument("--apply", action="store_true", help="Write available secrets to GitHub using gh secret set")
    parser.add_argument("--print-runbook", action="store_true")
    args = parser.parse_args()

    if args.print_runbook:
        print(setup_runbook())
        return

    result = collect(
        repo=args.repo,
        certificate_p12=args.certificate_p12,
        tauri_signing_key=args.tauri_signing_key,
        signing_identity=args.signing_identity,
        apply=args.apply,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"status={result['status']}")
    print(f"json={args.out}")
    if result["missing"]:
        print("missing=" + ", ".join(result["missing"]))
    if result["failures"]:
        print("failures=" + "; ".join(result["failures"]))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
