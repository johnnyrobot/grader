#!/usr/bin/env python3
"""Build a consolidated Grader release-readiness report."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(TOKEN|PASSWORD|SECRET|PRIVATE_KEY|API_KEY)[A-Z0-9_]*)=([^\s]+)"
)
BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")

REQUIRED_GATES = (
    "quality",
    "fairness",
    "citations",
    "canvas",
    "privacy",
    "performance",
    "frontend",
    "rust",
    "updater",
    "signing",
    "clean_machine",
)

CLEAN_MACHINE_REQUIRED_CHECKS = (
    "codesign_verify",
    "stapler_validate",
    "spctl_assess",
    "first_run_models",
    "crash_recovery",
    "keyboard_only_review",
    "updater_path",
)


def _gate_summary(gate: dict[str, Any]) -> str:
    missing_checks = gate.get("missing_checks")
    if isinstance(missing_checks, list) and missing_checks:
        return "missing checks: " + ", ".join(str(check) for check in missing_checks)
    for key in ("summary", "reason"):
        value = gate.get(key)
        if value:
            return str(value)
    failures = gate.get("failures")
    if isinstance(failures, list) and failures:
        return "failures: " + "; ".join(str(failure) for failure in failures[:5])
    output = str(gate.get("output", "")).strip()
    if output:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if lines:
            if lines[0][:1] in {"✓", "✗", "⊘"} and len(lines) > 1:
                return f"{lines[0]} — {lines[1]}"[:240]
            return lines[0][:240]
    return ""


def summarize_blockers(evidence: dict[str, Any]) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    for gate in REQUIRED_GATES:
        gate_evidence = evidence.get(gate, {})
        status = str(gate_evidence.get("status", "MISSING"))
        if status not in {"PASS", "PASS_EXPERIMENTAL"}:
            blockers.append({
                "gate": gate,
                "status": status,
                "summary": _gate_summary(gate_evidence),
            })
    return blockers


def redact_secrets(value: str) -> str:
    redacted = SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    return BEARER_RE.sub("Bearer <redacted>", redacted)


def _run_jsonish(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    output = redact_secrets((proc.stdout or "") + (proc.stderr or ""))
    status = "PASS" if proc.returncode == 0 else "FAIL"
    return {"status": status, "command": command, "returncode": proc.returncode, "output": output}


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text())


def _normalize_artifact(path: Path | None, default_status: str) -> dict[str, Any]:
    data = _load_json(path)
    if data is None:
        return {"status": default_status, "artifact": str(path) if path else None}
    if "status" not in data:
        if data.get("release_status"):
            data["status"] = data["release_status"]
        elif data.get("citation_mode", {}).get("release_status"):
            data["status"] = data["citation_mode"]["release_status"]
    return data


def _normalize_citation_artifact(path: Path | None) -> dict[str, Any]:
    citation = _normalize_artifact(path, "SKIP")
    mode = citation.get("citation_mode", {})
    if mode:
        citation["mode"] = mode.get("mode")
        citation["status"] = mode.get("release_status", citation.get("status"))

    if citation.get("mode") == "experimental":
        false_positives = int(citation.get("false_positives", 0) or 0)
        retraction = citation.get("retraction_check", {})
        retraction_total = int(retraction.get("total", 0) or 0)
        retraction_caught = int(retraction.get("caught", 0) or 0)
        if false_positives == 0 and retraction_total == retraction_caught:
            citation["status"] = "PASS_EXPERIMENTAL"
            citation["accepted_experimental"] = True
            citation["reason"] = (
                "citation checks are explicitly experimental; zero false positives and all "
                "retraction fixtures are caught"
            )

    return citation


def _normalize_clean_machine_artifact(path: Path | None) -> dict[str, Any]:
    clean = _normalize_artifact(path, "BLOCKED_CLEAN_MACHINE_EVIDENCE")
    if clean.get("status") != "PASS":
        return clean
    checks = clean.get("checks", {})
    missing = [name for name in CLEAN_MACHINE_REQUIRED_CHECKS if checks.get(name) is not True]
    if missing:
        clean["status"] = "FAIL"
        clean["reason"] = "clean-machine PASS artifact is missing required passing checks"
        clean["missing_checks"] = missing
    return clean


def _signing_preflight() -> dict[str, Any]:
    conf = Path("app/src-tauri/tauri.conf.json")
    if not conf.exists():
        return {"status": "FAIL", "reason": "missing app/src-tauri/tauri.conf.json"}
    config = json.loads(conf.read_text())
    macos = config.get("bundle", {}).get("macOS", {})
    updater = config.get("plugins", {}).get("updater", {})
    keychain_profile = os.environ.get("KEYCHAIN_PROFILE", "grader")
    checks = {
        "hardened_runtime": bool(macos.get("hardenedRuntime")),
        "updater_active": bool(updater.get("active")),
        "updater_pubkey_present": bool(updater.get("pubkey")),
        "keychain_profile": bool(keychain_profile),
        "signing_identity_env": bool(os.environ.get("SIGNING_IDENTITY")),
    }
    cert_proc = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        text=True,
        capture_output=True,
        check=False,
    )
    cert_output = redact_secrets(cert_proc.stdout + cert_proc.stderr)
    identity_match = re.search(r'"([^"]*Developer ID Application[^"]*)"', cert_output)
    signing_identity = os.environ.get("SIGNING_IDENTITY") or (identity_match.group(1) if identity_match else "")
    checks["developer_id_certificate"] = "Developer ID Application" in cert_output
    if not checks["developer_id_certificate"]:
        return {
            "status": "BLOCKED_SIGNING_CREDENTIALS",
            "checks": checks,
            "reason": "no Developer ID Application certificate found in keychain",
    }
    checks["codesign_private_key_access"] = False
    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", prefix="grader-codesign-probe-", delete=False) as probe:
            probe.write("#!/bin/sh\nexit 0\n")
            probe_path = Path(probe.name)
        probe_path.chmod(0o700)
        codesign_proc = subprocess.run(
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
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        codesign_output = redact_secrets(codesign_proc.stdout + codesign_proc.stderr)
        checks["codesign_private_key_access"] = codesign_proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        codesign_output = redact_secrets(str(exc))
    finally:
        try:
            if probe_path is not None:
                probe_path.unlink()
        except Exception:
            pass
    try:
        notary_proc = subprocess.run(
            ["xcrun", "notarytool", "history", "--keychain-profile", keychain_profile],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        notary_output = redact_secrets(notary_proc.stdout + notary_proc.stderr)
        checks["notary_keychain_profile"] = notary_proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        notary_output = redact_secrets(str(exc))
        checks["notary_keychain_profile"] = False
    credential_failures: list[str] = []
    output_parts: list[str] = []
    if not checks["codesign_private_key_access"]:
        credential_failures.append("codesign cannot use the Developer ID private key")
        output_parts.append(codesign_output)
    if not checks["notary_keychain_profile"]:
        credential_failures.append(f"notarytool keychain profile {keychain_profile!r} is unavailable or invalid")
        output_parts.append(notary_output)
    if credential_failures:
        return {
            "status": "BLOCKED_SIGNING_CREDENTIALS",
            "checks": checks,
            "reason": "; ".join(credential_failures),
            "output": "\n".join(part for part in output_parts if part)[:2000],
        }
    if not checks["hardened_runtime"] or not checks["updater_active"] or not checks["updater_pubkey_present"]:
        return {"status": "FAIL", "checks": checks, "reason": "release signing/updater config incomplete"}
    return {"status": "PASS", "checks": checks}


def decide_verdict(evidence: dict[str, Any]) -> str:
    statuses = {
        gate: str(evidence.get(gate, {}).get("status", "MISSING"))
        for gate in REQUIRED_GATES
    }
    if any(status == "FAIL" or status.startswith("BLOCKED") for status in statuses.values()):
        return "blocked"
    if all(status in {"PASS", "PASS_EXPERIMENTAL"} for status in statuses.values()):
        return "supervised_public_beta"
    return "alpha"


def collect_evidence(args: argparse.Namespace) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": args.profile,
        "system": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
    }
    evidence["quality"] = _run_jsonish(
        [
            "uv",
            "run",
            "python",
            "engine/gates/quality.py",
            "--profile",
            "public_proxy",
            "--public-proxy-dir",
            args.public_proxy_dir,
        ]
    )
    evidence["fairness"] = _run_jsonish(
        [
            "uv",
            "run",
            "python",
            "engine/gates/bias.py",
            "--profile",
            "public_proxy",
            "--public-proxy-dir",
            args.public_proxy_dir,
        ]
    )
    evidence["citations"] = _normalize_citation_artifact(args.citation_artifact)
    evidence["canvas"] = _normalize_artifact(args.canvas_artifact, "SKIP")
    evidence["privacy"] = _normalize_artifact(args.privacy_artifact, "SKIP")
    evidence["performance"] = _normalize_artifact(args.performance_artifact, "SKIP")
    evidence["frontend"] = _normalize_artifact(args.frontend_artifact, "SKIP")
    evidence["rust"] = _normalize_artifact(args.rust_artifact, "SKIP")
    evidence["updater"] = _normalize_artifact(args.updater_artifact, "BLOCKED_UPDATER_EVIDENCE")
    evidence["signing"] = _signing_preflight()
    evidence["clean_machine"] = _normalize_clean_machine_artifact(args.clean_machine_artifact)
    evidence["verdict"] = decide_verdict(evidence)
    evidence["blockers"] = summarize_blockers(evidence)
    return evidence


def write_reports(evidence: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "release-readiness.json"
    md_path = out_dir / "release-readiness.md"
    json_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Grader Release Readiness",
        "",
        f"Verdict: `{evidence.get('verdict', 'unknown')}`",
        "",
        "| Gate | Status | Summary |",
        "| --- | --- | --- |",
    ]
    for gate in REQUIRED_GATES:
        gate_evidence = evidence.get(gate, {})
        status = gate_evidence.get("status", "MISSING")
        summary = _gate_summary(gate_evidence).replace("|", "\\|")
        lines.append(f"| {gate} | `{status}` | {summary} |")
    blockers = evidence.get("blockers") or summarize_blockers(evidence)
    if blockers:
        lines.extend(["", "## Blocking Items", ""])
        for blocker in blockers:
            suffix = f" — {blocker['summary']}" if blocker.get("summary") else ""
            lines.append(f"- `{blocker['gate']}`: `{blocker['status']}`{suffix}")
    lines.extend(
        [
            "",
            "Public beta requires every listed gate to pass or be explicitly disabled with honest product labeling.",
            "",
        ]
    )
    md_path.write_text("\n".join(lines))
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Write Grader release readiness reports")
    parser.add_argument("--profile", default="public_beta")
    parser.add_argument("--public-proxy-dir", default="fixtures/public_proxy")
    parser.add_argument("--citation-artifact", type=Path, default=None)
    parser.add_argument("--canvas-artifact", type=Path, default=None)
    parser.add_argument("--privacy-artifact", type=Path, default=None)
    parser.add_argument("--performance-artifact", type=Path, default=None)
    parser.add_argument("--frontend-artifact", type=Path, default=None)
    parser.add_argument("--rust-artifact", type=Path, default=None)
    parser.add_argument("--updater-artifact", type=Path, default=None)
    parser.add_argument("--clean-machine-artifact", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-readiness"))
    args = parser.parse_args()

    evidence = collect_evidence(args)
    json_path, md_path = write_reports(evidence, args.out)
    print(f"verdict={evidence['verdict']}")
    print(f"json={json_path}")
    print(f"markdown={md_path}")


if __name__ == "__main__":
    main()
