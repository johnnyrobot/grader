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
    DEMOTE_THRESHOLD,
    DISCLOSE_THRESHOLD,
    EVIDENCE_FLOOR,
    ApprovedComparison,
    can_draft,
    cold_start_status,
    compute_health,
)
from grader.db import Database  # noqa: E402
from grader.exemplars import Exemplar, ExemplarStore  # noqa: E402
from grader.overrides import OverrideStore  # noqa: E402
from grader.schema import (  # noqa: E402
    CriterionScore,
    GradeDraft,
    Rubric,
    RubricCriterion,
)


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

def _rubric(**maxes: float) -> Rubric:
    return Rubric(
        title="Essay",
        criteria=[RubricCriterion(id=cid, title=cid, max_points=m)
                  for cid, m in maxes.items()],
    )


FIVE_POINT = _rubric(c1=5.0)


class FakeApprovals:
    """The approved comparisons for an Assignment, without a database.

    Stands in for the reader api.py implements over grade_records. The calibration
    module never sees a row or a rubric title — only comparisons already resolved.
    """

    def __init__(self) -> None:
        self._rows: dict[str, list[ApprovedComparison]] = {}

    def approve(self, assignment_id: str, *, ai, teacher=None, rubric=FIVE_POINT):
        """One Approval. `teacher` is None when the teacher changed nothing."""
        self._rows.setdefault(assignment_id, []).append(
            ApprovedComparison(ai_scores=ai, teacher_scores=teacher, rubric=rubric)
        )

    def list(self, assignment_id: str, *, limit: int = 200) -> list[ApprovedComparison]:
        return self._rows.get(assignment_id, [])[:limit]


@pytest.fixture
def approvals():
    return FakeApprovals()


def _approve_many(approvals, assignment_id, n, *, ai, teacher=None, rubric=FIVE_POINT):
    for _ in range(n):
        approvals.approve(assignment_id, ai=ai, teacher=teacher, rubric=rubric)


def test_health_of_an_assignment_nobody_has_graded_is_empty_not_an_error(
    approvals, overrides, exemplars
):
    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.draft_bias is None
    assert health.n == 0
    assert health.override_count == 0
    assert health.exemplar_count == 0
    assert health.override_rate == 0.0
    assert health.per_criterion == {}


def test_an_approval_the_teacher_changed_nothing_on_still_moves_the_figures(
    approvals, overrides, exemplars
):
    """The defect this fixes: a rubber-stamped low draft used to leave no trace.

    Ten approvals a point below the teacher and ten accepted unchanged average to
    half the bias of the ten alone — which they cannot do if only the overrides
    are counted.
    """
    _approve_many(approvals, "a1", 10, ai={"c1": 3.0}, teacher={"c1": 4.0})
    _approve_many(approvals, "a1", 10, ai={"c1": 4.0}, teacher=None)

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.n == 20
    assert health.draft_bias == pytest.approx(-0.1)   # (10 x -0.2 + 10 x 0) / 20


def test_a_harsh_assignment_reports_a_negative_bias(approvals, overrides, exemplars):
    """Negative means the drafts run below the teacher — the glossary's direction."""
    _approve_many(approvals, "a1", 20, ai={"c1": 3.0}, teacher={"c1": 4.0})

    assert compute_health(approvals, overrides, exemplars, "a1").draft_bias < 0


def test_a_lenient_assignment_reports_a_positive_bias(approvals, overrides, exemplars):
    _approve_many(approvals, "a1", 20, ai={"c1": 4.0}, teacher={"c1": 3.0})

    assert compute_health(approvals, overrides, exemplars, "a1").draft_bias > 0


def test_bias_is_a_fraction_of_criterion_max_not_a_point_count(
    approvals, overrides, exemplars
):
    """One point out of five and twenty out of a hundred are the same bias.

    Absolute points mean two different things on a 5-point criterion and a
    100-point one, which is the whole reason the thresholds are fractions.
    """
    _approve_many(approvals, "a1", 20, ai={"c1": 3.0}, teacher={"c1": 4.0})
    _approve_many(approvals, "a2", 20, ai={"c1": 60.0}, teacher={"c1": 80.0},
                  rubric=_rubric(c1=100.0))

    small = compute_health(approvals, overrides, exemplars, "a1")
    large = compute_health(approvals, overrides, exemplars, "a2")

    assert small.draft_bias == pytest.approx(-0.2)
    assert large.draft_bias == pytest.approx(-0.2)


