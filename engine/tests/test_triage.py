import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

pytest = __import__("pytest")

from grader.schema import CriterionType, Flag, GradeDraft, Rubric, RubricCriterion, CriterionScore

try:
    from grader.triage import Lane, Triage, QueueStats, summarize, triage, LOW_CONFIDENCE, HIGH_CONFIDENCE
    HAS_TRIAGE = True
except ImportError:
    HAS_TRIAGE = False

pytestmark = pytest.mark.skipif(not HAS_TRIAGE, reason="pydantic-ai not installed")


def _rubric(open_ended=False):
    ct = CriterionType.OPEN_ENDED if open_ended else CriterionType.ANALYTIC
    return Rubric(
        title="T",
        criteria=[RubricCriterion(id="c", title="C", max_points=5.0, criterion_type=ct)],
    )


def _draft(confidence, flags=None, total=3.0):
    cs = CriterionScore(criterion_id="c", score=total, rationale="r")
    return GradeDraft(
        criterion_scores=[cs],
        feedback="f",
        confidence=confidence,
        flags=flags or [],
    )


def test_blocking_flag_routes_review_first():
    r = _rubric()
    d = _draft(0.95, flags=[Flag.NEEDS_HUMAN])
    t = triage(d, r, "submission text", allow_vote=False)
    assert t.lane == Lane.REVIEW_FIRST
    assert "flagged" in t.reasons[0]


def test_unreadable_routes_review_first():
    r = _rubric()
    d = _draft(0.95, flags=[Flag.UNREADABLE])
    t = triage(d, r, "text", allow_vote=False)
    assert t.lane == Lane.REVIEW_FIRST


def test_off_topic_routes_review_first():
    r = _rubric()
    d = _draft(0.95, flags=[Flag.OFF_TOPIC])
    t = triage(d, r, "text", allow_vote=False)
    assert t.lane == Lane.REVIEW_FIRST


def test_high_confidence_no_flags_no_open_ended():
    r = _rubric(open_ended=False)
    d = _draft(0.90)
    t = triage(d, r, "text", allow_vote=False)
    assert t.lane == Lane.HIGH_CONFIDENCE


def test_high_confidence_open_ended_demoted():
    r = _rubric(open_ended=True)
    d = _draft(0.90)
    t = triage(d, r, "text", allow_vote=False)
    assert t.lane == Lane.STANDARD
    assert any("style bias" in r for r in t.reasons)


def test_citation_issues_not_blocking():
    r = _rubric()
    d = _draft(0.90, flags=[Flag.CITATION_ISSUES])
    t = triage(d, r, "text", allow_vote=False)
    assert t.lane == Lane.HIGH_CONFIDENCE
    assert any("citation" in r for r in t.reasons)


def test_medium_confidence_standard():
    r = _rubric(open_ended=False)
    d = _draft(0.75)
    t = triage(d, r, "text", allow_vote=False)
    assert t.lane == Lane.STANDARD


def test_low_confidence_no_vote_review_first():
    r = _rubric(open_ended=False)
    d = _draft(0.50)
    t = triage(d, r, "text", allow_vote=False)
    assert t.lane == Lane.REVIEW_FIRST


def test_queue_stats_summary():
    stats = QueueStats(review_first=2, standard=5, high_confidence=3)
    s = stats.summary(spot_check_rate=0.2)
    assert "10" in s
    assert "7" in s or "read 7" in s


def test_queue_stats_total():
    stats = QueueStats(review_first=1, standard=2, high_confidence=3)
    assert stats.total == 6


def test_summarize():
    triages = [
        Triage(Lane.REVIEW_FIRST, 0.3, None, ["r"]),
        Triage(Lane.STANDARD, 0.75, None, ["s"]),
        Triage(Lane.HIGH_CONFIDENCE, 0.9, None, ["h"]),
    ]
    stats = summarize(triages)
    assert stats.review_first == 1
    assert stats.standard == 1
    assert stats.high_confidence == 1


def test_triage_reasons_populated():
    r = _rubric(open_ended=False)
    d = _draft(0.90)
    t = triage(d, r, "text", allow_vote=False)
    assert len(t.reasons) >= 1