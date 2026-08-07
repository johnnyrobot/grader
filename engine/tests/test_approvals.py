"""The approval invariant: nothing reaches Canvas without a teacher approving it.

Before this module the invariant was enforced by hand in three places — two
independent writers of grade_records.approved_at and one inline re-check in
canvas_post — and apply_override(), which produces the approved grade and the
Exemplar derived from it, had no test at all.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.db import Database
from grader.exemplars import ExemplarStore
from grader.overrides import OverrideStore
from grader.schema import CriterionScore, GradeDraft

GRADE_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL, student_ref TEXT,
    text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'imported', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grade_records (
    id TEXT PRIMARY KEY, submission_id TEXT NOT NULL, assignment_id TEXT NOT NULL,
    rubric_version INTEGER NOT NULL DEFAULT 1, rubric_title TEXT,
    ai_scores_json TEXT, ai_feedback TEXT, ai_confidence REAL, ai_flags_json TEXT,
    teacher_scores_json TEXT, teacher_feedback TEXT, lane TEXT,
    approved_at TEXT, posted_at TEXT, created_at TEXT NOT NULL
);
"""


@pytest.fixture
def engine_db(tmp_path):
    """A stand-in for api._db: one connection, handed back on every call."""
    db = Database.at(tmp_path / "engine.db")
    db.executescript(GRADE_SCHEMA)
    yield lambda: db
    db.close()


@pytest.fixture
def audit_log():
    events = []
    return events, lambda entity, actor, event, payload: events.append(
        (entity, actor, event, payload)
    )


@pytest.fixture
def seeded(engine_db):
    db = engine_db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES ('sub_1', 'a1', '98765', 'student text', 'graded', 'now')"
    )
    db.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, ai_scores_json, "
        "ai_feedback, ai_confidence, ai_flags_json, lane, created_at) "
        "VALUES ('gr_1', 'sub_1', 'a1', ?, 'ai feedback', 0.8, '[]', 'standard', 'now')",
        (json.dumps({"c1": 3.0}),),
    )
    db.commit()
    return engine_db


def _draft():
    return GradeDraft(
        criterion_scores=[CriterionScore(criterion_id="c1", score=3.0, rationale="ai")],
        feedback="ai feedback", confidence=0.8, flags=[],
    )


def _stores():
    return OverrideStore(Database.memory()), ExemplarStore(Database.memory())


def test_approve_stamps_the_grade_and_audits_it(seeded, audit_log):
    from grader.approvals import ApprovalStore

    events, audit = audit_log
    approvals = ApprovalStore(seeded, audit)

    result = approvals.approve("gr_1")

    db = seeded()
    row = db.execute("SELECT approved_at FROM grade_records WHERE id='gr_1'").fetchone()
    db.close()
    assert row["approved_at"] == result.approved_at
    assert [e[2] for e in events] == ["approve"]


def test_approving_twice_is_refused_and_changes_nothing(seeded, audit_log):
    from grader.approvals import AlreadyApproved, ApprovalStore

    events, audit = audit_log
    approvals = ApprovalStore(seeded, audit)
    first = approvals.approve("gr_1")

    with pytest.raises(AlreadyApproved):
        approvals.approve("gr_1")

    db = seeded()
    row = db.execute("SELECT approved_at FROM grade_records WHERE id='gr_1'").fetchone()
    db.close()
    assert row["approved_at"] == first.approved_at
    assert [e[2] for e in events] == ["approve"]  # no second event


def test_unknown_grade_is_an_error_not_a_silent_approval(seeded, audit_log):
    from grader.approvals import ApprovalStore, GradeNotFound

    _, audit = audit_log
    approvals = ApprovalStore(seeded, audit)

    with pytest.raises(GradeNotFound):
        approvals.approve("gr_missing")


def test_plain_approval_never_produces_an_exemplar(seeded, audit_log):
    """Only a correction teaches the model. Approving as-is must not.

    Structural, not incidental: approve() holds no reference to either store.
    """
    from grader.approvals import ApprovalStore

    _, audit = audit_log
    override_store, exemplar_store = _stores()
    approvals = ApprovalStore(seeded, audit)

    approvals.approve("gr_1")

    assert override_store.count("a1") == 0
    assert exemplar_store.count("a1") == 0