def test_half_points_are_not_rounded_away(approvals, overrides, exemplars):
    """Teachers grade in half-points; rounding to integers is why QWK left."""
    _approve_many(approvals, "a1", 20, ai={"c1": 3.0}, teacher={"c1": 3.5})

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.draft_bias == pytest.approx(-0.1)


# -- The evidence floor -----------------------------------------------------

def test_below_the_floor_there_are_counts_and_no_direction(
    approvals, overrides, exemplars
):
    _approve_many(approvals, "a1", EVIDENCE_FLOOR - 1, ai={"c1": 1.0},
                  teacher={"c1": 5.0})

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.n == EVIDENCE_FLOOR - 1     # the count is still reported
    assert health.draft_bias is None          # the direction is not
    assert health.per_criterion == {}
    assert health.discloses is False
    assert health.demotes is False


def test_the_floor_is_twenty_approvals(approvals, overrides, exemplars):
    _approve_many(approvals, "a1", EVIDENCE_FLOOR, ai={"c1": 1.0}, teacher={"c1": 5.0})

    assert EVIDENCE_FLOOR == 20
    assert compute_health(approvals, overrides, exemplars, "a1").draft_bias is not None


def test_one_approval_is_one_data_point_however_many_criteria_it_scored(
    approvals, overrides, exemplars
):
    """Draft bias is assignment-level (ADR-0001), so its denominator is `n`.

    A draft that scored four criteria must not outweigh one that scored two, or
    the figure would be an average of criteria wearing an average of approvals'
    clothes — and `n` beside it would describe a different population.
    """
    _approve_many(approvals, "a1", 19, ai={"a": 5.0, "b": 5.0},
                  teacher={"a": 5.0, "b": 5.0}, rubric=_rubric(a=5.0, b=5.0))
    approvals.approve("a1", ai={"a": 4.0}, teacher={"a": 5.0},
                      rubric=_rubric(a=5.0, b=5.0))   # one criterion, -0.2

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.n == 20
    assert health.draft_bias == pytest.approx(-0.2 / 20)


def test_n_counts_approvals_not_criterion_scores(approvals, overrides, exemplars):
    """Two criteria per approval must not buy an assignment its way past the floor."""
    _approve_many(approvals, "a1", 10, ai={"a": 1.0, "b": 1.0}, teacher={"a": 2.0, "b": 2.0},
                  rubric=_rubric(a=5.0, b=5.0))

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.n == 10
    assert health.draft_bias is None


# -- The thresholds a consumer reads rather than re-derives -----------------

def test_a_negligible_gap_clears_neither_threshold(approvals, overrides, exemplars):
    _approve_many(approvals, "a1", 20, ai={"c1": 4.9}, teacher={"c1": 5.0})  # -0.02

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.discloses is False
    assert health.demotes is False


def test_a_gap_worth_mentioning_discloses_without_demoting(
    approvals, overrides, exemplars
):
    """Grader informs the teacher before it restructures their queue."""
    _approve_many(approvals, "a1", 20, ai={"c1": 4.75}, teacher={"c1": 5.0})  # -0.05

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.discloses is True
    assert health.demotes is False


def test_a_gap_at_the_disclose_threshold_discloses(approvals, overrides, exemplars):
    _approve_many(approvals, "a1", 20, ai={"c1": 4.85}, teacher={"c1": 5.0})  # -0.03

    assert compute_health(approvals, overrides, exemplars, "a1").discloses is True


def test_a_large_gap_demotes_and_still_discloses(approvals, overrides, exemplars):
    _approve_many(approvals, "a1", 20, ai={"c1": 4.0}, teacher={"c1": 5.0})  # -0.2

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.demotes is True
    assert health.discloses is True


def test_a_lenient_gap_trips_the_thresholds_exactly_as_a_harsh_one_does(
    approvals, overrides, exemplars
):
    """A model drifting lenient inflates grades; it is caught just as readily."""
    _approve_many(approvals, "a1", 20, ai={"c1": 5.0}, teacher={"c1": 4.0})   # +0.2
    _approve_many(approvals, "a2", 20, ai={"c1": 4.0}, teacher={"c1": 5.0})   # -0.2

    lenient = compute_health(approvals, overrides, exemplars, "a1")
    harsh = compute_health(approvals, overrides, exemplars, "a2")

    assert lenient.draft_bias == -harsh.draft_bias
    assert (lenient.discloses, lenient.demotes) == (harsh.discloses, harsh.demotes)


def test_the_thresholds_are_the_ones_the_decision_record_fixed():
    assert DISCLOSE_THRESHOLD == 0.03
    assert DEMOTE_THRESHOLD == 0.10


