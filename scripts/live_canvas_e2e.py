#!/usr/bin/env python3
"""Guarded live Canvas import -> approve -> post -> read-back harness.

The default mode is a dry run. Live writes require --apply, Canvas
credentials, and a course name that starts with ZZ-GRADER-TEST.

Two submissions are exercised, because they answer different questions:

  * the primary one takes the ordinary path — import, teacher override, bulk
    post, Progress polled to completed — and its comment attachment is read
    back off the submission to show FR-3.4 reaches Canvas rather than merely
    sending what the docs describe;
  * the second one is posted while the engine points at an unroutable Canvas,
    so the upload fails on purpose. FR-3.5 must then queue the post *with the
    file still to send*, keep the teacher's approval, and deliver both when the
    queue is flushed.

Both read-backs also check `/courses/:c/files`. A student's annotated work in
shared course content is the defect this endpoint choice exists to avoid, and
only Canvas can say where the file actually landed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Distinguishes "work the commit out yourself" from an explicit `None`, which a
# caller outside a checkout passes to say there is no commit to record.
_UNSET = object()

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SECRET_RE = re.compile(
    r"(?i)\b(token|authorization|api[_-]?key|password|secret)=([^\s&]+)"
)
BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")

# Where the engine is pointed while the upload has to fail. A closed loopback
# port refuses instantly, so the probe is a real connection failure rather than
# a timeout, and no request leaves this machine while it is in force.
UNROUTABLE_CANVAS = "https://127.0.0.1:1"

# Every file this harness sends carries the sandbox marker, so anything it
# leaves behind on Canvas is identifiable as test data.
FEEDBACK_PREFIX = "ZZ-GRADER-TEST-feedback"

# Named in one place because the engine refuses to guess between saved rubrics,
# and grading against the wrong one is silent — the scores still look plausible.
RUBRIC_TITLE = "ZZ-GRADER-TEST Live E2E Rubric"


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
    target_canvas_user_id: int | None = None
    # Attachments may only be sent from the engine's own data directory, so the
    # harness has to write its feedback file where the engine will accept it.
    attachment_dir: Path = Path(os.environ.get("GRADER_DATA_DIR", "fixtures"))


def redact(value: str) -> str:
    """Redact common secrets and direct student contact identifiers from logs."""
    redacted = SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    redacted = BEARER_RE.sub("Bearer <redacted>", redacted)
    redacted = EMAIL_RE.sub("<redacted-email>", redacted)
    return redacted


def validate_course_name(name: str, prefix: str = "ZZ-GRADER-TEST") -> None:
    if not name.startswith(prefix):
        raise ValueError(f"refusing Canvas write: course name must start with {prefix!r}")


def _attachment_failures(attachment: dict[str, Any], label: str) -> list[str]:
    """What Canvas has to show for an uploaded file to count as delivered.

    The POST's status code is not evidence: it says Canvas accepted a request,
    not that the file is on that student's submission comment and nowhere a
    classmate can reach it.
    """
    failures: list[str] = []
    if not attachment:
        return [f"no {label} attachment evidence in this run"]
    if not attachment.get("read_back"):
        failures.append(
            f"{label} attachment was not read back from the submission comment"
        )
    elif attachment.get("submission_comment_id") is None:
        failures.append(f"{label} attachment is not scoped to a submission comment")
    if attachment.get("in_shared_course_files"):
        failures.append(f"{label} attachment reached shared course files")
    return failures


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
        if int(row.get("comments", 0) or 0) < 1:
            failures.append(
                f"missing Canvas feedback comment for {row.get('submission_id', '<unknown>')}"
            )

    failures += _attachment_failures(result.get("attachment") or {}, "posted")

    queued = result.get("attachment_queue") or {}
    if not queued:
        failures.append("no attachment upload-failure evidence in this run")
    else:
        if int(queued.get("queued", 0) or 0) < 1:
            failures.append("a failed attachment upload did not take the post queue")
        if not queued.get("pending_file_preserved"):
            failures.append("the queued post lost the attachment it was waiting to send")
        if not queued.get("approval_intact"):
            failures.append("a failed attachment upload threw away the approval")
        if int(queued.get("flushed", 0) or 0) < 1:
            failures.append("the post queue did not deliver the grade on flush")
        failures += _attachment_failures(queued, "queued")

    status = "FAIL" if failures else "PASS"
    return {
        "status": status,
        "posted": int(result.get("posted", 0) or 0),
        "progress_state": result.get("progress_state", "unknown"),
        "failures": failures,
    }


def _head_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def stamp_provenance(result: dict[str, Any], commit: str | None = _UNSET) -> dict[str, Any]:
    """Say when this ran and against what.

    `AGENTS.md` freshness-checks Canvas results against the current git commit,
    and the release docs cite this artifact as proof of current behaviour. With
    no stamp there is nothing to check: a run from three commits ago is
    indistinguishable from one made just now.
    """
    if commit is _UNSET:
        commit = _head_commit()
    return {
        **result,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": commit or "unknown",
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


def _wait_for_drafts(
    config: CanvasE2EConfig, assignment_key: str, submission_ids: list[str]
) -> dict[str, dict]:
    """Wait until every requested submission has a draft. Keyed by submission.

    Keyed rather than ordered because the queue is ordered by creation time and
    the two submissions here have different jobs: one takes the ordinary post
    path, the other the upload-failure probe.
    """
    wanted = set(submission_ids)
    deadline = time.monotonic() + config.timeout_seconds
    last_items: list[dict] = []
    while time.monotonic() < deadline:
        queue = _engine_request(config, "GET", f"/api/queue?assignment_id={assignment_key}")
        items = queue.get("items", [])
        last_items = items
        ready = {
            item["submission_id"]: item
            for item in items
            if item.get("ai_scores") and item.get("submission_id") in wanted
        }
        if wanted <= set(ready):
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


def _approve_grade(config: CanvasE2EConfig, grade_id: str) -> dict:
    """Approve a draft, treating a previously approved draft as idempotent."""
    try:
        return _engine_request(config, "POST", f"/api/grades/{grade_id}/approve", {})
    except RuntimeError as exc:
        message = str(exc)
        if "HTTP 409" in message and "already approved" in message.lower():
            return {"status": "already_approved"}
        raise


def _teacher_override(config: CanvasE2EConfig, grade: dict[str, Any]) -> dict:
    """Apply a deterministic teacher override so Canvas read-back has a nonzero score/comment."""
    draft_scores = grade.get("ai_scores") or {}
    score_keys = list(draft_scores) or ["thesis", "evidence"]
    teacher_scores = {
        key: (2.0 if i < 2 else 0.0)
        for i, key in enumerate(score_keys)
    }
    return _engine_request(config, "POST", f"/api/grades/{grade['id']}/override", {
        "teacher_scores": teacher_scores,
        "teacher_feedback": (
            "GRADER TEST: teacher-approved feedback comment for Canvas read-back."
        ),
    })


def _select_submissions(
    imported: dict[str, Any],
    target_canvas_user_id: int | None,
    count: int,
    with_work: set[str] | None = None,
) -> list[str]:
    """Pick `count` imported submissions, the requested student first.

    Returns fewer than asked for — including none — rather than substituting a
    student the caller did not name, and the caller blocks on that.

    `with_work` narrows it to students who actually submitted something.
    Canvas keeps a row for one who never did, and the engine imports it, so
    without this the run can grade an empty submission and read the score back
    as though it had graded work.
    """
    submissions = imported.get("submissions") or []
    if with_work is not None:
        submissions = [
            row for row in submissions
            if str(row.get("canvas_user_id")) in with_work
        ]
    ordered = [row.get("id") for row in submissions]
    if target_canvas_user_id is not None:
        target = str(target_canvas_user_id)
        first = next(
            (row.get("id") for row in submissions
             if str(row.get("canvas_user_id")) == target),
            None,
        )
        if first is None:
            return []
        ordered = [first] + [sub_id for sub_id in ordered if sub_id != first]
    selected = [sub_id for sub_id in ordered if sub_id][:count]
    return selected if len(selected) == count else []


def _canvas_user_ids_with_work(course_id: int, assignment_id: int) -> set[str]:
    """Who actually handed something in.

    Canvas returns a submission row for every enrolled student, submitted or
    not. Grading one that is empty produces a plausible-looking score for a
    student who wrote nothing — and read back off Canvas it is indistinguishable
    from a real one.
    """
    from grader.canvas import list_submissions

    return {
        str(submission.get("user_id"))
        for submission in list_submissions(course_id, assignment_id)
        if (submission.get("body") or "").strip() or submission.get("attachments")
    }


def _find_comment_attachment(submission: dict[str, Any], filename: str) -> dict | None:
    """Where on this submission Canvas is holding `filename`, if anywhere.

    Reads the comment it hangs off, not just the file: an id alone would not
    say the file is scoped to one student's feedback.
    """
    for comment in submission.get("submission_comments") or []:
        for attachment in comment.get("attachments") or []:
            names = {attachment.get("display_name"), attachment.get("filename")}
            if filename in names:
                return {
                    "canvas_attachment_id": attachment.get("id"),
                    "submission_comment_id": comment.get("id"),
                }
    return None


def _count_course_files_named(course_id: int, filename: str) -> int:
    """How many files with this name are in shared course content.

    The number that makes the run a PASS is zero. `/courses/:c/files` is where
    every student in the course can reach a file, and an annotated submission
    there is one student's work handed to their classmates.
    """
    import httpx
    from grader.canvas import resolve_canvas_base_url, resolve_canvas_token

    base = resolve_canvas_base_url().rstrip("/")
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        response = client.get(
            f"{base}/api/v1/courses/{course_id}/files",
            headers={
                "Authorization": f"Bearer {resolve_canvas_token()}",
                "Accept": "application/json",
            },
            params={"search_term": filename, "per_page": 100},
        )
        response.raise_for_status()
        return len(response.json())


def _read_attachment_back(
    config: CanvasE2EConfig, user_id: str, filename: str
) -> dict[str, Any]:
    """Ask Canvas where the file went, rather than trusting the upload's 201."""
    from grader.canvas import get_submission

    submission = get_submission(config.course_id, config.assignment_id, user_id)
    found = _find_comment_attachment(submission, filename)
    return {
        "filename": filename,
        "canvas_user_id": user_id,
        "read_back": found is not None,
        "canvas_attachment_id": (found or {}).get("canvas_attachment_id"),
        "submission_comment_id": (found or {}).get("submission_comment_id"),
        "in_shared_course_files": _count_course_files_named(
            config.course_id, filename
        ) > 0,
    }


