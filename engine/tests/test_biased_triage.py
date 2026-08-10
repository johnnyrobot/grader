"""A measurably biased Assignment loses its HIGH_CONFIDENCE lane.

Lanes routed on confidence, flags, criterion type and scanned-ness, with no
notion of direction — so a confidently wrong under-score was labelled exactly
like a confidently correct one, and the queue's own labelling vouched for the
drafts most likely to mislead the teacher (ADR-0001).

The rule is a floor, not a reroute, and it mirrors the one for scanned work: the
label is withdrawn, the score is never touched. Triage stays a pure function —
it is handed a signed, already-scaled `Draft bias` and looks nothing up.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.draftbias import DEMOTE_THRESHOLD  # noqa: E402
from grader.schema import (  # noqa: E402
    CriterionScore,
    CriterionType,
    Flag,
    GradeDraft,
    Rubric,
    RubricCriterion,
)
from grader.triage import Lane, triage  # noqa: E402

HARSH = -(DEMOTE_THRESHOLD + 0.05)  # drafts run below the teacher, past the threshold
LENIENT = -HARSH                    # the same distance the other way


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


def _draft(confidence: float = 0.95, flags: list[Flag] | None = None) -> GradeDraft:
    return GradeDraft(
        criterion_scores=[CriterionScore(criterion_id="correctness", score=3.0,
                                         rationale="mostly right")],
        feedback="Nearly.",
        confidence=confidence,
        flags=flags or [],
    )


def test_an_unmeasured_assignment_still_auto_clears():
    """The control: with no bias to speak of this is a HIGH_CONFIDENCE grade."""
    verdict = triage(_draft(), _rubric(), "a queue is FIFO", allow_vote=False)

    assert verdict.lane == Lane.HIGH_CONFIDENCE


def test_a_harsh_assignment_loses_its_high_confidence_lane():
    verdict = triage(_draft(), _rubric(), "a queue is FIFO",
                     allow_vote=False, draft_bias=HARSH)

    assert verdict.lane == Lane.STANDARD
    assert any("below" in r for r in verdict.reasons)


def test_a_lenient_assignment_demotes_exactly_as_a_harsh_one_does():
    """A model drifting lenient inflates grades; it is caught just as readily."""
    verdict = triage(_draft(), _rubric(), "a queue is FIFO",
                     allow_vote=False, draft_bias=LENIENT)

    assert verdict.lane == Lane.STANDARD
    assert any("above" in r for r in verdict.reasons)


def test_an_assignment_short_of_the_demote_threshold_is_unaffected():
    """−0.33 of a point on a 5-point scale is 0.066 of max: it discloses, it does
    not demote. That figure staying in HIGH_CONFIDENCE is the intended reading."""
    verdict = triage(_draft(), _rubric(), "a queue is FIFO",
                     allow_vote=False, draft_bias=-0.066)

    assert verdict.lane == Lane.HIGH_CONFIDENCE


def test_an_assignment_exactly_on_the_threshold_clears_it():
    verdict = triage(_draft(), _rubric(), "a queue is FIFO",
                     allow_vote=False, draft_bias=-DEMOTE_THRESHOLD)

    assert verdict.lane == Lane.STANDARD


def test_an_assignment_below_the_evidence_floor_is_unaffected():
    """No direction is knowable yet, so none is acted on — whatever the drafts
    happen to look like. The floor is expressed as the absence of a figure."""
    verdict = triage(_draft(), _rubric(), "a queue is FIFO",
                     allow_vote=False, draft_bias=None)

    assert verdict.lane == Lane.HIGH_CONFIDENCE


@pytest.mark.parametrize("bias", [HARSH, LENIENT, -0.9, 0.9])
def test_demotion_never_produces_review_first(bias):
    """A measured bias must not flood the must-read pile."""
    verdict = triage(_draft(), _rubric(), "a queue is FIFO",
                     allow_vote=False, draft_bias=bias)

    assert verdict.lane == Lane.STANDARD


def test_demotion_never_promotes_a_lane():
    """A draft that was already going to be read stays that way."""
    standard = triage(_draft(confidence=0.80), _rubric(), "text",
                      allow_vote=False, draft_bias=HARSH)
    review_first = triage(_draft(confidence=0.95, flags=[Flag.NEEDS_HUMAN]), _rubric(),
                          "text", allow_vote=False, draft_bias=HARSH)

    assert standard.lane == Lane.STANDARD
    assert review_first.lane == Lane.REVIEW_FIRST


def test_a_demoted_draft_keeps_its_scores_confidence_and_flags():
    """Nothing is adjusted to compensate: what the teacher approves is what the
    model produced."""
    draft = _draft(flags=[Flag.CITATION_ISSUES])

    verdict = triage(draft, _rubric(), "a queue is FIFO",
                     allow_vote=False, draft_bias=HARSH)

    assert verdict.lane == Lane.STANDARD
    assert verdict.confidence == 0.95
    assert [(cs.criterion_id, cs.score) for cs in draft.criterion_scores] == [
        ("correctness", 3.0)
    ]
    assert draft.confidence == 0.95
    assert draft.flags == [Flag.CITATION_ISSUES]


def test_a_demoted_draft_keeps_the_reasons_that_explain_the_score():
    """A floor, not a bypass — what the score itself looked like still shows."""
    verdict = triage(_draft(flags=[Flag.CITATION_ISSUES]), _rubric(), "a queue is FIFO",
                     allow_vote=False, draft_bias=HARSH)

    assert any("citation" in r for r in verdict.reasons)
    assert any("below" in r for r in verdict.reasons)


def test_a_clean_draft_still_says_what_the_score_looked_like_when_demoted():
    """The reason a demoted draft would otherwise lose.

    A confident unflagged grade has no reason of its own until the fallback
    supplies one, so a demotion appended at the end would leave the teacher with
    an explanation of the demotion and nothing about the grade."""
    verdict = triage(_draft(), _rubric(), "a queue is FIFO",
                     allow_vote=False, draft_bias=HARSH)

    assert "confident, no flags" in verdict.reasons
    assert any("below" in r for r in verdict.reasons)


def test_scanned_work_still_outranks_a_measured_bias():
    """Two floors, and the stricter one stands."""
    verdict = triage(_draft(), _rubric(), "a queue is FIFO",
                     allow_vote=False, draft_bias=HARSH, scanned=True)

    assert verdict.lane == Lane.REVIEW_FIRST
    assert any("scanned" in r for r in verdict.reasons)


def test_triage_looks_nothing_up_to_decide_this():
    """The bias arrives as an argument. Lane assignment stays a pure function of
    its inputs — no store, no database, no I/O (ADR-0001: the demotion depends on
    no new database access inside triage).

    Asserted on the import graph, not on the source text: a module that reads no
    rows itself but imports the one that does has still acquired the dependency,
    and grep cannot see that."""
    import inspect
    import subprocess
    import sys

    from grader import triage as triage_module

    params = inspect.signature(triage_module.triage).parameters
    assert "draft_bias" in params
    assert params["draft_bias"].default is None

    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys, grader.triage; "
         "print(','.join(m for m in ('sqlite3', 'httpx', 'grader.db', 'grader.exemplars', "
         "'grader.overrides', 'grader.calibration') if m in sys.modules))"],
        cwd=str(Path(__file__).parents[1]), capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "", f"triage drags in {probe.stdout.strip()}"
