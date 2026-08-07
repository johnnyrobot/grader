"""Calibration: the cold-start gate, and the health panel a teacher reads.

The route exposing this had tests; the module computing it did not. What a
teacher sees here decides whether they trust the drafts, so the arithmetic
behind "the AI is running lenient on Evidence" needs to be pinned down.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.calibration import (  # noqa: E402  (needs the path insert above)
    COLD_START_MIN,
    can_draft,
    cold_start_status,
    compute_health,
)
from grader.db import Database  # noqa: E402
from grader.exemplars import Exemplar, ExemplarStore  # noqa: E402
from grader.overrides import OverrideStore  # noqa: E402
from grader.schema import CriterionScore, GradeDraft  # noqa: E402


@pytest.fixture
def exemplars():
    return ExemplarStore(Database.memory())


@pytest.fixture
def overrides():
    return OverrideStore(Database.memory())


def _add_exemplars(store, assignment_id, n):
    """Exemplars that count: retrieval skips any row without a usable vector."""
    for i in range(n):
        store.add(
            Exemplar(
                id=None, assignment_id=assignment_id, text=f"submission {i}",
                total=4.0, max_total=5.0, scores={"c1": 4.0},
                embedding=[0.1, 0.2, 0.3],
            ),
            embed_now=False,
        )


def _draft(**scores):
    return GradeDraft(
        criterion_scores=[
            CriterionScore(criterion_id=cid, score=s, rationale="ai")
            for cid, s in scores.items()
        ],
        feedback="ai feedback", confidence=0.8, flags=[],
    )


def _record(store, assignment_id, *, ai, teacher, max_total=10.0):
    return store.record(
        assignment_id=assignment_id,
        submission_text="student text",
        ai_draft=_draft(**ai),
        teacher_scores=teacher,
        teacher_feedback="teacher feedback",
        max_total=max_total,
    )


# ---------------------------------------------------------------------------
# Cold start: the teacher must demonstrate their standard before the AI drafts
# ---------------------------------------------------------------------------

def test_drafting_is_locked_until_the_teacher_has_graded_enough(exemplars):
    for n in range(COLD_START_MIN):
        assert can_draft(exemplars, "a1") is False
        _add_exemplars(exemplars, "a1", 1)

    assert can_draft(exemplars, "a1") is True


def test_cold_start_is_counted_per_assignment(exemplars):
    """Calibrating one assignment must not unlock drafting on another."""
    _add_exemplars(exemplars, "a1", COLD_START_MIN)

    assert can_draft(exemplars, "a1") is True
    assert can_draft(exemplars, "a2") is False


def test_cold_start_status_counts_down_in_words(exemplars):
    _add_exemplars(exemplars, "a1", COLD_START_MIN - 1)

    status = cold_start_status(exemplars, "a1")

    assert status["unlocked"] is False
    assert status["remaining"] == 1
    assert "1 more item" in status["message"]  # singular, not "1 more items"


def test_cold_start_status_says_so_once_unlocked(exemplars):
    _add_exemplars(exemplars, "a1", COLD_START_MIN)

    status = cold_start_status(exemplars, "a1")

    assert status["unlocked"] is True
    assert status["remaining"] == 0
    assert "unlocked" in status["message"]


def test_an_exemplar_without_a_usable_vector_does_not_unlock_drafting(exemplars):
    """count() gates on what retrieval can actually use, and so must this."""
    for i in range(COLD_START_MIN):
        exemplars.add(
            Exemplar(id=None, assignment_id="a1", text=f"s{i}", total=4.0,
                     max_total=5.0, scores={"c1": 4.0}),
            embed_now=False,   # no vector stored
        )

    assert can_draft(exemplars, "a1") is False


# ---------------------------------------------------------------------------
# Health panel
# ---------------------------------------------------------------------------

def test_health_of_an_assignment_nobody_has_graded_is_empty_not_an_error(
    overrides, exemplars
):
    health = compute_health(overrides, exemplars, "a1")

    assert health.qwk is None
    assert health.override_count == 0
    assert health.exemplar_count == 0
    assert health.override_rate == 0.0
    assert health.per_criterion == {}


def test_override_rate_is_overrides_over_exemplars(overrides, exemplars):
    _add_exemplars(exemplars, "a1", 4)
    _record(overrides, "a1", ai={"c1": 3.0}, teacher={"c1": 4.0})

    health = compute_health(overrides, exemplars, "a1")

    assert health.override_count == 1
    assert health.total_graded == 4
    assert health.override_rate == 0.25


def test_per_criterion_disagreement_separates_direction_from_magnitude(
    overrides, exemplars
):
    """A criterion the teacher raises as often as they lower it is not calibrated.

    Its mean delta cancels to zero; only the absolute delta shows the churn.
    """
    _record(overrides, "a1", ai={"c1": 3.0}, teacher={"c1": 5.0})   # +2
    _record(overrides, "a1", ai={"c1": 5.0}, teacher={"c1": 3.0})   # -2

    stats = compute_health(overrides, exemplars, "a1").per_criterion["c1"]

    assert stats["mean_delta"] == 0.0
    assert stats["abs_delta"] == 2.0
    assert stats["count"] == 2


def test_a_consistently_lenient_ai_shows_a_positive_mean_delta(overrides, exemplars):
    """Signed delta is teacher minus AI, so positive means the AI scored low."""
    for _ in range(3):
        _record(overrides, "a1", ai={"c1": 2.0}, teacher={"c1": 4.0})

    stats = compute_health(overrides, exemplars, "a1").per_criterion["c1"]

    assert stats["mean_delta"] == 2.0
    assert stats["abs_delta"] == 2.0


def test_criteria_are_reported_separately(overrides, exemplars):
    _record(overrides, "a1",
            ai={"thesis": 3.0, "evidence": 4.0},
            teacher={"thesis": 5.0, "evidence": 4.0})

    per_criterion = compute_health(overrides, exemplars, "a1").per_criterion

    assert per_criterion["thesis"]["mean_delta"] == 2.0
    assert per_criterion["evidence"]["mean_delta"] == 0.0


def test_health_is_scoped_to_one_assignment(overrides, exemplars):
    _record(overrides, "a1", ai={"c1": 3.0}, teacher={"c1": 4.0})
    _record(overrides, "a2", ai={"c1": 1.0}, teacher={"c1": 5.0})

    health = compute_health(overrides, exemplars, "a1")

    assert health.override_count == 1
    assert health.per_criterion["c1"]["mean_delta"] == 1.0


def test_trend_needs_two_full_windows_before_it_says_anything(overrides, exemplars):
    """Reporting a trend off a part-filled window would be noise dressed as signal."""
    for _ in range(4):
        _record(overrides, "a1", ai={"c1": 3.0}, teacher={"c1": 4.0})

    assert compute_health(overrides, exemplars, "a1", window=2).qwk_trend is not None
    assert compute_health(overrides, exemplars, "a1", window=3).qwk_trend is None
    assert compute_health(overrides, exemplars, "a1", window=20).qwk_trend is None


def test_a_harsh_ai_reports_a_negative_mean_error(overrides, exemplars):
    """mean_error is the panel's bias readout: minus means the AI scored low."""
    _record(overrides, "a1", ai={"c1": 2.0}, teacher={"c1": 4.0})

    health = compute_health(overrides, exemplars, "a1")

    assert health.mean_error == -2.0
    assert health.mean_abs_error == 2.0
    assert health.n == 1


def test_health_renders_the_summary_a_teacher_reads(overrides, exemplars):
    _add_exemplars(exemplars, "a1", 2)
    _record(overrides, "a1", ai={"c1": 3.0}, teacher={"c1": 5.0})

    rendered = str(compute_health(overrides, exemplars, "a1"))

    assert "Calibration Health" in rendered
    assert "Overrides: 1/2 (50%)" in rendered
    assert "Exemplars: 2" in rendered
    assert "c1" in rendered
