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


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., CompletedProcessLike]


def redact_secrets(value: str) -> str:
    return SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)


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
        return proc.returncode == 0 and "Signature:" in (proc.stdout or ""), output, command
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
) -> dict[str, Any]:
    if env is None:
        env = dict(os.environ)
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
    }

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
            reason = "updater private key appears encrypted and TAURI_SIGNING_PRIVATE_KEY_PASSWORD is not set"
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
            "Set TAURI_SIGNING_PRIVATE_KEY_PASSWORD in the private release shell or CI secret store.",
            "Rerun: uv run python scripts/updater_key_doctor.py --signing-key /path/to/tauri-updater-key.pem",
            "After PASS, regenerate latest.json with scripts/generate_appcast.py and rerun scripts/updater_evidence.py.",
        ]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose Tauri updater signing-key readiness")
    parser.add_argument("--signing-key", type=Path, default=Path("tauri-updater-key.pem"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/updater-key.json"))
    parser.add_argument("--no-fail", action="store_true", help="Write diagnostics but exit 0 even when blocked")
    args = parser.parse_args()

    result = collect_updater_key_diagnostics(signing_key=args.signing_key)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "PASS" and not args.no_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
