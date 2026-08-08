"""The expensive signal: re-grading a shaky draft to see whether it holds.

Every existing triage test passes `allow_vote=False`, so the consistency vote
had never run under test — and it is the mechanism that decides whether a
low-confidence grade reaches the teacher as REVIEW_FIRST or is let through as
STANDARD. It is also the only path in triage that spends real model time, which
is why it is bought only when confidence is already low.

The judge here is a stub returning scripted totals, so the routing is what is
under test rather than any model's behaviour.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.schema import (  # noqa: E402
    CriterionScore,
    CriterionType,
    Flag,
    GradeDraft,
    GradeRun,
    Rubric,
    RubricCriterion,
)
from grader.triage import UNSTABLE_SPREAD, Lane, triage  # noqa: E402

MAX_POINTS = 10.0


def _rubric() -> Rubric:
    return Rubric(
        title="Essay",
        criteria=[
            RubricCriterion(id="c", title="C", max_points=MAX_POINTS,
                            criterion_type=CriterionType.ANALYTIC)
        ],
    )


def _draft(total: float, confidence: float, flags=None) -> GradeDraft:
    return GradeDraft(
        criterion_scores=[CriterionScore(criterion_id="c", score=total, rationale="r")],
        feedback="f",
        confidence=confidence,
        flags=flags or [],
    )


@dataclass
class ScriptedJudge:
    """Returns the next scripted total each time triage re-grades."""

    totals: list[float]
    calls: int = 0

    def grade(self, rubric, submission, *, anchors="", materials="") -> GradeRun:
        total = self.totals[self.calls % len(self.totals)]
        self.calls += 1
        return GradeRun(_draft(total, 0.5), "tool", "stub", 0.0, 0)


@dataclass
class FailingJudge:
    calls: int = 0

    def grade(self, rubric, submission, *, anchors="", materials="") -> GradeRun:
        self.calls += 1
        return GradeRun(None, "tool", "stub", 0.0, 0, error="model died")


# -- When the vote is bought -------------------------------------------------


def test_a_confident_grade_never_pays_for_a_vote():
    judge = ScriptedJudge(totals=[5.0])

    triage(_draft(5.0, 0.95), _rubric(), "text", judge=judge)

    assert judge.calls == 0


def test_a_low_confidence_grade_without_a_judge_is_a_programming_error():
    """Re-grading needs a judge; silently skipping it would hide the escalation."""
    with pytest.raises(ValueError, match="needs a judge"):
        triage(_draft(5.0, 0.40), _rubric(), "text", judge=None, allow_vote=True)


def test_the_vote_resamples_one_fewer_than_consistency_samples():
    """The original draft is the first vote; only the rest are re-graded."""
    judge = ScriptedJudge(totals=[5.0])

    triage(_draft(5.0, 0.40), _rubric(), "text", judge=judge, consistency_samples=4)

    assert judge.calls == 3


# -- What the vote decides ---------------------------------------------------


def test_a_score_that_holds_up_is_let_through_as_standard():
    judge = ScriptedJudge(totals=[5.0, 5.0])

    verdict = triage(_draft(5.0, 0.40), _rubric(), "text", judge=judge)

    assert verdict.lane == Lane.STANDARD
    assert any("held up" in r for r in verdict.reasons)


def test_a_score_that_moves_under_resampling_goes_to_the_teacher():
    """Spread beyond UNSTABLE_SPREAD of the rubric total means it is not stable."""
    unstable = MAX_POINTS * (UNSTABLE_SPREAD + 0.3)
    judge = ScriptedJudge(totals=[5.0 + unstable, 5.0 - unstable])

    verdict = triage(_draft(5.0, 0.40), _rubric(), "text", judge=judge)

    assert verdict.lane == Lane.REVIEW_FIRST
    assert any("unstable" in r for r in verdict.reasons)


def test_the_votes_are_reported_so_the_teacher_can_see_the_spread():
    judge = ScriptedJudge(totals=[7.0, 3.0])

    verdict = triage(_draft(5.0, 0.40), _rubric(), "text", judge=judge)

    assert verdict.votes == [5.0, 7.0, 3.0]
    assert verdict.consistency is not None


def test_consistency_is_higher_for_a_tighter_spread():
    steady = triage(_draft(5.0, 0.40), _rubric(), "text",
                    judge=ScriptedJudge(totals=[5.0, 5.0]))
    shaky = triage(_draft(5.0, 0.40), _rubric(), "text",
                   judge=ScriptedJudge(totals=[6.0, 4.0]))

    assert steady.consistency > shaky.consistency


# -- When re-grading itself fails --------------------------------------------


def test_resamples_that_fail_do_not_count_as_agreement():
    """A model that died has not confirmed anything."""
    judge = FailingJudge()

    verdict = triage(_draft(5.0, 0.40), _rubric(), "text", judge=judge)

    assert judge.calls == 2
    assert verdict.votes == [5.0]
    assert verdict.lane == Lane.REVIEW_FIRST


# -- The vote cannot rescue a hard flag --------------------------------------


def test_a_hard_flag_skips_the_vote_entirely():
    """NEEDS_HUMAN is human business; re-grading it would only cost time."""
    judge = ScriptedJudge(totals=[5.0, 5.0])

    verdict = triage(
        _draft(5.0, 0.40, flags=[Flag.NEEDS_HUMAN]), _rubric(), "text", judge=judge
    )

    assert verdict.lane == Lane.REVIEW_FIRST
    assert judge.calls == 0