def _write_feedback_file(config: CanvasE2EConfig, grade_id: str) -> Path:
    """An annotated-feedback stand-in, written where the engine will send it.

    `AttachmentStore` refuses anything outside the engine's data directory, so
    a file written anywhere else is rejected before Canvas is ever reached.
    """
    directory = config.attachment_dir.expanduser().resolve() / "live-e2e"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{FEEDBACK_PREFIX}-{grade_id}.md"
    path.write_text(
        "# GRADER TEST feedback\n\n"
        "Synthetic annotated feedback for the guarded live Canvas E2E. "
        "No student work is in this file.\n"
    )
    return path


def _canvas_user_ids(imported: dict[str, Any]) -> dict[str, str]:
    return {
        row["id"]: str(row["canvas_user_id"])
        for row in imported.get("submissions") or []
    }


def _pending_file_is_preserved(items: list[dict[str, Any]], filename: str) -> bool:
    """Is the file still listed as one the queue has to send?

    Reads `pending_files` as the JSON list it is. Searching the row as text
    would also match `last_error`, which quotes the failed path straight back —
    so a queue that dropped the file would still look like one that kept it.
    """
    for item in items:
        raw = item.get("pending_files")
        if not raw:
            continue
        try:
            paths = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if any(Path(str(path)).name == filename for path in paths):
            return True
    return False


