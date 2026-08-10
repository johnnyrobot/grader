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
import sqlite3
import subprocess
import tomllib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from grader.db import Database

CLI_PATH = os.environ.get("CANVAS_PP_CLI", "canvas-pp-cli")
CREDS_PATHS = [
    Path.home() / ".local/share/canvas-pp-cli/credentials.toml",
    Path.home() / ".config/canvas-pp-cli/credentials.toml",
]
CONFIG_PATHS = [
    Path.home() / ".config/canvas-pp-cli/config.toml",
    Path.home() / ".local/share/canvas-pp-cli/config.toml",
]


def _scan_toml(paths: list[Path], keys: tuple[str, ...]) -> str | None:
    """Return the first matching TOML value at top level or one nested table."""
    for path in paths:
        if not path.exists():
            continue
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError:
            continue
        scopes = [data, *(v for v in data.values() if isinstance(v, dict))]
        for scope in scopes:
            for key in keys:
                value = scope.get(key)
                if value:
                    return str(value)
    return None


def resolve_canvas_token() -> str:
    """Resolve a Canvas API token from env or canvas-pp-cli's credentials file."""
    return (
        os.environ.get("CANVAS_TOKEN")
        or os.environ.get("CANVAS_API_TOKEN")
        or os.environ.get("CANVAS_ACCESS_TOKEN")
        or _scan_toml(CREDS_PATHS, ("access_token", "token", "api_token"))
        or ""
    )


def resolve_canvas_base_url() -> str:
    """Resolve the Canvas base URL from env or canvas-pp-cli's config files."""
    return (
        os.environ.get("CANVAS_BASE_URL")
        or _scan_toml(CONFIG_PATHS + CREDS_PATHS, ("base_url", "url", "host"))
        or ""
    )


def missing_canvas_credentials() -> list[str]:
    """Secret-safe list of missing Canvas credential fields."""
    missing: list[str] = []
    if not resolve_canvas_base_url():
        missing.append("CANVAS_BASE_URL or canvas-pp-cli base_url")
    if not resolve_canvas_token():
        missing.append("CANVAS_TOKEN/CANVAS_API_TOKEN or canvas-pp-cli access_token")
    return missing


def _run(
    *args: str,
    timeout: int = 30,
    stdin_json: dict[str, Any] | None = None,
    base_url: str | None = None,
    token: str | None = None,
) -> dict:
    """Run canvas-pp-cli with --agent flag, parse JSON output."""
    cmd = [CLI_PATH, *args, "--agent", "--data-source", "live", "--no-cache"]
    env = os.environ.copy()
    if base_url:
        env["CANVAS_BASE_URL"] = base_url.rstrip("/")
    if token:
        env["CANVAS_API_TOKEN"] = token
        env["CANVAS_ACCESS_TOKEN"] = token
    result = subprocess.run(
        cmd,
        capture_output=True,
        input=json.dumps(stdin_json) if stdin_json is not None else None,
        text=True,
        timeout=timeout,
        env=env,
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

def list_submissions(
    course_id: int | str,
    assignment_id: int | str,
    *,
    base_url: str | None = None,
    token: str | None = None,
) -> list[dict]:
    """List submissions for an assignment, including rubric assessments and comments.

    Returns per-student: user_id, score, workflow_state, attachments[], submission_comments[],
    rubric_assessment (if already graded).

    Paginated straight against Canvas rather than through `canvas-pp-cli`. The
    CLI has no page-size flag and no cursor, and its read envelope carries no
    pagination metadata, so it returns Canvas's default first page — **ten
    submissions** — and nothing says the rest exist. A class of thirty imported
    ten and reported a complete import. This is the same CLI gap as
    `download_attachment`, found the same way: by counting what came back.
    """
    tok = token or resolve_canvas_token()
    if not tok:
        raise RuntimeError("no Canvas token set")
    base = (base_url or resolve_canvas_base_url() or "https://canvas.instructure.com").rstrip("/")

    url: str | None = (
        f"{base}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions"
    )
    params: dict[str, Any] | None = {
        "include[]": "submission_comments",
        "per_page": 100,
    }
    submissions: list[dict] = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        while url:
            response = client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
            )
            response.raise_for_status()
            page = response.json()
            submissions.extend(page if isinstance(page, list) else [page])
            # The next link already carries the cursor; re-sending params would
            # reset it and fetch page two forever.
            next_link = response.links.get("next") or {}
            url = next_link.get("url")
            params = None
    return submissions


def list_ungraded(
    course_id: int | str,
    assignment_id: int | str,
    *,
    base_url: str | None = None,
    token: str | None = None,
) -> list[dict]:
    """Filter to the submissions that still need a grade."""
    subs = list_submissions(course_id, assignment_id, base_url=base_url, token=token)
    return [s for s in subs if needs_grading(s)]