# -- Per-criterion disagreement, over the same population -------------------

def test_per_criterion_disagreement_separates_direction_from_magnitude(
    approvals, overrides, exemplars
):
    """A criterion the teacher raises as often as they lower it is not calibrated.

    Its signed bias cancels to zero; only the absolute figure shows the churn.
    """
    _approve_many(approvals, "a1", 10, ai={"c1": 3.0}, teacher={"c1": 5.0})   # -0.4
    _approve_many(approvals, "a1", 10, ai={"c1": 5.0}, teacher={"c1": 3.0})   # +0.4

    stats = compute_health(approvals, overrides, exemplars, "a1").per_criterion["c1"]

    assert stats["bias"] == pytest.approx(0.0)
    assert stats["abs_bias"] == pytest.approx(0.4)
    assert stats["count"] == 20


def test_per_criterion_bias_is_scaled_by_that_criterion_s_own_max(
    approvals, overrides, exemplars
):
    """One point off a 5-point criterion is four times one point off a 20-point one."""
    _approve_many(approvals, "a1", 20,
                  ai={"thesis": 4.0, "evidence": 19.0},
                  teacher={"thesis": 5.0, "evidence": 20.0},
                  rubric=_rubric(thesis=5.0, evidence=20.0))

    per_criterion = compute_health(approvals, overrides, exemplars, "a1").per_criterion

    assert per_criterion["thesis"]["bias"] == pytest.approx(-0.2)
    assert per_criterion["evidence"]["bias"] == pytest.approx(-0.05)


def test_per_criterion_counts_the_approvals_the_teacher_changed_nothing_on(
    approvals, overrides, exemplars
):
    _approve_many(approvals, "a1", 10, ai={"c1": 4.0}, teacher={"c1": 5.0})
    _approve_many(approvals, "a1", 10, ai={"c1": 5.0}, teacher=None)

    stats = compute_health(approvals, overrides, exemplars, "a1").per_criterion["c1"]

    assert stats["count"] == 20
    assert stats["bias"] == pytest.approx(-0.1)


def test_health_is_scoped_to_one_assignment(approvals, overrides, exemplars):
    _approve_many(approvals, "a1", 20, ai={"c1": 4.0}, teacher={"c1": 5.0})
    _approve_many(approvals, "a2", 20, ai={"c1": 1.0}, teacher={"c1": 5.0})

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.draft_bias == pytest.approx(-0.2)


# -- What the panel keeps, and what it no longer reports ---------------------

def test_override_rate_still_counts_overrides_over_exemplars(
    approvals, overrides, exemplars
):
    """Draft bias widened its population; override rate did not."""
    _add_exemplars(exemplars, "a1", 4)
    _record(overrides, "a1", ai={"c1": 3.0}, teacher={"c1": 4.0})
    _approve_many(approvals, "a1", 20, ai={"c1": 3.0}, teacher={"c1": 4.0})

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert health.override_count == 1
    assert health.total_graded == 4
    assert health.override_rate == 0.25
    assert health.exemplar_count == 4


def test_the_panel_no_longer_reports_qwk(approvals, overrides, exemplars):
    """QWK rounds half-points away and invites comparison with the release gate's."""
    _approve_many(approvals, "a1", 20, ai={"c1": 3.0}, teacher={"c1": 4.0})

    health = compute_health(approvals, overrides, exemplars, "a1")

    assert not hasattr(health, "qwk")
    assert not hasattr(health, "qwk_trend")


def test_health_renders_the_summary_a_teacher_reads(
    approvals, overrides, exemplars
):
    _add_exemplars(exemplars, "a1", 2)
    _record(overrides, "a1", ai={"c1": 3.0}, teacher={"c1": 5.0})
    _approve_many(approvals, "a1", 20, ai={"c1": 4.0}, teacher={"c1": 5.0})

    rendered = str(compute_health(approvals, overrides, exemplars, "a1"))

    assert "Calibration Health" in rendered
    assert "Draft bias" in rendered
    assert "n=20" in rendered
    assert "Overrides: 1/2 (50%)" in rendered
    assert "Exemplars: 2" in rendered
    assert "c1" in rendered
    assert "QWK" not in rendered


def test_below_the_floor_the_summary_says_so_instead_of_a_direction(
    approvals, overrides, exemplars
):
    _approve_many(approvals, "a1", 3, ai={"c1": 1.0}, teacher={"c1": 5.0})

    rendered = str(compute_health(approvals, overrides, exemplars, "a1"))

    assert "Draft bias" in rendered
    assert "3/20" in rendered          # progress toward a meaningful figure
