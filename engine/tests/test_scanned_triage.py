"""Scanned work never auto-clears — the rule handwriting.py always claimed.

`handwriting.py` has said since it was written that "Scanned work defaults to
REVIEW_FIRST" and that "transcription must be editable before grading". Nothing
implemented it: the Canvas import stored OCR output as plain submission text
with no record of where it came from, so triage had nothing to route on and a
confidently-graded transcription could land in HIGH_CONFIDENCE and be approved
in bulk.

The signal the grade cannot carry: the model scores the *transcription*. A word
OCR misread is invisible to it, so its confidence is about the score, not about
whether the page was read correctly.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.schema import (  # noqa: E402
    CriterionScore,
    CriterionType,
    Flag,
    GradeDraft,
    Rubric,
    RubricCriterion,
)
from grader.triage import Lane, triage  # noqa: E402


def _rubric() -> Rubric:
    return Rubric(
        title="Short answer",
        criteria=[
            RubricCriterion(
                id="correctness",
                title="Correctness",
                max_points=5.0,
                criterion_type=CriterionType.ANALYTIC,
            )
        ],
    )


def _draft(confidence: float, flags: list[Flag] | None = None) -> GradeDraft:
    return GradeDraft(
        criterion_scores=[CriterionScore(criterion_id="correctness", score=5.0,
                                         rationale="right")],
        feedback="Correct.",
        confidence=confidence,
        flags=flags or [],
    )


def test_a_confident_typed_answer_can_auto_clear():
    """The control: without the scanned signal this is a HIGH_CONFIDENCE grade."""
    verdict = triage(_draft(0.95), _rubric(), "a queue is FIFO", allow_vote=False)

    assert verdict.lane == Lane.HIGH_CONFIDENCE


def test_the_same_answer_scanned_does_not_auto_clear():
    verdict = triage(_draft(0.95), _rubric(), "a queue is FIFO",
                     allow_vote=False, scanned=True)

    assert verdict.lane == Lane.REVIEW_FIRST
    assert any("scanned" in r for r in verdict.reasons)


def test_a_scanned_item_keeps_the_reasons_that_explain_the_score():
    """A floor, not a bypass — what the score itself looked like still shows."""
    verdict = triage(
        _draft(0.95, flags=[Flag.CITATION_ISSUES]), _rubric(), "a queue is FIFO",
        allow_vote=False, scanned=True,
    )

    assert verdict.lane == Lane.REVIEW_FIRST
    assert any("citation" in r for r in verdict.reasons)
    assert any("scanned" in r for r in verdict.reasons)
    assert verdict.confidence == 0.95


def test_a_scanned_item_already_flagged_for_review_is_not_double_counted():
    verdict = triage(
        _draft(0.95, flags=[Flag.NEEDS_HUMAN]), _rubric(), "text",
        allow_vote=False, scanned=True,
    )

    assert verdict.lane == Lane.REVIEW_FIRST
    assert not any("scanned" in r for r in verdict.reasons)


@pytest.mark.parametrize("confidence", [0.2, 0.5, 0.95])
def test_scanned_work_lands_in_review_first_at_every_confidence(confidence):
    verdict = triage(_draft(confidence), _rubric(), "text",
                     allow_vote=False, scanned=True)

    assert verdict.lane == Lane.REVIEW_FIRST


# -- The provenance has to survive the import to be worth anything ------------


def test_a_submission_records_whether_its_text_came_from_ocr(tmp_path, monkeypatch):
    """The signal is only useful if the import writes it down."""
    import importlib

    sys.modules.pop("api", None)
    api = importlib.import_module("api")
    try:
        monkeypatch.setattr(api, "DB_PATH", tmp_path / "engine.db")
        db = api._db()
        db.execute(
            "INSERT INTO submissions (id, assignment_id, student_ref, text, state, "
            "source_kind, created_at) VALUES (?,?,?,?,?,?,?)",
            ("sub_scan", "a1", "1", "transcribed", "imported", api.SCANNED, api._now()),
        )
        db.execute(
            "INSERT INTO submissions (id, assignment_id, student_ref, text, state, "
            "source_kind, created_at) VALUES (?,?,?,?,?,?,?)",
            ("sub_typed", "a1", "2", "typed", "imported", api.TYPED, api._now()),
        )
        db.commit()

        store = api._SqliteGradeStore()

        assert store.fetch_submission("sub_scan").scanned is True
        assert store.fetch_submission("sub_typed").scanned is False
    finally:
        sys.modules.pop("api", None)


def test_a_database_written_before_source_kind_existed_still_opens(tmp_path, monkeypatch):
    """The migration must not strand a teacher's existing work."""
    import importlib
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE submissions (id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL, "
        "student_ref TEXT, text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'imported', "
        "created_at TEXT NOT NULL);"
        "INSERT INTO submissions VALUES ('old_1','a1','1','older work','imported','t');"
    )
    conn.commit()
    conn.close()

    sys.modules.pop("api", None)
    api = importlib.import_module("api")
    try:
        monkeypatch.setattr(api, "DB_PATH", path)
        row = api._db().execute(
            "SELECT source_kind FROM submissions WHERE id = 'old_1'"
        ).fetchone()
        assert row["source_kind"] == api.TYPED
    finally:
        sys.modules.pop("api", None)


def test_only_image_attachments_are_recognised_as_scanned_today(tmp_path):
    """The boundary of the signal, stated rather than implied.

    `_parse_image` is the OCR path and reports source_type='image', which is
    what the import writes down. A *scanned PDF* goes through Docling instead,
    comes back as 'pdf', and `_parse_with_docling` hardcodes confidence=1.0 —
    so there is no signal that its text came from OCR, and it will not be
    routed to REVIEW_FIRST.

    Photos of handwritten work (the handwriting.py path) are covered. Scanned
    PDFs are not. Closing that needs Docling's own OCR confidence surfaced
    through ParsedDoc, which `_parse_with_docling` does not currently do.
    """
    from grader.parse import parse

    typed = tmp_path / "essay.txt"
    typed.write_text("A queue is first in, first out.")
    assert parse(typed).source_type == "txt"

    # The claim under test: 'image' is the only source_type the import treats as
    # scanned, and it comes only from the OCR path.
    import inspect

    from grader import parse as parse_module

    assert 'source_type="image"' in inspect.getsource(parse_module._parse_image)
    assert 'source_type="image"' not in inspect.getsource(
        parse_module._parse_with_docling
    )
