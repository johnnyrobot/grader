#!/usr/bin/env python3
"""Diagnose Tauri updater signing-key readiness.

This is a focused preflight for the remaining updater-manifest blocker. It
signs a tiny temporary file with ``cargo tauri signer sign`` and records whether
the configured updater private key and optional
``TAURI_SIGNING_PRIVATE_KEY_PASSWORD`` work in a non-interactive release run.

The JSON output is secret-free. It never prints or stores the private key or
password value.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(TOKEN|PASSWORD|SECRET|PRIVATE_KEY|API_KEY)[A-Z0-9_]*)=([^\s]+)"
)
SIGNATURE_RE = re.compile(r"(?im)^\s*(?:Public\s+)?Signature:\s*\S+")


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., CompletedProcessLike]


def redact_secrets(value: str) -> str:
    return SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)


def _load_password_from_keychain(
    *,
    service: str,
    account: str | None = None,
    runner: Runner = subprocess.run,
) -> tuple[str | None, dict[str, Any]]:
    command = ["security", "find-generic-password", "-w", "-s", service]
    if account:
        command.extend(["-a", account])
    try:
        proc = runner(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, {
            "service": service,
            "account": account,
            "ok": False,
            "output": redact_secrets(str(exc)),
        }
    password = (proc.stdout or "").rstrip("\n")
    return (password or None), {
        "service": service,
        "account": account,
        "ok": proc.returncode == 0 and bool(password),
        "returncode": proc.returncode,
        "stderr": redact_secrets((proc.stderr or "").strip()),
    }


def _run_signer(
    *,
    signing_key: Path,
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
) -> tuple[bool, str, list[str]]:
    probe_path: Path | None = None
    command: list[str] = []
    try:
        with tempfile.NamedTemporaryFile("wb", prefix="grader-updater-key-probe-", delete=False) as probe:
            probe.write(b"grader updater signing probe\n")
            probe_path = Path(probe.name)
        command = [
            "cargo",
            "tauri",
            "signer",
            "sign",
            "--private-key-path",
            str(signing_key),
            str(probe_path),
        ]
        proc = runner(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
            env=env,
        )
        output = redact_secrets(((proc.stdout or "") + (proc.stderr or "")).strip())
        return proc.returncode == 0 and bool(SIGNATURE_RE.search(proc.stdout or "")), output, command
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, redact_secrets(str(exc)), command
    finally:
        try:
            if probe_path is not None:
                probe_path.unlink()
        except Exception:
            pass


def collect_updater_key_diagnostics(
    *,
    signing_key: Path = Path("tauri-updater-key.pem"),
    runner: Runner = subprocess.run,
    env: dict[str, str] | None = None,
    password_keychain_service: str | None = None,
    password_keychain_account: str | None = None,
) -> dict[str, Any]:
    if env is None:
        env = dict(os.environ)
    env = dict(env)
    password_env_present = bool(env.get("TAURI_SIGNING_PRIVATE_KEY_PASSWORD"))
    password_source = "env" if password_env_present else "missing"
    keychain_details: dict[str, Any] | None = None
    if not password_env_present and password_keychain_service:
        password, keychain_details = _load_password_from_keychain(
            service=password_keychain_service,
            account=password_keychain_account,
            runner=runner,
        )
        if password:
            env["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"] = password
            password_source = "keychain"
    password_present = bool(env.get("TAURI_SIGNING_PRIVATE_KEY_PASSWORD"))
    checks: dict[str, bool] = {
        "signing_key_present": signing_key.exists(),
        "password_env_present": password_present,
        "signing_probe": False,
    }
    details: dict[str, Any] = {
        "signing_key": {
            "path": str(signing_key),
            "present": signing_key.exists(),
            "size_bytes": signing_key.stat().st_size if signing_key.exists() else None,
        },
        "password_env_present": password_present,
        "password_env_name": "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
        "password_source": password_source,
    }
    if keychain_details is not None:
        details["password_keychain"] = keychain_details

    output = ""
    command: list[str] = []
    if signing_key.exists():
        ok, output, command = _run_signer(signing_key=signing_key, runner=runner, env=env)
        checks["signing_probe"] = ok
        details["signing_probe"] = {
            "command": command,
            "ok": ok,
            "output": output,
        }
    else:
        details["signing_probe"] = {
            "command": [],
            "ok": False,
            "output": "updater signing key file is missing",
        }

    missing = [name for name, ok in checks.items() if not ok]
    if checks["signing_key_present"] and checks["signing_probe"]:
        status = "PASS"
        reason = ""
    else:
        status = "BLOCKED_UPDATER_KEY"
        if not checks["signing_key_present"]:
            reason = f"updater signing key not found at {signing_key}"
        elif not password_present and "password" in output.lower():
            reason = "updater private key appears encrypted and no password was available from TAURI_SIGNING_PRIVATE_KEY_PASSWORD or Keychain"
        elif "password" in output.lower():
            reason = "updater private key password was rejected or unavailable"
        else:
            reason = "updater signing probe failed"

    result: dict[str, Any] = {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "missing_checks": [] if status == "PASS" else missing,
        "details": details,
    }
    if reason:
        result["reason"] = reason
        result["remediation"] = [
            "Set TAURI_SIGNING_PRIVATE_KEY_PASSWORD in the private release shell or CI secret store, or store it in macOS Keychain and pass --password-keychain-service.",
            "Rerun: uv run python scripts/updater_key_doctor.py --signing-key /path/to/tauri-updater-key.pem",
            "After PASS, regenerate latest.json with scripts/generate_appcast.py and rerun scripts/updater_evidence.py.",
        ]
    return result


def private_shell_runbook() -> str:
    return """# Tauri updater-key private-shell runbook

