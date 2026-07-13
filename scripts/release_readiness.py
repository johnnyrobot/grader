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
    "signing",
    "clean_machine",
)


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


def _signing_preflight() -> dict[str, Any]:
    conf = Path("app/src-tauri/tauri.conf.json")
    if not conf.exists():
        return {"status": "FAIL", "reason": "missing app/src-tauri/tauri.conf.json"}
    config = json.loads(conf.read_text())
    macos = config.get("bundle", {}).get("macOS", {})
    updater = config.get("plugins", {}).get("updater", {})
    checks = {
        "hardened_runtime": bool(macos.get("hardenedRuntime")),
        "updater_active": bool(updater.get("active")),
        "updater_pubkey_present": bool(updater.get("pubkey")),
        "keychain_profile": bool(os.environ.get("KEYCHAIN_PROFILE", "grader")),
        "signing_identity_env": bool(os.environ.get("SIGNING_IDENTITY")),
    }
    cert_proc = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        text=True,
        capture_output=True,
        check=False,
    )
    cert_output = redact_secrets(cert_proc.stdout + cert_proc.stderr)
    checks["developer_id_certificate"] = "Developer ID Application" in cert_output
    if not checks["developer_id_certificate"]:
        return {
            "status": "BLOCKED_SIGNING_CREDENTIALS",
            "checks": checks,
            "reason": "no Developer ID Application certificate found in keychain",
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
    evidence["signing"] = _signing_preflight()
    evidence["clean_machine"] = _normalize_artifact(
        args.clean_machine_artifact,
        "BLOCKED_CLEAN_MACHINE_EVIDENCE",
    )
    evidence["verdict"] = decide_verdict(evidence)
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
        "| Gate | Status |",
        "| --- | --- |",
    ]
    for gate in REQUIRED_GATES:
        status = evidence.get(gate, {}).get("status", "MISSING")
        lines.append(f"| {gate} | `{status}` |")
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
