"""The grading pipeline, run in-process.

Before this module the loop lived inside engine/api.py behind a background
thread, and every test swapped threading.Thread for a fake that captured the
constructor arguments without calling the target — so the loop itself had never
executed under test.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.gradingjob import GradingJob, SubmissionText
from grader.schema import (
    CriterionScore, CriterionType, Flag, GradeDraft, GradeRun, Rubric, RubricCriterion,
)
from grader.triage import Lane, Triage


def _rubric():
    return Rubric(title="Essay", criteria=[
        RubricCriterion(id="thesis", title="Thesis", max_points=5.0,
                        criterion_type=CriterionType.ANALYTIC),
    ])


def _draft(confidence=0.9, score=4.0):
    return GradeDraft(
        criterion_scores=[CriterionScore(criterion_id="thesis", score=score, rationale="why")],
        feedback="feedback", confidence=confidence, flags=[],
    )


class FakeJudge:
    """Grades from a script keyed by submission id, or a default draft."""

    def __init__(self, failures=(), draft=None):
        self.failures = set(failures)
        self.calls = []
        self._draft = draft or _draft()

    def grade(self, rubric, submission, *, anchors="", materials=""):
        self.calls.append({"submission": submission, "anchors": anchors})
        if submission in self.failures:
            return GradeRun(None, "tool", "fake", 0.0, 0, error="model timed out")
        return GradeRun(self._draft, "tool", "fake", 0.1, 0)


class FakeExemplars:
    def __init__(self, count=0):
        self._count = count
        self.retrieved = []

    def count(self, assignment_id):
        return self._count

    def retrieve(self, assignment_id, query_text, *, k=6, balance=True):
        self.retrieved.append(query_text)
        return []


class FakeStore:
    def __init__(self, submissions):
        self.submissions = {s.id: s for s in submissions}
        self.saved = []
        self.audited = []

    def fetch_submission(self, submission_id):
        return self.submissions.get(submission_id)

    def save(self, outcome, *, rubric_title):
        self.saved.append((outcome, rubric_title))

    def audit(self, entity, actor, event, payload):
        self.audited.append((entity, actor, event))


def _job(judge=None, exemplars=None, store=None, **kw):
    return GradingJob(
        _rubric(),
        judge=judge or FakeJudge(),
        exemplars=exemplars or FakeExemplars(),
        store=store or FakeStore([SubmissionText("sub_1", "a1", "an essay")]),
        low_confidence=kw.pop("low_confidence", 0.6),
        high_confidence=kw.pop("high_confidence", 0.9),
        **kw,
    )


def test_running_a_batch_grades_persists_and_reports_each_submission(monkeypatch):
    monkeypatch.setattr(
        "grader.gradingjob.triage",
        lambda *a, **kw: Triage(Lane.STANDARD, 0.9, None),
    )
    store = FakeStore([
        SubmissionText("sub_1", "a1", "first essay"),
        SubmissionText("sub_2", "a1", "second essay"),
    ])
    progress = []

    outcomes = _job(store=store).run(
        "a1", ["sub_1", "sub_2"], on_progress=lambda done, total, o: progress.append((done, total)),
    )

    assert [o.state for o in outcomes] == ["drafted", "drafted"]
    assert progress == [(1, 2), (2, 2)]
    assert len(store.saved) == 2
    assert [e[2] for e in store.audited] == ["graded", "graded"]


def test_one_failed_grade_does_not_abort_the_batch(monkeypatch):
    monkeypatch.setattr(
        "grader.gradingjob.triage",
        lambda *a, **kw: Triage(Lane.STANDARD, 0.9, None),
    )
    store = FakeStore([
        SubmissionText("sub_1", "a1", "first"),
        SubmissionText("sub_2", "a1", "second"),
    ])

    outcomes = _job(judge=FakeJudge(failures=["second"]), store=store).run(
        "a1", ["sub_1", "sub_2"],
    )

    assert [o.state for o in outcomes] == ["drafted", "error"]
    assert outcomes[1].record["error"] == "model timed out"


def test_a_submission_that_cannot_be_read_is_still_accounted_for(monkeypatch):
    monkeypatch.setattr(
        "grader.gradingjob.triage",
        lambda *a, **kw: Triage(Lane.STANDARD, 0.9, None),
    )
    store = FakeStore([SubmissionText("sub_1", "a1", "first")])
    progress = []

    outcomes = _job(store=store).run(
        "a1", ["sub_1", "sub_gone"], on_progress=lambda d, t, o: progress.append(d),
    )

    assert [o.state for o in outcomes] == ["drafted", "not_found"]
    assert progress == [1, 2]
    assert [e[2] for e in store.audited] == ["graded", "grade_skipped"]


def test_anchors_are_retrieved_only_once_calibration_says_so(monkeypatch):
    """The cold-start rule belongs to calibration, not to this loop."""
    from grader import calibration

    monkeypatch.setattr(
        "grader.gradingjob.triage",
        lambda *a, **kw: Triage(Lane.STANDARD, 0.9, None),
    )
    monkeypatch.setattr(calibration, "COLD_START_MIN", 5)
    exemplars = FakeExemplars(count=4)

    _job(exemplars=exemplars).run("a1", ["sub_1"])

    assert exemplars.retrieved == []


def test_thresholds_are_checked_before_any_model_call():
    """Grading is the expensive part. A misconfigured job must fail before it."""
    judge = FakeJudge()

    with pytest.raises(ValueError):
        GradingJob(
            _rubric(), judge=judge, exemplars=FakeExemplars(),
            store=FakeStore([]), low_confidence=0.9, high_confidence=0.6,
        )

    assert judge.calls == []


def test_the_job_cannot_rebind_the_exemplar_model_midway():
    """for_model() is unreachable from inside a running job, not merely unused."""
    job = _job()
    assert not hasattr(job._exemplars, "for_model")


# -- Draft bias: a biased Assignment loses its HIGH_CONFIDENCE lane -----------
#
# The lane the teacher actually sees, decided by the loop that assembles it. The
# job resolves Draft bias once for the Assignment and hands the same figure to
# every triage call; triage looks nothing up (ADR-0001).


class FakeDraftBias:
    """The Assignment's measured Draft bias, signed and already scaled.

    None is the below-the-floor answer: not "no bias", but "no direction is
    knowable yet".
    """

    def __init__(self, value=None):
        self.value = value
        self.calls = []

    def __call__(self, assignment_id):
        self.calls.append(assignment_id)
        return self.value


def _batch(bias_value, *, submissions=("sub_1",), **kw):
    """Grade a batch through the real triage, at a confidence that auto-clears."""
    store = FakeStore([SubmissionText(s, "a1", f"essay {s}") for s in submissions])
    bias = FakeDraftBias(bias_value)
    outcomes = _job(store=store, draft_bias=bias, **kw).run("a1", list(submissions))
    return outcomes, bias


def test_an_unmeasured_assignment_still_produces_a_high_confidence_lane():
    """The control: this batch auto-clears when nothing has been measured."""
    outcomes, _ = _batch(None)

    assert outcomes[0].record["lane"] == "high_confidence"


def test_a_biased_assignment_stops_calling_its_drafts_high_confidence():
    outcomes, _ = _batch(-0.15)

    assert outcomes[0].record["lane"] == "standard"


def test_a_lenient_assignment_demotes_exactly_as_a_harsh_one_does():
    outcomes, _ = _batch(0.15)

    assert outcomes[0].record["lane"] == "standard"


def test_an_assignment_short_of_the_demote_threshold_is_unaffected():
    outcomes, _ = _batch(-0.066)

    assert outcomes[0].record["lane"] == "high_confidence"


def test_demotion_never_produces_review_first():
    """A measured bias moves the draft to STANDARD, not into the must-read pile."""
    outcomes, _ = _batch(-0.9)

    assert outcomes[0].record["lane"] == "standard"


def test_a_demoted_draft_keeps_its_scores_confidence_and_flags():
    """Nothing is adjusted to compensate — the record is the draft the model made."""
    demoted, _ = _batch(-0.15)
    untouched, _ = _batch(None)

    record = demoted[0].record
    assert record["ai_scores"] == untouched[0].record["ai_scores"] == {"thesis": 4.0}
    assert record["confidence"] == 0.9
    assert record["flags"] == []


def test_draft_bias_is_resolved_once_per_run_not_once_per_submission():
    """It is a property of the Assignment, and every draft in the batch is routed
    against the same figure."""
    outcomes, bias = _batch(-0.15, submissions=("sub_1", "sub_2", "sub_3"))

    assert bias.calls == ["a1"]
    assert [o.record["lane"] for o in outcomes] == ["standard"] * 3


def test_a_job_given_no_bias_source_grades_as_before():
    """The input is optional: nothing about a caller that has not measured changes."""
    store = FakeStore([SubmissionText("sub_1", "a1", "an essay")])

    outcomes = _job(store=store).run("a1", ["sub_1"])

    assert outcomes[0].record["lane"] == "high_confidence"
