#!/usr/bin/env python3
"""Guarded live Canvas import -> approve -> post -> read-back harness.

The default mode is a dry run. Live writes require --apply, Canvas
credentials, and a course name that starts with ZZ-GRADER-TEST.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SECRET_RE = re.compile(
    r"(?i)\b(token|authorization|api[_-]?key|password|secret)=([^\s&]+)"
)
BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


@dataclass
class CanvasE2EConfig:
    course_id: int | None
    assignment_id: int | None
    course_name: str
    apply: bool
    course_prefix: str = "ZZ-GRADER-TEST"


def redact(value: str) -> str:
    """Redact common secrets and direct student contact identifiers from logs."""
    redacted = SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    redacted = BEARER_RE.sub("Bearer <redacted>", redacted)
    redacted = EMAIL_RE.sub("<redacted-email>", redacted)
    return redacted


def validate_course_name(name: str, prefix: str = "ZZ-GRADER-TEST") -> None:
    if not name.startswith(prefix):
        raise ValueError(f"refusing Canvas write: course name must start with {prefix!r}")


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if result.get("progress_state") != "completed":
        failures.append("Canvas Progress did not reach completed")
    if int(result.get("posted", 0) or 0) < 1:
        failures.append("no approved grades were posted")
    for row in result.get("read_back", []):
        if row.get("expected_score") != row.get("actual_score"):
            failures.append(
                f"read-back mismatch for {row.get('submission_id', '<unknown>')}"
            )
    status = "FAIL" if failures else "PASS"
    return {
        "status": status,
        "posted": int(result.get("posted", 0) or 0),
        "progress_state": result.get("progress_state", "unknown"),
        "failures": failures,
    }


def _blocked(status: str, reason: str, config: CanvasE2EConfig) -> dict[str, Any]:
    return {
        "status": status,
        "reason": redact(reason),
        "course_id": config.course_id,
        "assignment_id": config.assignment_id,
        "course_prefix": config.course_prefix,
        "applied": False,
    }


def run(config: CanvasE2EConfig) -> dict[str, Any]:
    if not config.apply:
        return _blocked("DRY_RUN", "pass --apply to perform guarded live Canvas writes", config)

    validate_course_name(config.course_name, config.course_prefix)
    missing = [
        name for name in ("CANVAS_BASE_URL", "CANVAS_TOKEN") if not os.environ.get(name)
    ]
    if missing:
        return _blocked(
            "BLOCKED_CANVAS_CREDENTIALS",
            f"missing Canvas credential env vars: {', '.join(missing)}",
            config,
        )
    if config.course_id is None or config.assignment_id is None:
        return _blocked(
            "BLOCKED_CANVAS_TARGET",
            "--course-id and --assignment-id are required for live Canvas E2E",
            config,
        )

    return _blocked(
        "BLOCKED_CANVAS_IMPLEMENTATION",
        "live app import -> approve -> post -> read-back orchestration is not implemented yet",
        config,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarded Grader live Canvas E2E harness")
    parser.add_argument("--course-id", type=int, default=None)
    parser.add_argument("--assignment-id", type=int, default=None)
    parser.add_argument("--course-name", default="ZZ-GRADER-TEST-SANDBOX")
    parser.add_argument("--course-prefix", default="ZZ-GRADER-TEST")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    config = CanvasE2EConfig(
        course_id=args.course_id,
        assignment_id=args.assignment_id,
        course_name=args.course_name,
        course_prefix=args.course_prefix,
        apply=args.apply and not args.dry_run,
    )
    try:
        result = run(config)
    except Exception as exc:
        result = {"status": "FAIL", "reason": redact(str(exc)), "applied": False}

    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    sys.exit(1 if result.get("status") == "FAIL" else 0)


if __name__ == "__main__":
    main()
