"""Calibration health panel: Draft bias, override rate, per-criterion disagreement.

This is the data the teacher sees to know whether calibration is working, and
`Draft bias` is the part that matters: the signed gap between what the AI drafts
and what the teacher approves, measured over *every* Approval on the Assignment.

The population is the whole substance of the term (ADR-0001). Reading only the
overrides — items the teacher changed — inverts the metric under exactly the
failure it needs to catch: a teacher who has begun rubber-stamping low drafts
generates fewer overrides, so the panel reads better as automation bias gets
worse. An approval with no override is a complete recorded comparison and counts.

Bias is a fraction of criterion max, never a point count: minus 1 point means two
different things on a 5-point criterion and a 100-point one. Below `EVIDENCE_FLOOR`
approvals the panel reports counts and no direction, and no consumer may infer one.
QWK is not here — it rounds the half-points teachers actually grade in, and it
remains a release-gate metric only.

Also contains the cold-start rule: AI drafts unlock after the teacher grades
3 items (or imports past graded work). This makes calibration structural —
the teacher must demonstrate their standard before the AI drafts anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from grader.exemplars import ExemplarStore
from grader.overrides import OverrideStore
from grader.schema import Rubric


# ---------------------------------------------------------------------------
# Cold-start rule
# ---------------------------------------------------------------------------

COLD_START_MIN = 3  # AI drafts unlock after the teacher grades 3 items (PRD A3)

def can_draft(exemplar_store: ExemplarStore, assignment_id: str) -> bool:
    """Has the teacher graded enough items to unlock AI drafting?

    The cold-start rule (PRD: "Grade 3 yourself to unlock AI drafts") makes calibration
    structural rather than optional. The AI needs anchor exemplars to calibrate; without
    them it grades like a generic model, not like this teacher.
    """
    return exemplar_store.count(assignment_id) >= COLD_START_MIN


def cold_start_status(exemplar_store: ExemplarStore, assignment_id: str) -> dict:
    """Human-readable cold-start state for the UI."""
    count = exemplar_store.count(assignment_id)
    remaining = max(0, COLD_START_MIN - count)
    return {
        "unlocked": count >= COLD_START_MIN,
        "exemplar_count": count,
        "remaining": remaining,
        "message": (
            f"AI drafts unlocked — {count} exemplars calibrated."
            if count >= COLD_START_MIN
            else f"Grade {remaining} more item{'s' if remaining != 1 else ''} to unlock AI drafts ({count}/{COLD_START_MIN})."
        ),
    }


# ---------------------------------------------------------------------------
# Draft bias: the thresholds and the evidence floor (ADR-0001)
# ---------------------------------------------------------------------------

# Fractions of criterion max, symmetric in both directions. Disclose is set below
# demote on purpose: Grader tells the teacher about a gap before it restructures
# their queue over it.
DISCLOSE_THRESHOLD = 0.03
DEMOTE_THRESHOLD = 0.10

# Approvals needed before the panel names a direction at all. Below it there are
# counts and nothing else — three data points do not make a confident story.
EVIDENCE_FLOOR = 20

# The most recent approvals the figure is measured over, so that a teacher who
# recalibrates mid-assignment is not held to grading they have moved on from.
# It also caps the `n` the panel reports.
ROLLING_WINDOW = 200


@dataclass(frozen=True)
class ApprovedComparison:
    """One Approval, as the AI-vs-teacher comparison it already is.

    `teacher_scores` is None when the teacher approved the draft unchanged; that
    is a zero-delta comparison, not a missing one.

    `rubric` is the one the grade was actually produced against, already resolved
    by whoever read the record. This module does not know how records are stored
    or how a Rubric is found from one.
    """

    ai_scores: dict[str, float]
    teacher_scores: dict[str, float] | None
    rubric: Rubric

    def scaled_deltas(self) -> list[tuple[str, float]]:
        """(criterion_id, AI minus teacher as a fraction of that criterion's max).

        Negative means the draft ran below the teacher. A criterion the draft did
        not score contributes nothing; one the teacher left alone contributes a
        zero, which is the data point the override-only population was missing.
        """
        approved = self.teacher_scores or self.ai_scores
        deltas: list[tuple[str, float]] = []
        for criterion in self.rubric.criteria:
            if criterion.id not in self.ai_scores or criterion.max_points <= 0:
                continue
            ai = self.ai_scores[criterion.id]
            teacher = approved.get(criterion.id, ai)
            deltas.append((criterion.id, (ai - teacher) / criterion.max_points))
        return deltas


class ApprovedComparisons(Protocol):
    """The approved comparisons for one Assignment, newest first.

    Deliberately the narrowest thing `compute_health` can be given: no writes, no
    grade ids, no rows. Rows this reader cannot turn into a comparison — no AI
    scores, or no resolvable Rubric — are left out rather than returned empty,
    since a comparison that cannot be scaled is not evidence about anything.
    """

    def list(
        self, assignment_id: str, *, limit: int = ROLLING_WINDOW
    ) -> list[ApprovedComparison]: ...


# ---------------------------------------------------------------------------
# Calibration health panel
# ---------------------------------------------------------------------------

@dataclass
class CalibrationHealth:
    """The data behind the calibration health dashboard (PRD FR-5.4)."""
    # Draft bias: signed, scaled as a fraction of criterion max. Negative means
    # drafts run below the teacher. None below the evidence floor — the counts
    # are still reported there, the direction is not.
    draft_bias: float | None = None
    # Approvals the figure rests on: the comparisons the reader could actually
    # make, capped at ROLLING_WINDOW. An approval whose Rubric no longer resolves
    # is not one of them — it can be counted but not scaled.
    n: int = 0
    discloses: bool = False           # clears DISCLOSE_THRESHOLD
    demotes: bool = False             # clears DEMOTE_THRESHOLD

    # Override rate. A different population on purpose: an Override is a
    # teacher's correction, and the rate counts corrections.
    override_count: int = 0
    total_graded: int = 0
    override_rate: float = 0.0

    # Per-criterion disagreement, over the same approvals as draft_bias and in
    # the same convention: signed AI-minus-teacher, scaled by that criterion's max.
    per_criterion: dict[str, dict[str, float]] = field(default_factory=dict)
    # {criterion_id: {"bias": signed, "abs_bias": float, "count": int}}

    # Exemplar store size
    exemplar_count: int = 0

    def __str__(self) -> str:
        lines = ["Calibration Health"]
        if self.draft_bias is None:
            lines.append(f"  Draft bias: not yet — {self.n}/{EVIDENCE_FLOOR} approvals")
        elif not self.discloses:
            lines.append(f"  Draft bias: {self.draft_bias:+.1%} of max — "
                         f"too small to mention (n={self.n})")
        else:
            direction = "below" if self.draft_bias < 0 else "above"
            lines.append(f"  Draft bias: {self.draft_bias:+.1%} of max "
                         f"(drafts run {direction} you, n={self.n})")
        lines.append(f"  Overrides: {self.override_count}/{self.total_graded} "
                     f"({self.override_rate:.0%})")
        lines.append(f"  Exemplars: {self.exemplar_count}")
        if self.per_criterion:
            lines.append("  Per-criterion disagreement:")
            for cid, stats in sorted(self.per_criterion.items()):
                lines.append(f"    {cid}: {stats['bias']:+.1%} "
                             f"|{stats['abs_bias']:.1%}| (n={stats['count']:.0f})")
        return "\n".join(lines)



def compute_health(
    approved_comparisons: ApprovedComparisons,
    override_store: OverrideStore,
    exemplar_store: ExemplarStore,
    assignment_id: str,
) -> CalibrationHealth:
    """Compute the calibration health panel for one Assignment.

    `Draft bias` and per-criterion disagreement come from every Approval on the
    Assignment, the zero-delta ones included. `Override` rate and `Exemplar`
    count keep their own stores and their own meaning.
    """
    health = CalibrationHealth()

    health.exemplar_count = exemplar_store.count(assignment_id)
    health.override_count = override_store.count(assignment_id)
    health.total_graded = health.exemplar_count
    if health.total_graded > 0:
        health.override_rate = health.override_count / health.total_graded

    approvals = approved_comparisons.list(assignment_id, limit=ROLLING_WINDOW)
    health.n = len(approvals)
    if health.n < EVIDENCE_FLOOR:
        return health

    # One approval, one data point — the same population `n` reports and the floor
    # counts. Averaging the criterion deltas directly would let a draft that scored
    # four criteria outweigh one that scored two.
    per_approval: list[float] = []
    per_criterion: dict[str, list[float]] = {}
    for approval in approvals:
        deltas = approval.scaled_deltas()
        if not deltas:
            continue
        per_approval.append(sum(delta for _, delta in deltas) / len(deltas))
        for criterion_id, delta in deltas:
            per_criterion.setdefault(criterion_id, []).append(delta)

    if not per_approval:
        return health

    health.draft_bias = sum(per_approval) / len(per_approval)
    health.discloses = abs(health.draft_bias) >= DISCLOSE_THRESHOLD
    health.demotes = abs(health.draft_bias) >= DEMOTE_THRESHOLD
    health.per_criterion = {
        cid: {
            "bias": sum(deltas) / len(deltas),
            "abs_bias": sum(abs(d) for d in deltas) / len(deltas),
            "count": len(deltas),
        }
        for cid, deltas in per_criterion.items()
    }
    return health