Do this only in a private local release shell. Do not paste the updater key password into chat,
commit it, or write it to a repo file.

## Option A: one-shell environment variable

```bash
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD='<updater-key-password>'
uv run python scripts/updater_key_doctor.py --signing-key tauri-updater-key.pem --out artifacts/release-evidence/updater-key-current.json
uv run python scripts/generate_appcast.py --app app/src-tauri/target/release/bundle/macos/Grader.app --version 0.1.1 --signing-key tauri-updater-key.pem --output latest.json
uv run python scripts/updater_evidence.py --manifest latest.json --artifact app/src-tauri/target/release/bundle/macos/Grader.app.tar.gz --version 0.1.1 --out artifacts/release-evidence/updater-current.json
unset TAURI_SIGNING_PRIVATE_KEY_PASSWORD
```

## Option B: macOS Keychain item

```bash
security add-generic-password -U \\
  -a grader-updater \\
  -s TAURI_SIGNING_PRIVATE_KEY_PASSWORD \\
  -w '<updater-key-password>'

uv run python scripts/updater_key_doctor.py \\
  --signing-key tauri-updater-key.pem \\
  --password-keychain-service TAURI_SIGNING_PRIVATE_KEY_PASSWORD \\
  --password-keychain-account grader-updater \\
  --out artifacts/release-evidence/updater-key-current.json

uv run python scripts/generate_appcast.py \\
  --app app/src-tauri/target/release/bundle/macos/Grader.app \\
  --version 0.1.1 \\
  --signing-key tauri-updater-key.pem \\
  --password-keychain-service TAURI_SIGNING_PRIVATE_KEY_PASSWORD \\
  --password-keychain-account grader-updater \\
  --output latest.json

uv run python scripts/updater_evidence.py \\
  --manifest latest.json \\
  --artifact app/src-tauri/target/release/bundle/macos/Grader.app.tar.gz \\
  --version 0.1.1 \\
  --out artifacts/release-evidence/updater-current.json
```
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose Tauri updater signing-key readiness")
    parser.add_argument("--signing-key", type=Path, default=Path("tauri-updater-key.pem"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/updater-key.json"))
    parser.add_argument("--password-keychain-service", default=None, help="Read updater key password from this macOS Keychain service without printing it")
    parser.add_argument("--password-keychain-account", default=None, help="Optional macOS Keychain account for --password-keychain-service")
    parser.add_argument("--print-private-runbook", action="store_true", help="Print a secret-free private-shell runbook")
    parser.add_argument("--no-fail", action="store_true", help="Write diagnostics but exit 0 even when blocked")
    args = parser.parse_args()

    if args.print_private_runbook:
        print(private_shell_runbook())
        return

    result = collect_updater_key_diagnostics(
        signing_key=args.signing_key,
        password_keychain_service=args.password_keychain_service,
        password_keychain_account=args.password_keychain_account,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "PASS" and not args.no_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