def test_override_records_the_correction_before_approving_it(seeded, audit_log, monkeypatch):
    """A failed derivation must not leave an approved grade behind.

    Ordering is the guarantee: a crash can leave a recorded correction with no
    approval — safe, postable() excludes it — but never an approval with no
    correction.
    """
    from grader import approvals as approvals_mod
    from grader.approvals import ApprovalStore

    _, audit = audit_log
    override_store, exemplar_store = _stores()
    approvals = ApprovalStore(seeded, audit)

    def exploding_apply_override(*a, **kw):
        raise RuntimeError("derivation failed")

    monkeypatch.setattr(approvals_mod, "apply_override", exploding_apply_override)

    with pytest.raises(RuntimeError):
        approvals.override(
            "gr_1", override_store=override_store, exemplar_store=exemplar_store,
            assignment_id="a1", submission_text="student text", ai_draft=_draft(),
            teacher_scores={"c1": 4.0}, teacher_feedback="raised", max_total=5.0,
        )

    row = seeded().execute(
        "SELECT approved_at FROM grade_records WHERE id='gr_1'"
    ).fetchone()
    assert row["approved_at"] is None


def test_override_turns_the_correction_into_an_anchor_and_approves(
    seeded, audit_log, monkeypatch
):
    """The whole loop, with the real apply_override: correct, anchor, approve.

    Every other override test stubs the derivation out. This one runs it, because
    "the teacher's correction is retrievable as an anchor for the next
    submission" is the promise calibration rests on.
    """
    from grader.approvals import ApprovalStore

    events, audit = audit_log
    override_store, exemplar_store = _stores()
    approvals = ApprovalStore(seeded, audit)
    # The only thing in the loop that would leave the machine.
    monkeypatch.setattr("grader.exemplars.embed", lambda texts, **kw: [[0.1, 0.2]] * len(texts))

    approval = approvals.override(
        "gr_1", override_store=override_store, exemplar_store=exemplar_store,
        assignment_id="a1", submission_text="student text", ai_draft=_draft(),
        teacher_scores={"c1": 4.0}, teacher_feedback="raised", max_total=5.0,
    )

    # The correction is recorded, with the difference from what the AI proposed...
    assert approval.override.ai_total == 3.0
    assert approval.override.teacher_total == 4.0
    assert approval.override.criterion_delta == {"c1": 1.0}

    # ...it is immediately an anchor for the next submission...
    assert exemplar_store.count("a1") == 1
    anchor = exemplar_store._all("a1")[0]
    assert anchor.source == "override"
    assert anchor.total == 4.0
    assert anchor.scores == {"c1": 4.0}
    assert anchor.comment == "raised"

    # ...and the grade is approved, once, with one moment of record.
    row = seeded().execute(
        "SELECT approved_at, teacher_scores_json, teacher_feedback "
        "FROM grade_records WHERE id='gr_1'"
    ).fetchone()
    assert row["approved_at"] == approval.approved_at
    assert json.loads(row["teacher_scores_json"]) == {"c1": 4.0}
    assert row["teacher_feedback"] == "raised"
    assert [e[2] for e in events] == ["override"]


def test_overriding_an_already_approved_grade_is_allowed(seeded, audit_log, monkeypatch):
    """A teacher revising their own correction is legitimate; approve() is not."""
    from grader.approvals import AlreadyApproved, ApprovalStore

    _, audit = audit_log
    override_store, exemplar_store = _stores()
    approvals = ApprovalStore(seeded, audit)
    monkeypatch.setattr("grader.exemplars.embed", lambda texts, **kw: [[0.1, 0.2]] * len(texts))

    kwargs = dict(
        override_store=override_store, exemplar_store=exemplar_store,
        assignment_id="a1", submission_text="student text", ai_draft=_draft(),
        teacher_feedback="raised", max_total=5.0,
    )
    approvals.override("gr_1", teacher_scores={"c1": 4.0}, **kwargs)
    second = approvals.override("gr_1", teacher_scores={"c1": 5.0}, **kwargs)

    assert second.override.teacher_total == 5.0
    with pytest.raises(AlreadyApproved):
        approvals.approve("gr_1")


# ---------------------------------------------------------------------------
# postable(): the only supported way to ask "may this go to Canvas?"
# ---------------------------------------------------------------------------