def _grade_is_approved(config: CanvasE2EConfig, assignment_key: str, grade_id: str) -> bool:
    queue = _engine_request(config, "GET", f"/api/queue?assignment_id={assignment_key}")
    for item in queue.get("items", []):
        if item.get("id") == grade_id:
            return bool(item.get("approved"))
    return False


def _run_upload_failure_probe(
    config: CanvasE2EConfig,
    draft: dict[str, Any],
    canvas_user_id: str,
    assignment_key: str,
) -> dict[str, Any]:
    """Fail the attachment upload on purpose, then make FR-3.5 recover it.

    What is being checked is not that a failure is survivable in principle, but
    that this one costs nothing: the teacher's approval stands, the file they
    attached is still queued to send, and flushing puts both on the submission.
    """
    grade_id = draft["id"]
    _teacher_override(config, draft)
    feedback_file = _write_feedback_file(config, grade_id)
    _engine_request(config, "POST", f"/api/grades/{grade_id}/attachments", {
        "path": str(feedback_file),
    })

    with _canvas_unreachable(config):
        queued = _engine_request(config, "POST", "/api/canvas/post", {
            "course_id": config.course_id,
            "assignment_id": config.assignment_id,
            "grade_ids": [grade_id],
        })
        pending = _engine_request(config, "GET", "/api/canvas/post-queue")

    preserved = _pending_file_is_preserved(pending.get("items", []), feedback_file.name)
    approval_intact = _grade_is_approved(config, assignment_key, grade_id)
    flushed = _engine_request(config, "POST", "/api/canvas/flush")

    return {
        "queued": int(queued.get("queued", 0) or 0),
        "queued_reason": redact(str(queued.get("queued_reason", ""))),
        "pending_file_preserved": preserved,
        "approval_intact": approval_intact,
        "flushed": int(flushed.get("posted", 0) or 0),
        "remaining_in_queue": int(flushed.get("remaining", 0) or 0),
        **_read_attachment_back(config, canvas_user_id, feedback_file.name),
    }