def needs_grading(submission: dict) -> bool:
    """Is Canvas still waiting for a grade on this submission?

    `workflow_state` alone is not the answer. Canvas leaves it at `graded` when
    a teacher *removes* a score, and removing a score is how a teacher says "do
    this one over" — so the one submission they asked Grader to re-do was the
    one it could not see. An absent score is the truer signal.

    Excused is the case that breaks the simple version of that rule: it reads
    identically — `graded`, no score — and grading an excused student is worse
    than missing one, so it is checked first.
    """
    if submission.get("excused"):
        return False
    if submission.get("workflow_state") != "graded":
        return True
    return submission.get("score") is None


def get_submission(
    course_id: int | str,
    assignment_id: int | str,
    user_id: int | str,
    *,
    base_url: str | None = None,
    token: str | None = None,
) -> dict:
    """Show a single submission with attachments + comments."""
    envelope = _run(
        "submissions", "show", str(course_id), str(assignment_id), str(user_id),
        "--include", "submission_comments",
        timeout=15,
        base_url=base_url,
        token=token,
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
    tok = token or resolve_canvas_token()
    if not tok:
        raise RuntimeError(
            "no Canvas token set (CANVAS_TOKEN env, token= kwarg, or canvas-pp-cli credentials)"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        r = client.get(url, headers={"Authorization": f"Bearer {tok}"})
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


@dataclass
class AttachmentDownload:
    """Outcome of one attachment download.

    `ok` is reported explicitly because the destination existing proves nothing — a
    leftover file from an earlier import satisfies `path.exists()` just as well as a
    fresh download, and treating that as success feeds the wrong bytes to the grader.
    """

    path: Path
    ok: bool
    error: str | None = None


def download_submission_attachments(
    submission: dict, dest_dir: Path, *, token: str | None = None,
) -> list[AttachmentDownload]:
    """Download all attachments for a submission.

    Every student in an assignment shares one `dest_dir`, and generic filenames like
    "essay.pdf" are the norm, so destinations are namespaced by the Canvas attachment
    id. Without that, one student's original silently overwrites another's — which also
    breaks the rule that original student files are immutable.
    """
    results: list[AttachmentDownload] = []
    for index, att in enumerate(submission.get("attachments", [])):
        url = att.get("url")
        if not url:
            continue
        att_id = att.get("id")
        prefix = str(att_id) if att_id not in (None, "") else f"idx{index}"
        raw_filename = str(att.get("filename") or f"attachment_{prefix}")
        safe_name = Path(raw_filename).name or f"attachment_{prefix}"
        dest = dest_dir / f"{prefix}_{safe_name}"
        try:
            download_attachment(url, dest, token=token)
            results.append(AttachmentDownload(path=dest, ok=True))
        except Exception as e:
            results.append(AttachmentDownload(path=dest, ok=False, error=str(e)[:200]))
    return results


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
    base_url: str | None = None,
    token: str | None = None,
) -> dict:
    """Post a single grade via `submissions update`. The queue's delivery path.

    rubric_assessment: {criterion_id: {points: float, comments: str}}
    comment_file_ids: Canvas file IDs from the 3-step upload flow.

    The body goes over stdin as nested JSON, not as `--submission-posted-grade`
    and friends. Those flags exist, are spelled as the CLI's help describes,
    and produce a body of flat keys like `"submission[posted_grade]"` — but the
    brackets are *form*-encoding. As JSON keys they are part of the key name,
    so Canvas finds no `submission` to update, answers 200, and echoes back the
    submission it did not change. Nothing about the call looks wrong; the score
    simply stays null. Only reading it back off Canvas shows it, which is why
    this was written the wrong way for as long as it was.
    """
    body: dict[str, Any] = {"submission": {"posted_grade": str(grade)}}
    if rubric_assessment:
        body["rubric_assessment"] = rubric_assessment
    comment_body: dict[str, Any] = {}
    if comment:
        comment_body["text_comment"] = str(comment)
    if comment_file_ids:
        comment_body["file_ids"] = [int(fid) for fid in comment_file_ids]
    if comment_body:
        body["comment"] = comment_body

    envelope = _run(
        "submissions", "update", str(course_id), str(assignment_id), str(user_id),
        "--stdin",
        timeout=15,
        stdin_json=body,
        base_url=base_url,
        token=token,
    )
    return _mutation_data(envelope)


def post_grades_batch(
    course_id: int | str,
    assignment_id: int | str,
    grade_data: dict[str, Any],
    *,
    base_url: str | None = None,
    token: str | None = None,
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

    envelope = _run(
        "submissions",
        "bulk-update",
        str(course_id),
        "--stdin",
        timeout=30,
        stdin_json=body,
        base_url=base_url,
        token=token,
    )

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

    return _poll_progress(
        course_id,
        progress_id,
        base_url=base_url,
        token=token,
    )


def _poll_progress(
    course_id: int | str,
    progress_id: int | str,
    *,
    timeout: int = 60,
    interval: float = 1.0,
    base_url: str | None = None,
    token: str | None = None,
) -> dict:
    """Poll the Progress endpoint until completion or timeout (gotcha #1)."""
    _ = course_id  # Progress show is global in canvas-pp-cli; keep arg for API compatibility.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        envelope = _run(
            "progress",
            "show",
            str(progress_id),
            timeout=10,
            base_url=base_url,
            token=token,
        )
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

def upload_comment_attachment(
    course_id: int | str,
    assignment_id: int | str,
    user_id: int | str,
    file_path: Path,
    *,
    base_url: str | None = None,
    token: str | None = None,
) -> int:
    """Upload one file as a submission-comment attachment. Returns its Canvas file id.

    Targets the submission's own comment-files endpoint:

        POST /courses/:c/assignments/:a/submissions/:u/comments/files

    **Not** `/courses/:c/files`. That is course files — shared course content — and
    an annotated submission uploaded there is one student's work placed where
    other students can reach it. This function carries a student's graded work,
    so the destination is the part of Canvas scoped to that student's submission.

    Canvas's 3-step flow:
      1. Tell Canvas the name and size; it answers with a one-shot upload URL.
      2. POST the bytes there. Canvas answers 201 with the file, or 3xx to a
         location that returns it.
      3. Read the id out of whichever of those came back.
    """
    tok = token or resolve_canvas_token()
    if not tok:
        raise RuntimeError("no Canvas token set")

    canvas_base = base_url or resolve_canvas_base_url()
    base = canvas_base.rstrip("/") if canvas_base else "https://canvas.instructure.com"
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    params: dict[str, Any] = {
        "name": file_path.name,
        "size": file_path.stat().st_size,
        "content_type": "application/octet-stream",
    }

    # Step 2 must not carry the Authorization header: the upload URL is
    # pre-signed, and some Canvas file backends reject a request that presents
    # both. follow_redirects stays off so step 3 is explicit about what it read.
    with httpx.Client(timeout=120, follow_redirects=False) as client:
        url = (
            f"{base}/api/v1/courses/{course_id}/assignments/{assignment_id}"
            f"/submissions/{user_id}/comments/files"
        )
        r = client.post(url, headers=headers, data=params)
        r.raise_for_status()
        upload_info = r.json()

        with open(file_path, "rb") as handle:
            r = client.post(
                upload_info["upload_url"],
                data=upload_info.get("upload_params") or {},
                files={"file": (file_path.name, handle, "application/octet-stream")},
            )
        if r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get("location")
            if not location:
                raise RuntimeError("Canvas redirected the upload without a location")
            r = client.get(location, headers=headers)
        r.raise_for_status()

        file_id = r.json().get("id")
        if file_id is None:
            raise RuntimeError(f"Canvas file upload did not return id: {r.text[:200]}")
        return int(file_id)


# ---------------------------------------------------------------------------
# Offline post queue
# ---------------------------------------------------------------------------

# How many times a queued post may be retried before the queue stops trying.
# Five spans a long outage without letting an entry Canvas will never accept
# re-post on every flush forever.
MAX_POST_ATTEMPTS = 5

# What a caller hands `flush` so a file an outage stopped can still be uploaded.
# Returns the Canvas file id, or None when the file is no longer one this machine
# may send — deleted, moved, or pointed out of the attachment root since it was
# queued. Raising means the upload itself failed and the file stays pending.
Uploader = Callable[[str, str, str, Path], "int | None"]


def _add_pending_files(conn: sqlite3.Connection) -> None:
    """A queue written before attachments could outlive an outage."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(post_queue)")}
    if "pending_files" not in columns:
        conn.execute("ALTER TABLE post_queue ADD COLUMN pending_files TEXT")


class OfflinePostQueue:
    """Queues grade posts when offline; flushes when connectivity returns.

    Imported batches grade with no network. Posts queue here and flush on reconnect.
    Each entry carries enough context to retry without re-computing the grade.

    `file_ids` and `pending_files` are the two halves of one attachment: an id
    is a file Canvas already holds, a path is one it does not have yet. An
    outage between the two used to lose the file — the grade queued, the path
    did not, and the flush delivered feedback the teacher had attached with the
    attachment silently missing.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
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
                pending_files TEXT,
                approved_at TEXT NOT NULL,
                attempts    INTEGER DEFAULT 0,
                last_error  TEXT,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pq_course ON post_queue(course_id, assignment_id);
        """)
        self.db.migrate("post_queue", [_add_pending_files])

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
        pending_files: str | None = None,
        approved_at: str | None = None,
    ) -> int:
        from datetime import datetime, timezone
        cur = self.db.execute(
            "INSERT INTO post_queue (course_id, assignment_id, user_id, grade, "
            "rubric_assessment, comment, file_ids, pending_files, approved_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (course_id, assignment_id, user_id, grade,
             rubric_assessment, comment, file_ids, pending_files,
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

    def _upload_pending(
        self,
        item: dict,
        file_ids: list[int],
        paths: list[str],
        upload: Uploader | None,
    ) -> tuple[list[int], list[str], str]:
        """Send what the outage left behind. Returns (ids, still pending, error).

        Each success is written back before the post is attempted, so a post
        that fails again does not re-upload a file Canvas already has — the
        same reason `canvas_post` carries its uploaded ids into the queue.
        """
        if upload is None:
            raise RuntimeError("no uploader was given for a queued attachment")
        uploaded = list(file_ids)
        still: list[str] = []
        error = ""
        for path in paths:
            try:
                file_id = upload(
                    item["course_id"], item["assignment_id"], item["user_id"], Path(path)
                )
            except Exception as exc:
                still.append(path)
                error = str(exc)
                continue
            # None is the uploader saying this file is no longer one this
            # machine may send. It is dropped rather than retried forever.
            if file_id is not None:
                uploaded.append(int(file_id))
        self.db.execute(
            "UPDATE post_queue SET file_ids = ?, pending_files = ? WHERE id = ?",
            (json.dumps(uploaded) if uploaded else None,
             json.dumps(still) if still else None,
             item["id"]),
        )
        self.db.commit()
        return uploaded, still, error

    def _give_up_on(
        self, queue_id: int, file_ids: list[int], paths: list[str], error: str
    ) -> None:
        """Stop waiting for these files and let the grade go.

        The row is cleared of them and the reason is left on the entry, so the
        flush that deletes it still reports why the feedback never went — the
        teacher attached it and is entitled to know it did not arrive.
        """
        self.db.execute(
            "UPDATE post_queue SET file_ids = ?, pending_files = NULL, last_error = ? "
            "WHERE id = ?",
            (json.dumps(file_ids) if file_ids else None,
             f"gave up on {len(paths)} attachment(s): {error}"[:300],
             queue_id),
        )
        self.db.commit()

    def flush(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        upload: Uploader | None = None,
    ) -> dict:
        """Attempt to post all queued grades.

        Returns {posted, failed, abandoned, remaining}.

        `upload` is how an entry whose attachment never reached Canvas finishes
        the job. Without one, such an entry fails rather than posting a grade
        the teacher attached feedback to — a post missing its attachment reads
        as complete and is not retried.

        `canvas-pp-cli` reports every failure as one RuntimeError, so the queue
        cannot tell a network outage from a grade Canvas will never accept — a
        student who dropped the course, say. Retrying is therefore right by
        default and wrong forever: without a bound, one such entry re-posts on
        every flush for the life of the install. Past MAX_POST_ATTEMPTS an entry
        stops being retried but stays in the queue, so it surfaces to the
        teacher through /api/canvas/post-queue instead of failing silently.
        """
        posted = 0
        failed = 0
        abandoned = 0
        attachments_abandoned = 0
        for item in self.pending():
            if item["attempts"] >= MAX_POST_ATTEMPTS:
                abandoned += 1
                continue
            try:
                ra = json.loads(item["rubric_assessment"]) if item["rubric_assessment"] else None
                fids = json.loads(item["file_ids"]) if item["file_ids"] else []
                paths = json.loads(item["pending_files"]) if item["pending_files"] else []
                if paths:
                    fids, still, error = self._upload_pending(item, fids, paths, upload)
                    if still and item["attempts"] + 1 < MAX_POST_ATTEMPTS:
                        raise RuntimeError(f"attachment upload failed: {error}")
                    if still:
                        # Out of attempts. Holding the grade back any longer is
                        # worse than the silent drop this replaced: the file is
                        # gone either way, and now so is the student's grade.
                        self._give_up_on(item["id"], fids, still, error)
                        attachments_abandoned += 1
                post_grade(
                    item["course_id"], item["assignment_id"], item["user_id"],
                    grade=item["grade"], rubric_assessment=ra,
                    comment=item["comment"], comment_file_ids=fids or None,
                    base_url=base_url, token=token,
                )
                self.mark_done(item["id"])
                posted += 1
            except Exception as e:
                self.mark_failed(item["id"], str(e))
                failed += 1
        remaining = len(self.pending())
        return {
            "posted": posted,
            "failed": failed,
            "abandoned": abandoned,
            "attachments_abandoned": attachments_abandoned,
            "remaining": remaining,
        }
