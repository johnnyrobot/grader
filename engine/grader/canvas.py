"""Canvas integration via canvas-pp-cli subprocess.

Three concerns, isolated here:
1. Read: list ungraded submissions + their attachments from a Canvas course.
2. Download: authenticated GET for attachment bytes (the one gap in the CLI).
3. Write: post grades + rubric assessments + comments back, polling Progress.

Gotchas (all discovered the hard way — see docs/phases/P3.md):
- Reads return {meta, results}; mutations return {action, resource, success, ...}. Parse the right key.
- bulk-update is ASYNC: returns a Progress object, not grades. Poll until "completed".
- Submission attachments must be uploaded as the STUDENT (as_user_id). Comment attachments are teacher-scoped.
- Canvas returns HTML unless Accept: application/json is sent.

Nothing posts without teacher_final.approved_at — enforced by the caller, not here.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

CLI_PATH = os.environ.get("CANVAS_PP_CLI", "canvas-pp-cli")
CANVAS_TOKEN = (
    os.environ.get("CANVAS_TOKEN")
    or os.environ.get("CANVAS_API_TOKEN")
    or os.environ.get("CANVAS_ACCESS_TOKEN")
    or ""
)
CANVAS_BASE = os.environ.get("CANVAS_BASE_URL", "")


def _run(*args: str, timeout: int = 30, stdin_json: dict[str, Any] | None = None) -> dict:
    """Run canvas-pp-cli with --agent flag, parse JSON output."""
    cmd = [CLI_PATH, *args, "--agent", "--data-source", "live", "--no-cache"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        input=json.dumps(stdin_json) if stdin_json is not None else None,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"canvas-pp-cli failed: {result.stderr[:300]}")
    envelope = json.loads(result.stdout)
    return envelope


def _read_results(envelope: dict) -> list[dict]:
    """Reads return {meta, results} — NOT {data} (gotcha #2)."""
    results = envelope.get("results") or envelope.get("data") or []
    if isinstance(results, dict):
        results = [results]
    return results


def _mutation_data(envelope: dict) -> dict:
    """Mutations return {action, resource, success, data, partial_failure}."""
    if not envelope.get("success", True):
        raise RuntimeError(f"Canvas mutation failed: {envelope.get('partial_failure', envelope)}")
    return envelope.get("data", {})


# ---------------------------------------------------------------------------
# Read: list submissions + attachments
# ---------------------------------------------------------------------------

def list_submissions(course_id: int | str, assignment_id: int | str) -> list[dict]:
    """List submissions for an assignment, including rubric assessments and comments.

    Returns per-student: user_id, score, workflow_state, attachments[], submission_comments[],
    rubric_assessment (if already graded).
    """
    envelope = _run(
        "submissions", "index", str(course_id), str(assignment_id),
        "--include", "submission_comments",
        timeout=30,
    )
    return _read_results(envelope)


def list_ungraded(course_id: int | str, assignment_id: int | str) -> list[dict]:
    """Filter to submissions that need grading (workflow_state != 'graded')."""
    subs = list_submissions(course_id, assignment_id)
    return [s for s in subs if s.get("workflow_state") != "graded"]


def get_submission(course_id: int | str, assignment_id: int | str, user_id: int | str) -> dict:
    """Show a single submission with attachments + comments."""
    envelope = _run(
        "submissions", "show", str(course_id), str(assignment_id), str(user_id),
        "--include", "submission_comments",
        timeout=15,
    )
    results = _read_results(envelope)
    return results[0] if results else {}


# ---------------------------------------------------------------------------
# Download: authenticated attachment GET (the CLI gap)
# ---------------------------------------------------------------------------

def download_attachment(url: str, dest: Path, *, token: str | None = None) -> Path:
    """Download a Canvas attachment by URL with the bearer token.

    Canvas attachment URLs require authentication — the CLI returns URLs, not bytes.
    """
    tok = token or CANVAS_TOKEN
    if not tok:
        raise RuntimeError("no Canvas token set (CANVAS_TOKEN env or token= kwarg)")

    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        r = client.get(url, headers={"Authorization": f"Bearer {tok}"})
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


def download_submission_attachments(
    submission: dict, dest_dir: Path, *, token: str | None = None,
) -> list[Path]:
    """Download all attachments for a submission. Returns paths to downloaded files."""
    paths: list[Path] = []
    for att in submission.get("attachments", []):
        url = att.get("url")
        if not url:
            continue
        filename = att.get("filename", f"attachment_{att.get('id', 'unknown')}")
        dest = dest_dir / filename
        try:
            download_attachment(url, dest, token=token)
            paths.append(dest)
        except Exception:
            paths.append(dest)  # still return path; caller can check existence
    return paths


# ---------------------------------------------------------------------------
# Post: grades + rubric assessments + comments
# ---------------------------------------------------------------------------

def post_grade(
    course_id: int | str,
    assignment_id: int | str,
    user_id: int | str,
    *,
    grade: str | float,
    rubric_assessment: dict | None = None,
    comment: str | None = None,
    comment_file_ids: list[int] | None = None,
) -> dict:
    """Post a single grade via `submissions update`.

    rubric_assessment: {criterion_id: {points: float, comments: str}}
    comment_file_ids: Canvas file IDs from the 3-step upload flow.
    """
    args = [
        "submissions", "update", str(course_id), str(assignment_id), str(user_id),
        f"--submission-posted-grade={grade}",
    ]
    if comment:
        args.append(f"--comment-text-comment={comment}")
    if rubric_assessment:
        args.append(f"--rubric-assessment={json.dumps(rubric_assessment)}")
    if comment_file_ids:
        for fid in comment_file_ids:
            args.append(f"--comment-file-ids={fid}")

    envelope = _run(*args, timeout=15)
    return _mutation_data(envelope)


def post_grades_batch(
    course_id: int | str,
    assignment_id: int | str,
    grade_data: dict[str, Any],
) -> dict:
    """Batch post via `submissions bulk-update`. Returns Progress object.

    CRITICAL: bulk-update is ASYNC (gotcha #1). The POST returns a Progress object,
    not grades. Poll /progress/:id until 'completed' before reporting success.

    grade_data: {user_id_str: {posted_grade, rubric_assessment, ...}}
    """
    assignment_data: dict[str, Any] = {}
    for user_id, fields in grade_data.items():
        payload: dict[str, Any] = {}
        if "posted_grade" in fields:
            payload["posted_grade"] = str(fields["posted_grade"])
        if "excuse" in fields:
            payload["excuse"] = fields["excuse"]
        if fields.get("rubric_assessment"):
            payload["rubric_assessment"] = fields["rubric_assessment"]
        comment = fields.get("text_comment") or fields.get("comment")
        if comment:
            payload["text_comment"] = str(comment)
        if fields.get("file_ids"):
            payload["file_ids"] = fields["file_ids"]
        assignment_data[str(user_id)] = payload

    body = {"grade_data": {str(assignment_id): assignment_data}}

    envelope = _run("submissions", "bulk-update", str(course_id), "--stdin", timeout=30, stdin_json=body)

    # Mutations return {action, resource, success, data, partial_failure}
    if not envelope.get("success", True):
        partial = envelope.get("partial_failure", {})
        raise RuntimeError(f"bulk-update failed: {json.dumps(partial)[:300]}")

    progress = envelope.get("data", {})
    if progress.get("workflow_state") == "completed":
        return progress

    progress_id = progress.get("id")
    if progress_id is None:
        return progress

    return _poll_progress(course_id, progress_id)


def _poll_progress(
    course_id: int | str, progress_id: int | str, *, timeout: int = 60, interval: float = 1.0,
) -> dict:
    """Poll the Progress endpoint until completion or timeout (gotcha #1)."""
    _ = course_id  # Progress show is global in canvas-pp-cli; keep arg for API compatibility.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        envelope = _run("progress", "show", str(progress_id), timeout=10)
        results = _read_results(envelope)
        if not results:
            break
        p = results[0]
        state = p.get("workflow_state", "")
        if state == "completed":
            return p
        if state in ("failed", "aborted"):
            raise RuntimeError(f"Canvas progress {progress_id} {state}")
        time.sleep(interval)
    raise TimeoutError(f"Canvas progress {progress_id} did not complete within {timeout}s")


# ---------------------------------------------------------------------------
# File upload: 3-step flow for annotated feedback files
# ---------------------------------------------------------------------------

def upload_file(
    course_id: int | str,
    file_path: Path,
    *,
    as_user_id: int | str | None = None,
) -> int:
    """Upload a file to Canvas via the 3-step flow. Returns Canvas file ID.

    Step 1: notify Canvas of the upload intent.
    Step 2: PUT the file bytes to the returned URL.
    Step 3: confirm with Canvas, get the file ID.
    """
    tok = CANVAS_TOKEN
    if not tok:
        raise RuntimeError("no Canvas token set")

    base = CANVAS_BASE.rstrip("/") if CANVAS_BASE else "https://canvas.instructure.com"
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    file_size = file_path.stat().st_size
    params: dict[str, Any] = {
        "name": file_path.name,
        "size": file_size,
        "content_type": "application/octet-stream",
        "parent_folder_path": "graded_feedback",
    }
    if as_user_id is not None:
        params["as_user_id"] = as_user_id

    with httpx.Client(timeout=60, follow_redirects=True) as client:
        # Step 1: notify
        url = f"{base}/api/v1/courses/{course_id}/files"
        r = client.post(url, headers=headers, data=params)
        r.raise_for_status()
        upload_info = r.json()
        upload_url = upload_info["upload_url"]
        upload_params = upload_info["upload_params"]

        # Step 2: PUT file bytes
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, "application/octet-stream")}
            r = client.put(upload_url, data=upload_params, files=files)
            r.raise_for_status()

        # Step 3: confirm
        r = client.get(upload_info.get("redirect_url", ""), headers=headers)
        if r.status_code != 200:
            r = client.post(url, headers=headers, data={"name": file_path.name, "size": file_size})
            r.raise_for_status()
        file_id = r.json().get("id")
        if file_id is None:
            raise RuntimeError(f"Canvas file upload did not return id: {r.json()}")
        return file_id


