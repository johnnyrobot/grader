"""Calibration health panel: rolling QWK, override rate, per-criterion disagreement.

This is the data the teacher sees to know whether calibration is working. It rolls
up the override history and the AI-vs-teacher agreement into a living dashboard.

Also contains the cold-start rule: AI drafts unlock after the teacher grades
3 items (or imports past graded work). This makes calibration structural —
the teacher must demonstrate their standard before the AI drafts anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from grader.metrics import agreement
from grader.overrides import OverrideStore
from grader.exemplars import ExemplarStore


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
# Calibration health panel
# ---------------------------------------------------------------------------

@dataclass
class CalibrationHealth:
    """The data behind the calibration health dashboard (PRD FR-5.4)."""
    # Rolling agreement
    qwk: float | None = None          # AI-draft vs teacher-final, rolling window
    exact: float | None = None
    adjacent: float | None = None
    mean_error: float | None = None    # signed: + = AI lenient, - = AI harsh
    mean_abs_error: float | None = None
    n: int = 0

    # Override rate
    override_count: int = 0
    total_graded: int = 0
    override_rate: float = 0.0

    # Per-criterion disagreement
    per_criterion: dict[str, dict[str, float]] = field(default_factory=dict)
    # {criterion_id: {"mean_delta": signed, "abs_delta": float, "count": int}}

    # Exemplar store size
    exemplar_count: int = 0

    # Trend (last N vs previous N)
    qwk_trend: float | None = None    # current window QWK - previous window QWK

    def __str__(self) -> str:
        lines = ["Calibration Health"]
        if self.qwk is not None:
            bias = ("lenient" if self.mean_error and self.mean_error > 0.15
                    else "harsh" if self.mean_error and self.mean_error < -0.15
                    else "neutral")
            lines.append(f"  QWK: {self.qwk:.3f} (bias: {bias})")
        if self.qwk_trend is not None:
            arrow = "↑" if self.qwk_trend > 0.01 else "↓" if self.qwk_trend < -0.01 else "→"
            lines.append(f"  Trend: {arrow} {self.qwk_trend:+.3f}")
        lines.append(f"  Overrides: {self.override_count}/{self.total_graded} "
                     f"({self.override_rate:.0%})")
        lines.append(f"  Exemplars: {self.exemplar_count}")
        if self.per_criterion:
            lines.append("  Per-criterion disagreement:")
            for cid, stats in sorted(self.per_criterion.items()):
                lines.append(f"    {cid}: Δ={stats['mean_delta']:+.2f} "
                             f"|Δ|={stats['abs_delta']:.2f} (n={stats['count']})")
        return "\n".join(lines)


def compute_health(
    override_store: OverrideStore,
    exemplar_store: ExemplarStore,
    assignment_id: str,
    *,
    window: int = 20,
) -> CalibrationHealth:
    """Compute the calibration health panel from override + exemplar data.

    The QWK here is AI-draft vs teacher-final on overridden items — it measures
    where the teacher disagreed, which is where calibration matters most.
    """
    health = CalibrationHealth()
    overrides = override_store.list(assignment_id, limit=200)

    health.exemplar_count = exemplar_store.count(assignment_id)
    health.override_count = len(overrides)

    # QWK on overridden items (AI vs teacher)
    if overrides:
        ai_scores = [o.ai_total for o in overrides]
        teacher_scores = [o.teacher_total for o in overrides]
        agr = agreement(ai_scores, teacher_scores)
        health.qwk = agr.qwk
        health.exact = agr.exact
        health.adjacent = agr.adjacent
        health.mean_error = agr.mean_error
        health.mean_abs_error = agr.mean_abs_error
        health.n = agr.n

        # Per-criterion disagreement
        crit_data: dict[str, list[float]] = {}
        for ov in overrides:
            for cid, delta in ov.criterion_delta.items():
                crit_data.setdefault(cid, []).append(delta)
        for cid, deltas in crit_data.items():
            n = len(deltas)
            health.per_criterion[cid] = {
                "mean_delta": sum(deltas) / n,
                "abs_delta": sum(abs(d) for d in deltas) / n,
                "count": n,
            }

        # Trend: compare last `window` overrides to the previous `window`
        if len(overrides) >= window * 2:
            recent = overrides[:window]
            older = overrides[window : window * 2]
            recent_ai = [o.ai_total for o in recent]
            recent_t = [o.teacher_total for o in recent]
            older_ai = [o.ai_total for o in older]
            older_t = [o.teacher_total for o in older]
            recent_qwk = agreement(recent_ai, recent_t).qwk
            older_qwk = agreement(older_ai, older_t).qwk
            health.qwk_trend = recent_qwk - older_qwk

    health.total_graded = health.exemplar_count
    if health.total_graded > 0:
        health.override_rate = health.override_count / health.total_graded

    return health