@contextmanager
def _canvas_unreachable(config: CanvasE2EConfig):
    """Point the engine at a Canvas that cannot answer, then put it back.

    This is how the harness produces a genuine upload failure: no fixture, no
    patched function, just the engine finding nothing at the other end. The
    restore is in a finally because leaving a real teacher's engine aimed at
    loopback would break every post after this run.
    """
    saved = _engine_request(config, "GET", "/api/settings").get("canvas_url", "")
    _engine_request(config, "PUT", "/api/settings", {"canvas_url": UNROUTABLE_CANVAS})
    try:
        yield
    finally:
        _engine_request(config, "PUT", "/api/settings", {"canvas_url": saved})


def run(config: CanvasE2EConfig) -> dict[str, Any]:
    if not config.apply:
        return _blocked("DRY_RUN", "pass --apply to perform guarded live Canvas writes", config)

    validate_course_name(config.course_name, config.course_prefix)
    from grader.canvas import missing_canvas_credentials

    missing = missing_canvas_credentials()
    if missing:
        return _blocked(
            "BLOCKED_CANVAS_CREDENTIALS",
            f"missing Canvas credentials: {', '.join(missing)}",
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
            "title": RUBRIC_TITLE,
            "rubric_text": (
                f"# {RUBRIC_TITLE}\n"
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
        # Two: one for the ordinary post path, one for the upload-failure
        # probe. A grade Canvas has already taken cannot be posted again, so
        # the probe cannot reuse the first submission.
        selected = _select_submissions(
            imported,
            config.target_canvas_user_id,
            2,
            with_work=_canvas_user_ids_with_work(config.course_id, config.assignment_id),
        )
        if not selected:
            return _blocked(
                "BLOCKED_CANVAS_DATA",
                "need two ungraded Canvas submissions with work in them — one for the "
                f"post path and one for the upload-failure probe: {imported}",
                config,
            )
        selected_submission_id, queue_submission_id = selected

        _engine_request(config, "POST", "/api/grade/batch", {
            "assignment_id": assignment_key,
            "model": config.model,
            "rubric_title": RUBRIC_TITLE,
            "submission_ids": selected,
        })
        drafts = _wait_for_drafts(config, assignment_key, selected)
        draft = drafts[selected_submission_id]
        grade_id = draft["id"]
        _teacher_override(config, draft)

        # Attached before the post, because /api/canvas/post is what uploads it.
        feedback_file = _write_feedback_file(config, grade_id)
        _engine_request(config, "POST", f"/api/grades/{grade_id}/attachments", {
            "path": str(feedback_file),
        })

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
        canvas_user_ids = _canvas_user_ids(imported)
        attachment = _read_attachment_back(
            config, canvas_user_ids[selected_submission_id], feedback_file.name
        )
        attachment_queue = _run_upload_failure_probe(
            config,
            drafts[queue_submission_id],
            canvas_user_ids[queue_submission_id],
            assignment_key,
        )

        result = {
            "status": "PASS",
            "applied": True,
            "course_id": config.course_id,
            "assignment_id": config.assignment_id,
            "assignment_key": assignment_key,
            "imported": imported.get("imported", 0),
            "selected_submission_id": selected_submission_id,
            "queue_submission_id": queue_submission_id,
            "grade_ids": [grade_id],
            "posted": posted.get("posted", 0),
            "progress_state": posted.get("progress_state", "unknown"),
            "read_back": read_back,
            "attachment": attachment,
            "attachment_queue": attachment_queue,
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
    parser.add_argument("--target-canvas-user-id", type=int, default=None)
    parser.add_argument(
        "--attachment-dir",
        type=Path,
        default=Path(os.environ.get("GRADER_DATA_DIR", "fixtures")),
        help="the engine's data directory — the only place it will send a file from",
    )
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
        target_canvas_user_id=args.target_canvas_user_id,
        attachment_dir=args.attachment_dir,
    )
    try:
        result = run(config)
    except Exception as exc:
        result = {"status": "FAIL", "reason": redact(str(exc)), "applied": False}

    text = json.dumps(stamp_provenance(result), indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    sys.exit(1 if result.get("status") == "FAIL" else 0)


if __name__ == "__main__":
    main()
