"""What counts as a biased Assignment — the thresholds, and the verdicts on them.

`Draft bias` is measured in grader.calibration, over every Approval on an
Assignment (ADR-0001). It is *read* in two very different places: the panel the
teacher opens, and the lane triage assigns. Those two must never tell a teacher
different stories, so the rule lives here rather than in either of them, and
neither re-derives 0.10 for itself.

This module is deliberately a leaf: constants and pure predicates, no stores, no
schema, no imports of its own. Triage routes on this and must stay a pure
function of its inputs (ADR-0001) — it cannot afford to import the module that
knows how approvals are read.

Both thresholds are fractions of criterion max and symmetric in both directions.
A point of gap means two different things on a 5-point criterion and a 100-point
one, and a model drifting lenient inflates grades exactly as one drifting harsh
deflates them.
"""

from __future__ import annotations

# Disclose is set below demote on purpose: Grader tells the teacher about a gap
# before it restructures their queue over one.
DISCLOSE_THRESHOLD = 0.03
DEMOTE_THRESHOLD = 0.10

# Approvals needed before a direction is named at all. Below it there are counts
# and nothing else — three data points do not make a confident story. The floor
# is expressed downstream as the absence of a figure: `draft_bias` is None.
EVIDENCE_FLOOR = 20


def discloses_to_teacher(draft_bias: float | None) -> bool:
    """Is the gap worth mentioning where the teacher reads the score?"""
    return draft_bias is not None and abs(draft_bias) >= DISCLOSE_THRESHOLD


def demotes_high_confidence(draft_bias: float | None) -> bool:
    """Is this Assignment biased enough to stop calling its drafts high-confidence?

    None is the below-the-floor answer: not "no bias", but "no direction is
    knowable yet" — and nothing is acted on until one is.
    """
    return draft_bias is not None and abs(draft_bias) >= DEMOTE_THRESHOLD
