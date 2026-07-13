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
import time
import urllib.error
import urllib.request
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
    engine_url: str = "http://127.0.0.1:8756"
    engine_token: str = "dev"
    model: str = "grader-text:8b"
    timeout_seconds: int = 180


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


def _engine_request(config: CanvasE2EConfig, method: str, path: str, data: dict | None = None) -> dict:
    body = json.dumps(data or {}).encode() if data is not None else None
    req = urllib.request.Request(
        f"{config.engine_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.engine_token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    if not payload:
        return {}
    return json.loads(payload.decode("utf-8"))


def _wait_for_drafts(config: CanvasE2EConfig, assignment_key: str, expected: int) -> list[dict]:
    deadline = time.monotonic() + config.timeout_seconds
    last_items: list[dict] = []
    while time.monotonic() < deadline:
        queue = _engine_request(config, "GET", f"/api/queue?assignment_id={assignment_key}")
        items = queue.get("items", [])
        last_items = items
        ready = [item for item in items if item.get("ai_scores")]
        if len(items) >= expected and ready:
            return ready
        time.sleep(2)
    raise TimeoutError(
        f"timed out waiting for drafted Canvas grades; last queue size={len(last_items)}"
    )


def _read_back_from_canvas(course_id: int, assignment_id: int, posted_scores: dict[str, Any]) -> list[dict]:
    from grader.canvas import get_submission

    rows: list[dict] = []
    for user_id, expected_score in posted_scores.items():
        sub = get_submission(course_id, assignment_id, user_id)
        rows.append({
            "submission_id": user_id,
            "expected_score": float(expected_score),
            "actual_score": sub.get("score"),
            "has_rubric": bool(sub.get("rubric_assessment")),
            "comments": len(sub.get("submission_comments") or []),
        })
    return rows


def run(config: CanvasE2EConfig) -> dict[str, Any]:
    if not config.apply:
        return _blocked("DRY_RUN", "pass --apply to perform guarded live Canvas writes", config)

    validate_course_name(config.course_name, config.course_prefix)
    missing = ["CANVAS_BASE_URL"] if not os.environ.get("CANVAS_BASE_URL") else []
    if not (
        os.environ.get("CANVAS_TOKEN")
        or os.environ.get("CANVAS_API_TOKEN")
        or os.environ.get("CANVAS_ACCESS_TOKEN")
    ):
        missing.append("CANVAS_TOKEN or CANVAS_API_TOKEN")
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

    try:
        health = _engine_request(config, "GET", "/health")
    except Exception as exc:
        return _blocked("BLOCKED_ENGINE", f"local Grader engine is not reachable: {exc}", config)
    if health.get("status") != "ok":
        return _blocked("BLOCKED_ENGINE", f"unexpected engine health response: {health}", config)

    assignment_key = f"canvas_{config.course_id}_{config.assignment_id}"
    try:
        _engine_request(config, "POST", "/api/rubrics", {
            "title": "ZZ-GRADER-TEST Live E2E Rubric",
            "rubric_text": (
                "# ZZ-GRADER-TEST Live E2E Rubric\n"
                "## Thesis (5 pts, analytic)\n"
                "- 5: Clear, specific claim\n"
                "- 3: General but relevant claim\n"
                "- 1: Unclear claim\n"
                "## Evidence (5 pts, analytic)\n"
                "- 5: Specific, relevant evidence\n"
                "- 3: Some relevant evidence\n"
                "- 1: Little support\n"
            ),
        })
        imported = _engine_request(config, "POST", "/api/canvas/import", {
            "course_id": config.course_id,
            "assignment_id": config.assignment_id,
            "download_attachments": True,
        })
        if int(imported.get("imported", 0) or 0) < 1:
            return _blocked(
                "BLOCKED_CANVAS_DATA",
                f"no ungraded Canvas submissions imported: {imported}",
                config,
            )

        _engine_request(config, "POST", "/api/grade/batch", {
            "assignment_id": assignment_key,
            "model": config.model,
        })
        drafts = _wait_for_drafts(config, assignment_key, int(imported["imported"]))
        grade_id = drafts[0]["id"]
        _engine_request(config, "POST", f"/api/grades/{grade_id}/approve", {})
        posted = _engine_request(config, "POST", "/api/canvas/post", {
            "course_id": config.course_id,
            "assignment_id": config.assignment_id,
            "grade_ids": [grade_id],
        })
        read_back = _read_back_from_canvas(
            config.course_id,
            config.assignment_id,
            posted.get("posted_scores", {}),
        )
        result = {
            "status": "PASS",
            "applied": True,
            "course_id": config.course_id,
            "assignment_id": config.assignment_id,
            "assignment_key": assignment_key,
            "imported": imported.get("imported", 0),
            "grade_ids": [grade_id],
            "posted": posted.get("posted", 0),
            "progress_state": posted.get("progress_state", "unknown"),
            "read_back": read_back,
        }
        result.update(summarize_result(result))
        return result
    except Exception as exc:
        return _blocked("FAIL", f"live Canvas E2E failed: {exc}", config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarded Grader live Canvas E2E harness")
    parser.add_argument("--course-id", type=int, default=None)
    parser.add_argument("--assignment-id", type=int, default=None)
    parser.add_argument("--course-name", default="ZZ-GRADER-TEST-SANDBOX")
    parser.add_argument("--course-prefix", default="ZZ-GRADER-TEST")
    parser.add_argument("--engine-url", default=os.environ.get("GRADER_ENGINE_URL", "http://127.0.0.1:8756"))
    parser.add_argument("--engine-token", default=os.environ.get("GRADER_ENGINE_TOKEN", "dev"))
    parser.add_argument("--model", default="grader-text:8b")
    parser.add_argument("--timeout-seconds", type=int, default=180)
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
        engine_url=args.engine_url,
        engine_token=args.engine_token,
        model=args.model,
        timeout_seconds=args.timeout_seconds,
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
