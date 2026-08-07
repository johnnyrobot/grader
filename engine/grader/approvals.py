"""The approval invariant: nothing reaches Canvas without a teacher approving it.

This is what Grader sells. Every AI-produced grade is a draft until the teacher
says otherwise, and the moment they do is recorded in exactly one place —
`grade_records.approved_at`, written only by `approve()` and `override()` here.

`postable()` is the only supported way to ask whether a grade may be sent. It
hands back values rather than rows, so a caller has nothing to read
`approved_at` from and no second way to answer the question.

Deliberately narrow. This module does not reconstruct a GradeDraft from
grade_records columns, resolve a Rubric's total_points, or talk to Canvas. The
first two need an Assignment-to-Rubric relation the schema does not yet have;
the third is grader.canvas's job. Folding them in would turn an invariant into
a grade_records repository, which is a different and larger change.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from grader.exemplars import ExemplarStore
from grader.overrides import Override, OverrideStore, apply_override
from grader.schema import GradeDraft

# A fresh connection per call, matching engine.api._db. Passed as a callable
# rather than a connection so that redirecting the database after import — which
# every test does — still reaches this module.
DbFactory = Callable[[], sqlite3.Connection]
Audit = Callable[[str, str, str, dict], None]

DEFAULT_COMMENT = "Posted by Grader after teacher approval."


class ApprovalError(Exception):
    """Base for the errors this module raises, so a caller can catch either."""


class GradeNotFound(ApprovalError):
    def __init__(self, grade_id: str) -> None:
        self.grade_id = grade_id
        super().__init__(f"grade not found: {grade_id}")


class AlreadyApproved(ApprovalError):
    """approve() only.

    Re-overriding an approved grade is legitimate — a teacher revising their own
    correction — and matches the behaviour that existed before this module.
    """

    def __init__(self, grade_id: str) -> None:
        self.grade_id = grade_id
        super().__init__(f"already approved: {grade_id}")


@dataclass(frozen=True)
class Approval:
    """What approving or overriding produced.

    One type for both paths, so callers and tests observe the same outcome
    whichever ran. `override` is set only when a correction was recorded.
    """

    grade_id: str
    approved_at: str
    override: Override | None = None


class PostSkipReason(str, Enum):
    NOT_FOUND = "not_found"
    NOT_APPROVED = "not_approved"
    MISSING_CANVAS_USER_ID = "missing_canvas_user_id"
    ALREADY_POSTED = "already_posted"


@dataclass(frozen=True)
class PostSkip:
    grade_id: str
    reason: PostSkipReason
    # The teacher-facing wording, unchanged from what canvas_post has always
    # surfaced. `reason` is for code; `detail` is for the person reading it.
    detail: str


@dataclass(frozen=True)
class PostableGrade:
    grade_id: str
    canvas_user_id: str
    posted_grade: float
    text_comment: str


@dataclass(frozen=True)
class PostableBatch:
    ready: list[PostableGrade]
    skipped: list[PostSkip]

    def grade_data(self) -> dict[str, dict[str, object]]:
        """The payload shape `grader.canvas.post_grades_batch` expects."""
        return {
            g.canvas_user_id: {"posted_grade": g.posted_grade, "text_comment": g.text_comment}
            for g in self.ready
        }

    @property
    def grade_ids(self) -> list[str]:
        return [g.grade_id for g in self.ready]

    @property
    def skip_details(self) -> list[str]:
        return [s.detail for s in self.skipped]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalStore:
    """Sole owner of grade_records.approved_at and posted_at.

    Invariants:
      - After approve() or override() returns, approved_at is set on that grade,
        and this call set it. No other code path writes the column.
      - override() records the Override and derives its Exemplar *before*
        stamping approved_at, never after and never conditionally. If that
        derivation raises, approved_at is untouched.
      - approve() holds no reference to OverrideStore or ExemplarStore, so the
        plain-approval path cannot produce an Exemplar — by construction rather
        than by care.
      - postable() never raises. An id it cannot resolve becomes a PostSkip, so
        one bad grade never aborts a batch.
      - Every write emits exactly one audit event in the same call.

    Ordering note: an override touches three separate databases and there is no
    transaction spanning them — as was true before this module. What is new is
    that approved_at is unconditionally last, so a crash can leave a recorded
    correction with no approval (safe: postable() excludes it) and never an
    approval with no correction.
    """

    def __init__(self, db: DbFactory, audit: Audit) -> None:
        self._db = db
        self._audit = audit

    def approve(self, grade_id: str, *, actor: str = "teacher") -> Approval:
        db = self._db()
        try:
            row = db.execute(
                "SELECT approved_at FROM grade_records WHERE id = ?", (grade_id,)
            ).fetchone()
            if row is None:
                raise GradeNotFound(grade_id)
            if row["approved_at"]:
                raise AlreadyApproved(grade_id)
            approved_at = self._stamp_approved(db, grade_id)
        finally:
            db.close()

        self._audit(grade_id, actor, "approve", {})
        return Approval(grade_id=grade_id, approved_at=approved_at)

    def override(
        self,
        grade_id: str,
        *,
        override_store: OverrideStore,
        exemplar_store: ExemplarStore,
        assignment_id: str,
        submission_text: str,
        ai_draft: GradeDraft,
        teacher_scores: dict[str, float],
        teacher_feedback: str,
        max_total: float,
        rubric_version: str = "1",
        actor: str = "teacher",
    ) -> Approval:
        """Record a teacher's correction, derive its Exemplar, then approve.

        `exemplar_store` must already be bound to the caller's embedding model
        via `ExemplarStore.for_model(...)` — never the shared singleton, which
        in-flight grading threads are reading.
        """
        db = self._db()
        try:
            exists = db.execute(
                "SELECT 1 FROM grade_records WHERE id = ?", (grade_id,)
            ).fetchone()
        finally:
            db.close()
        if not exists:
            raise GradeNotFound(grade_id)

        ov = apply_override(
            override_store,
            exemplar_store,
            assignment_id=assignment_id,
            submission_text=submission_text,
            ai_draft=ai_draft,
            teacher_scores=teacher_scores,
            teacher_feedback=teacher_feedback,
            max_total=max_total,
            rubric_version=rubric_version,
        )

        db = self._db()
        try:
            approved_at = self._stamp_approved(
                db,
                grade_id,
                teacher_scores=json.dumps(teacher_scores),
                teacher_feedback=teacher_feedback,
            )
        finally:
            db.close()

        self._audit(
            grade_id, actor, "override",
            {"ai_total": ov.ai_total, "teacher_total": ov.teacher_total,
             "delta": ov.criterion_delta},
        )
        return Approval(grade_id=grade_id, approved_at=approved_at, override=ov)

    def postable(self, grade_ids: Sequence[str]) -> PostableBatch:
        """Which of these grades may be sent to Canvas, and why the rest may not."""
        ready: list[PostableGrade] = []
        skipped: list[PostSkip] = []
        db = self._db()
        try:
            for gid in grade_ids:
                row = db.execute(
                    "SELECT gr.*, s.student_ref AS canvas_user_id FROM grade_records gr "
                    "LEFT JOIN submissions s ON s.id = gr.submission_id WHERE gr.id = ?",
                    (gid,),
                ).fetchone()
                skip = _why_not_postable(gid, row)
                if skip is not None:
                    skipped.append(skip)
                    continue
                teacher = json.loads(row["teacher_scores_json"] or "{}")
                ai = json.loads(row["ai_scores_json"] or "{}")
                scores = teacher or ai
                ready.append(
                    PostableGrade(
                        grade_id=gid,
                        canvas_user_id=str(row["canvas_user_id"]),
                        posted_grade=sum(scores.values()) if scores else 0.0,
                        text_comment=row["teacher_feedback"] or row["ai_feedback"]
                        or DEFAULT_COMMENT,
                    )
                )
        finally:
            db.close()
        return PostableBatch(ready=ready, skipped=skipped)

    def mark_posted(self, grade_ids: Sequence[str]) -> None:
        """Record that Canvas accepted these grades.

        Call only after a successful post: Canvas appends submission comments
        rather than replacing them, so a grade that is marked before the send
        succeeds can never be retried without the student seeing it twice.
        """
        if not grade_ids:
            return
        posted_at = _now()
        db = self._db()
        try:
            db.executemany(
                "UPDATE grade_records SET posted_at = ? WHERE id = ?",
                [(posted_at, gid) for gid in grade_ids],
            )
            db.commit()
        finally:
            db.close()

    # -- internals ------------------------------------------------------------

    def _stamp_approved(
        self,
        db: sqlite3.Connection,
        grade_id: str,
        *,
        teacher_scores: str | None = None,
        teacher_feedback: str | None = None,
    ) -> str:
        """The one and only writer of grade_records.approved_at."""
        approved_at = _now()
        if teacher_scores is None:
            db.execute(
                "UPDATE grade_records SET approved_at = ? WHERE id = ?",
                (approved_at, grade_id),
            )
        else:
            db.execute(
                "UPDATE grade_records SET teacher_scores_json = ?, teacher_feedback = ?, "
                "approved_at = ? WHERE id = ?",
                (teacher_scores, teacher_feedback, approved_at, grade_id),
            )
        db.commit()
        return approved_at


def _why_not_postable(grade_id: str, row: sqlite3.Row | None) -> PostSkip | None:
    """The postability predicate, in one place.

    Order matters only for the message a teacher reads: a grade can fail more
    than one of these, and they see the first.
    """
    if row is None:
        return PostSkip(grade_id, PostSkipReason.NOT_FOUND, f"{grade_id}: not found")
    if not row["approved_at"]:
        return PostSkip(
            grade_id, PostSkipReason.NOT_APPROVED,
            f"{grade_id}: not approved — WILL NOT POST",
        )
    if not row["canvas_user_id"]:
        return PostSkip(
            grade_id, PostSkipReason.MISSING_CANVAS_USER_ID,
            f"{grade_id}: missing Canvas user id — WILL NOT POST",
        )
    if row["posted_at"]:
        return PostSkip(
            grade_id, PostSkipReason.ALREADY_POSTED,
            f"{grade_id}: already posted — WILL NOT POST AGAIN",
        )
    return None