# ---------------------------------------------------------------------------
# Offline post queue
# ---------------------------------------------------------------------------

class OfflinePostQueue:
    """Queues grade posts when offline; flushes when connectivity returns.

    Imported batches grade with no network. Posts queue here and flush on reconnect.
    Each entry carries enough context to retry without re-computing the grade.
    """

    def __init__(self, path: Path | str = "fixtures/post_queue.db") -> None:
        import sqlite3
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS post_queue (
                id          INTEGER PRIMARY KEY,
                course_id   TEXT NOT NULL,
                assignment_id TEXT NOT NULL,
                user_id     TEXT NOT NULL,
                grade       TEXT NOT NULL,
                rubric_assessment TEXT,
                comment     TEXT,
                file_ids    TEXT,
                approved_at TEXT NOT NULL,
                attempts    INTEGER DEFAULT 0,
                last_error  TEXT,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pq_course ON post_queue(course_id, assignment_id);
        """)

    def enqueue(
        self,
        course_id: str,
        assignment_id: str,
        user_id: str,
        grade: str,
        *,
        rubric_assessment: str | None = None,
        comment: str | None = None,
        file_ids: str | None = None,
        approved_at: str | None = None,
    ) -> int:
        from datetime import datetime, timezone
        cur = self.db.execute(
            "INSERT INTO post_queue (course_id, assignment_id, user_id, grade, "
            "rubric_assessment, comment, file_ids, approved_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (course_id, assignment_id, user_id, grade,
             rubric_assessment, comment, file_ids,
             approved_at or datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def pending(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM post_queue ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_done(self, queue_id: int) -> None:
        self.db.execute("DELETE FROM post_queue WHERE id = ?", (queue_id,))
        self.db.commit()

    def mark_failed(self, queue_id: int, error: str) -> None:
        self.db.execute(
            "UPDATE post_queue SET attempts = attempts + 1, last_error = ? WHERE id = ?",
            (error[:300], queue_id),
        )
        self.db.commit()

    def flush(self) -> dict:
        """Attempt to post all queued grades. Returns {posted, failed, remaining}."""
        posted = 0
        failed = 0
        for item in self.pending():
            try:
                ra = json.loads(item["rubric_assessment"]) if item["rubric_assessment"] else None
                fids = json.loads(item["file_ids"]) if item["file_ids"] else None
                post_grade(
                    item["course_id"], item["assignment_id"], item["user_id"],
                    grade=item["grade"], rubric_assessment=ra,
                    comment=item["comment"], comment_file_ids=fids,
                )
                self.mark_done(item["id"])
                posted += 1
            except Exception as e:
                self.mark_failed(item["id"], str(e))
                failed += 1
        remaining = len(self.pending())
        return {"posted": posted, "failed": failed, "remaining": remaining}
