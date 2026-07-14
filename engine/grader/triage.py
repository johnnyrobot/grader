"""Review triage: decide which drafts a human must look at.

This is where the time saving actually comes from. Grading 30 papers should feel like
grading 8: the AI is trusted where it's reliable, and the teacher's attention is spent where
it isn't. Getting this wrong in the safe direction (too many reviews) wastes time; getting
it wrong in the unsafe direction (auto-clearing a bad grade) damages a student.

Two uncertainty signals, because the literature disagrees on which is better:
  * self-reported confidence — better calibrated (ECE 0.166 vs 0.229) and ~5x cheaper
    (arXiv 2603.29559)
  * self-consistency across samples — validated by SURE (MDPI 2504-4990/8/3/74), where
    regrading only the flagged items cut manual grading 40-90%

We use confidence on every item (free — it comes with the draft) and escalate to a sampled
consistency vote ONLY when confidence is low. Both signals bad => top of the queue. This
takes the cheap signal's calibration and the expensive signal's independence, and pays for
the expensive one only on the items that need it.

Criterion type also drives routing: open-ended prose carries documented style bias that
survives explicit instructions to ignore it (arXiv 2603.18765), so essays get less benefit
of the doubt than math.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum

from grader.agent import GradeRun, grade
from grader.schema import CriterionType, Flag, GradeDraft, Rubric


class Lane(str, Enum):
    REVIEW_FIRST = "review_first"      # AI is unsure, or the work is risky: teacher reads first
    STANDARD = "standard"              # normal review
    HIGH_CONFIDENCE = "high_confidence"  # spot-check a sample; accept the rest


# Below this, we don't trust the draft without a second look.
LOW_CONFIDENCE = 0.70
HIGH_CONFIDENCE = 0.85
# Points of disagreement across resamples (as a fraction of total) that means "unstable".
UNSTABLE_SPREAD = 0.15


@dataclass
class Triage:
    lane: Lane
    confidence: float
    consistency: float | None      # None = no vote was needed
    reasons: list[str] = field(default_factory=list)
    votes: list[float] = field(default_factory=list)

    def __str__(self) -> str:
        c = f" consistency={self.consistency:.2f}" if self.consistency is not None else ""
        return f"{self.lane.value:16s} conf={self.confidence:.2f}{c}  {'; '.join(self.reasons)}"


def _has_open_ended(rubric: Rubric) -> bool:
    return any(c.criterion_type == CriterionType.OPEN_ENDED for c in rubric.criteria)


def triage(
    draft: GradeDraft,
    rubric: Rubric,
    submission: str,
    *,
    model_name: str = "grader-text:8b",
    consistency_samples: int = 3,
    anchors: str = "",
    materials: str = "",
    allow_vote: bool = True,
) -> Triage:
    """Assign a review lane. Escalates to a consistency vote only when it's worth paying for."""
    reasons: list[str] = []
    conf = draft.confidence

    # Hard flags bypass all scoring logic — these are always human business.
    blocking = {Flag.NEEDS_HUMAN, Flag.UNREADABLE, Flag.POSSIBLE_MISCONDUCT, Flag.OFF_TOPIC}
    if hit := blocking.intersection(draft.flags):
        return Triage(Lane.REVIEW_FIRST, conf, None,
                      [f"flagged: {', '.join(sorted(f.value for f in hit))}"])

    if Flag.CITATION_ISSUES in draft.flags:
        reasons.append("citation issues found")

    # Cheap signal first.
    consistency: float | None = None
    votes: list[float] = []

    if conf < LOW_CONFIDENCE and allow_vote:
        # Expensive signal, bought only where it matters. Resampling at temperature 0 still
        # varies because the retrieved anchors and decode path aren't perfectly fixed; what
        # we're measuring is whether the grade is stable under re-derivation.
        reasons.append(f"low confidence ({conf:.2f}) -> consistency vote")
        votes = [draft.total]
        for _ in range(consistency_samples - 1):
            run: GradeRun = grade(rubric, submission, model_name=model_name, mode="tool",
                                  anchors=anchors, materials=materials)
            if run.ok:
                votes.append(run.draft.total)

        if len(votes) > 1:
            spread = (max(votes) - min(votes)) / (rubric.total_points or 1)
            consistency = 1.0 - min(1.0, spread / UNSTABLE_SPREAD) if UNSTABLE_SPREAD else 1.0
            if spread > UNSTABLE_SPREAD:
                reasons.append(f"unstable across resamples (spread {spread:.0%})")
            else:
                reasons.append(f"stable across resamples (spread {spread:.0%})")

    # Route.
    if conf < LOW_CONFIDENCE and (consistency is None or consistency < 0.5):
        lane = Lane.REVIEW_FIRST
        reasons.append("low confidence and unstable")
    elif conf < LOW_CONFIDENCE:
        lane = Lane.STANDARD
        reasons.append("low confidence but the score held up")
    # Citation findings are advisory and already appear in the teacher-facing reasons. They do not
    # by themselves make an otherwise confident analytic grade uncertain. Other non-hard flags
    # (for example LOW_CONFIDENCE) still prevent auto-clearing when they contradict the score.
    elif conf >= HIGH_CONFIDENCE and set(draft.flags).issubset({Flag.CITATION_ISSUES}):
        lane = Lane.HIGH_CONFIDENCE
        # Style bias on prose is real and prompt-resistant, so prose never fully auto-clears.
        if _has_open_ended(rubric):
            lane = Lane.STANDARD
            reasons.append("open-ended criteria: style bias risk, not auto-cleared")
    else:
        lane = Lane.STANDARD

    if not reasons:
        reasons.append("confident, no flags")
    return Triage(lane, conf, consistency, reasons, votes)


@dataclass
class QueueStats:
    review_first: int = 0
    standard: int = 0
    high_confidence: int = 0

    @property
    def total(self) -> int:
        return self.review_first + self.standard + self.high_confidence

    def summary(self, spot_check_rate: float = 0.2) -> str:
        """What the teacher actually cares about: how many papers must I really read?"""
        if not self.total:
            return "empty queue"
        must_read = self.review_first + self.standard
        spot = round(self.high_confidence * spot_check_rate)
        effective = must_read + spot
        saved = 1 - effective / self.total
        return (
            f"{self.total} submissions -> read {must_read} "
            f"+ spot-check {spot} of {self.high_confidence} high-confidence "
            f"= {effective} ({saved:.0%} less reading)"
        )


def summarize(triages: list[Triage]) -> QueueStats:
    s = QueueStats()
    for t in triages:
        if t.lane == Lane.REVIEW_FIRST:
            s.review_first += 1
        elif t.lane == Lane.STANDARD:
            s.standard += 1
        else:
            s.high_confidence += 1
    return s


def median_or_none(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None
