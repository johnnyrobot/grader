"""One batch of submissions, graded.

This is the pipeline: fetch, retrieve anchors, grade, triage, persist, report.
It ran inside engine/api.py behind a background thread until now, which meant
the only way to reach it was over HTTP with Ollama running — so the tests that
covered grading covered the thread being constructed, never the loop inside it.

Nothing here knows about threads, HTTP, or websockets. `run()` blocks; making
that non-blocking is the transport's problem, and progress is a callback rather
than a socket.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from grader.calibration import can_draft
from grader.exemplars import render_anchors
from grader.ports import Judge
from grader.schema import Rubric
from grader.triage import triage

ANCHORS_PER_GRADE = 6


@dataclass(frozen=True)
class SubmissionText:
    id: str
    assignment_id: str
    text: str
    # True when this text came out of OCR rather than off a keyboard. The grade
    # cannot see a misread word, so triage refuses to auto-clear it.
    scanned: bool = False


OutcomeState = Literal["drafted", "error", "not_found"]


@dataclass(frozen=True)
class GradeOutcome:
    """What happened to one submission.

    `record` is the payload the desktop app renders and the audit log stores.
    It stays a plain dict because it is a wire format shared with the frontend,
    not an internal type this module is free to reshape.
    """

    submission_id: str
    record_id: str
    state: OutcomeState
    record: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.state == "drafted"


class ExemplarRetriever(Protocol):
    """Retrieval, already bound to one embedding model."""

    def count(self, assignment_id: str) -> int: ...

    def retrieve(
        self, assignment_id: str, query_text: str, *, k: int = 6, balance: bool = True
    ) -> list[Any]: ...


class GradeStore(Protocol):
    """Where a batch reads submissions and writes what it decided."""

    def fetch_submission(self, submission_id: str) -> SubmissionText | None: ...

    def save(self, outcome: GradeOutcome, *, rubric_title: str) -> None: ...

    def audit(self, entity: str, actor: str, event: str, payload: dict) -> None: ...


OnProgress = Callable[[int, int, GradeOutcome], None]


@dataclass(frozen=True)
class _BoundExemplars:
    """A retriever with no attribute path back to `for_model`.

    A running batch must not be able to retarget its own retrieval — that is the
    bug commit 56f5844 fixed, and a Protocol alone only says so to a type
    checker. Wrapping the store means there is nothing to call.
    """

    _store: Any

    def count(self, assignment_id: str) -> int:
        return self._store.count(assignment_id)

    def retrieve(
        self, assignment_id: str, query_text: str, *, k: int = 6, balance: bool = True
    ) -> list[Any]:
        return self._store.retrieve(assignment_id, query_text, k=k, balance=balance)


class GradingJob:
    """Grades a batch against one rubric, with one model, in order.

    Everything that must not change mid-batch is fixed at construction: the
    rubric, the judge, the bound retriever, and the triage thresholds. `run()`
    takes only what varies per batch.

    Invariants:
      - `run()` returns exactly one outcome per submission id it was given, in
        order. A submission that cannot be read becomes a `not_found` outcome
        rather than a silent omission, so progress reaches its total.
      - A failed grade is an `error` outcome, never an exception. One bad
        submission does not end the batch.
      - Per submission the order is fixed: fetch, gate on calibration, retrieve
        anchors, grade, triage only if the grade succeeded, persist, audit, then
        report progress. Persistence always precedes the announcement.
      - `on_progress` is called once per submission, after that submission is
        saved, with the running count and its outcome.

    Error modes: `__init__` raises ValueError for thresholds that cannot route.
    `run()` does not catch failures from the store — losing a write is not
    something to continue through.
    """

    def __init__(
        self,
        rubric: Rubric,
        *,
        judge: Judge,
        exemplars: ExemplarRetriever,
        store: GradeStore,
        low_confidence: float,
        high_confidence: float,
        materials: str = "",
    ) -> None:
        # Checked here rather than on first use: triage validates the same thing,
        # but only once a submission reaches it — after a model call has already
        # been paid for.
        if not 0 <= low_confidence < high_confidence <= 1:
            raise ValueError(
                "triage thresholds must satisfy 0 <= low < high <= 1; "
                f"got low={low_confidence}, high={high_confidence}"
            )
        self._rubric = rubric
        self._judge = judge
        self._exemplars = _BoundExemplars(exemplars)
        self._store = store
        self._low = low_confidence
        self._high = high_confidence
        self._materials = materials

    def run(
        self,
        assignment_id: str,
        submission_ids: Sequence[str],
        *,
        on_progress: OnProgress | None = None,
    ) -> list[GradeOutcome]:
        total = len(submission_ids)
        outcomes: list[GradeOutcome] = []

        for index, submission_id in enumerate(submission_ids, start=1):
            submission = self._store.fetch_submission(submission_id)
            if submission is None:
                outcome = self._not_found(submission_id, assignment_id)
                self._store.audit(outcome.record_id, "engine", "grade_skipped", outcome.record)
            else:
                outcome = self._grade_one(submission)
                self._store.save(outcome, rubric_title=self._rubric.title)
                self._store.audit(outcome.record_id, "engine", "graded", outcome.record)

            outcomes.append(outcome)
            if on_progress is not None:
                on_progress(index, total, outcome)

        return outcomes

    # -- internals ------------------------------------------------------------

    def _grade_one(self, submission: SubmissionText) -> GradeOutcome:
        record_id = f"gr_{submission.id}"
        anchors = self._anchors_for(submission)

        run = self._judge.grade(
            self._rubric, submission.text, anchors=anchors, materials=self._materials
        )
        if not run.ok:
            return GradeOutcome(
                submission_id=submission.id, record_id=record_id, state="error",
                record=_blank_record(record_id, submission, error=run.error),
            )

        verdict = triage(
            run.draft,
            self._rubric,
            submission.text,
            judge=self._judge,
            anchors=anchors,
            materials=self._materials,
            low_confidence=self._low,
            high_confidence=self._high,
            scanned=submission.scanned,
        )
        return GradeOutcome(
            submission_id=submission.id, record_id=record_id, state="drafted",
            record={
                "id": record_id,
                "submission_id": submission.id,
                "assignment_id": submission.assignment_id,
                "ai_scores": {cs.criterion_id: cs.score for cs in run.draft.criterion_scores},
                "ai_feedback": run.draft.feedback,
                "confidence": run.draft.confidence,
                "flags": [f.value for f in run.draft.flags],
                "lane": verdict.lane.value,
                "confidence_level": verdict.confidence,
                "triage_reasons": verdict.reasons,
                "triage_consistency": verdict.consistency,
                "seconds": run.seconds,
                "retries": run.retries,
            },
        )

    def _anchors_for(self, submission: SubmissionText) -> str:
        # How many exemplars unlock drafting is calibration's rule, not this loop's.
        if not can_draft(self._exemplars, submission.assignment_id):
            return ""
        hits = self._exemplars.retrieve(
            submission.assignment_id, submission.text, k=ANCHORS_PER_GRADE, balance=True
        )
        return render_anchors(hits, [c.id for c in self._rubric.criteria])

    def _not_found(self, submission_id: str, assignment_id: str) -> GradeOutcome:
        record_id = f"gr_{submission_id}"
        return GradeOutcome(
            submission_id=submission_id, record_id=record_id, state="not_found",
            record={
                "id": record_id, "submission_id": submission_id,
                "assignment_id": assignment_id, "error": "submission not found",
                "lane": None, "ai_scores": None, "ai_feedback": None,
                "confidence": None, "flags": None,
            },
        )


def _blank_record(record_id: str, submission: SubmissionText, *, error: str | None) -> dict:
    return {
        "id": record_id, "submission_id": submission.id,
        "assignment_id": submission.assignment_id, "error": error,
        "lane": None, "ai_scores": None, "ai_feedback": None,
        "confidence": None, "flags": None,
    }