def _add_grade(engine_db, gid, *, student_ref="98765", approved=True, posted=False,
                teacher_scores=None, teacher_feedback=None, ai_feedback="ai feedback"):
    db = engine_db()
    db.execute(
        "INSERT OR IGNORE INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?, 'a1', ?, 'text', 'graded', 'now')",
        (f"sub_{gid}", student_ref),
    )
    db.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, ai_scores_json, "
        "ai_feedback, teacher_scores_json, teacher_feedback, approved_at, posted_at, created_at) "
        "VALUES (?, ?, 'a1', ?, ?, ?, ?, ?, ?, 'now')",
        (gid, f"sub_{gid}", json.dumps({"c1": 3.0}), ai_feedback,
         json.dumps(teacher_scores) if teacher_scores else None, teacher_feedback,
         "2026-01-01T00:00:00Z" if approved else None,
         "2026-01-02T00:00:00Z" if posted else None),
    )
    db.commit()


def test_postable_sorts_grades_by_why_they_may_or_may_not_go(engine_db, audit_log):
    from grader.approvals import ApprovalStore, PostSkipReason

    _, audit = audit_log
    _add_grade(engine_db, "gr_ok")
    _add_grade(engine_db, "gr_unapproved", approved=False)
    _add_grade(engine_db, "gr_no_user", student_ref=None)
    _add_grade(engine_db, "gr_posted", posted=True)

    batch = ApprovalStore(engine_db, audit).postable(
        ["gr_ok", "gr_unapproved", "gr_no_user", "gr_posted", "gr_absent"]
    )

    assert batch.grade_ids == ["gr_ok"]
    assert {s.grade_id: s.reason for s in batch.skipped} == {
        "gr_unapproved": PostSkipReason.NOT_APPROVED,
        "gr_no_user": PostSkipReason.MISSING_CANVAS_USER_ID,
        "gr_posted": PostSkipReason.ALREADY_POSTED,
        "gr_absent": PostSkipReason.NOT_FOUND,
    }


def test_postable_prefers_the_teachers_scores_and_words(engine_db, audit_log):
    from grader.approvals import ApprovalStore

    _, audit = audit_log
    _add_grade(engine_db, "gr_t", teacher_scores={"c1": 4.5},
               teacher_feedback="teacher words")

    ready = ApprovalStore(engine_db, audit).postable(["gr_t"]).ready[0]

    assert ready.posted_grade == 4.5          # not the AI's 3.0
    assert ready.text_comment == "teacher words"


def test_postable_falls_back_to_a_default_comment(engine_db, audit_log):
    from grader.approvals import ApprovalStore, DEFAULT_COMMENT

    _, audit = audit_log
    _add_grade(engine_db, "gr_quiet", ai_feedback=None)

    ready = ApprovalStore(engine_db, audit).postable(["gr_quiet"]).ready[0]

    assert ready.text_comment == DEFAULT_COMMENT


def test_grade_data_is_the_shape_canvas_expects(engine_db, audit_log):
    from grader.approvals import ApprovalStore

    _, audit = audit_log
    _add_grade(engine_db, "gr_ok", teacher_scores={"c1": 4.0}, teacher_feedback="ok")

    batch = ApprovalStore(engine_db, audit).postable(["gr_ok"])

    assert batch.grade_data() == {"98765": {"posted_grade": 4.0, "text_comment": "ok"}}


def test_mark_posted_closes_the_grade_to_further_posting(engine_db, audit_log):
    from grader.approvals import ApprovalStore, PostSkipReason

    _, audit = audit_log
    _add_grade(engine_db, "gr_ok")
    approvals = ApprovalStore(engine_db, audit)

    assert approvals.postable(["gr_ok"]).grade_ids == ["gr_ok"]
    approvals.mark_posted(["gr_ok"])
    again = approvals.postable(["gr_ok"])

    assert again.ready == []
    assert again.skipped[0].reason is PostSkipReason.ALREADY_POSTED


def test_approved_at_has_exactly_one_writer_in_the_codebase():
    """Turns the invariant into a regression test rather than an intention.

    Two independent writers is what this module exists to end; a third would
    otherwise be invisible until a grade reached a student unapproved.
    """
    engine = Path(__file__).parents[1]
    writers = {
        str(path.relative_to(engine))
        for path in engine.rglob("*.py")
        if "tests" not in path.parts
        for line in path.read_text().splitlines()
        if "SET approved_at" in line or "approved_at = ?" in line
    }
    assert writers == {"grader/approvals.py"}, writers
