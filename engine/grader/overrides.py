"""Override -> exemplar loop: the teacher's correction becomes an anchor immediately.

This is the "learn from my overrides" mechanism (PRD A4). When a teacher changes an AI
draft score, the corrected grade becomes a new exemplar in the store with source="override",
weighted above imported exemplars in retrieval. After >=3 overrides sharing a pattern,
a rubric-refinement proposal is generated (Reflect-and-Revise).

The loop is:
  1. Teacher overrides an AI draft (changes score(s) and/or feedback)
  2. The corrected grade is immediately embedded and added to the exemplar store
  3. The override is recorded for pattern detection
  4. If >=3 overrides cluster on the same criterion in the same direction, a rubric
     amendment proposal is surfaced for the teacher to accept/edit/reject
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from grader.exemplars import Exemplar, ExemplarStore
from grader.schema import GradeDraft


SCHEMA = """
CREATE TABLE IF NOT EXISTS overrides (
    id              INTEGER PRIMARY KEY,
    assignment_id   TEXT NOT NULL,
    submission_text TEXT NOT NULL,
    ai_scores_json  TEXT NOT NULL,      -- {criterion_id: score} from the AI draft
    teacher_scores_json TEXT NOT NULL,  -- {criterion_id: score} after correction
    ai_total        REAL NOT NULL,
    teacher_total   REAL NOT NULL,
    max_total       REAL NOT NULL,
    criterion_delta_json TEXT NOT NULL, -- {criterion_id: teacher - ai} per criterion
    ai_feedback     TEXT DEFAULT '',
    teacher_feedback TEXT DEFAULT '',
    rubric_version  TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ov_assignment ON overrides(assignment_id);
CREATE INDEX IF NOT EXISTS idx_ov_created ON overrides(created_at);
"""


@dataclass
class Override:
    """A teacher's correction of an AI draft."""
    id: int | None
    assignment_id: str
    submission_text: str
    ai_scores: dict[str, float]
    teacher_scores: dict[str, float]
    ai_total: float
    teacher_total: float
    max_total: float
    criterion_delta: dict[str, float]   # teacher - ai per criterion
    ai_feedback: str = ""
    teacher_feedback: str = ""
    rubric_version: str = "1"
    created_at: str = ""

    @property
    def is_raised(self) -> bool:
        """Teacher gave a higher score than the AI."""
        return self.teacher_total > self.ai_total

    @property
    def is_lowered(self) -> bool:
        """Teacher gave a lower score than the AI."""
        return self.teacher_total < self.ai_total


class OverrideStore:
    """SQLite-backed override history with pattern detection for rubric refinement."""

    def __init__(self, path: Path | str = "fixtures/overrides.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        # Shared across the request threadpool and the grading thread; see exemplars.py.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def record(
        self,
        *,
        assignment_id: str,
        submission_text: str,
        ai_draft: GradeDraft,
        teacher_scores: dict[str, float],
        teacher_feedback: str,
        max_total: float,
        rubric_version: str = "1",
    ) -> Override:
        """Record a teacher override and return the structured record."""
        ai_scores = {cs.criterion_id: cs.score for cs in ai_draft.criterion_scores}
        delta = {cid: teacher_scores.get(cid, 0) - ai_scores.get(cid, 0)
                 for cid in set(ai_scores) | set(teacher_scores)}

        ov = Override(
            id=None,
            assignment_id=assignment_id,
            submission_text=submission_text,
            ai_scores=ai_scores,
            teacher_scores=teacher_scores,
            ai_total=sum(ai_scores.values()),
            teacher_total=sum(teacher_scores.values()),
            max_total=max_total,
            criterion_delta=delta,
            ai_feedback=ai_draft.feedback,
            teacher_feedback=teacher_feedback,
            rubric_version=rubric_version,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        cur = self.db.execute(
            "INSERT INTO overrides (assignment_id, submission_text, ai_scores_json, "
            "teacher_scores_json, ai_total, teacher_total, max_total, criterion_delta_json, "
            "ai_feedback, teacher_feedback, rubric_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ov.assignment_id, ov.submission_text,
                json.dumps(ov.ai_scores), json.dumps(ov.teacher_scores),
                ov.ai_total, ov.teacher_total, ov.max_total,
                json.dumps(ov.criterion_delta),
                ov.ai_feedback, ov.teacher_feedback,
                ov.rubric_version, ov.created_at,
            ),
        )
        self.db.commit()
        ov.id = cur.lastrowid
        return ov

    def _row_to_override(self, r: sqlite3.Row) -> Override:
        return Override(
            id=r["id"], assignment_id=r["assignment_id"],
            submission_text=r["submission_text"],
            ai_scores=json.loads(r["ai_scores_json"]),
            teacher_scores=json.loads(r["teacher_scores_json"]),
            ai_total=r["ai_total"], teacher_total=r["teacher_total"],
            max_total=r["max_total"],
            criterion_delta=json.loads(r["criterion_delta_json"]),
            ai_feedback=r["ai_feedback"] or "",
            teacher_feedback=r["teacher_feedback"] or "",
            rubric_version=r["rubric_version"],
            created_at=r["created_at"],
        )

    def list(self, assignment_id: str, *, limit: int = 100) -> list[Override]:
        rows = self.db.execute(
            "SELECT * FROM overrides WHERE assignment_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (assignment_id, limit),
        ).fetchall()
        return [self._row_to_override(r) for r in rows]

    def count(self, assignment_id: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM overrides WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Override -> exemplar: the teacher's correction becomes an anchor immediately.
# ---------------------------------------------------------------------------

def override_to_exemplar(
    ov: Override,
    assignment_id: str,
    max_total: float,
) -> Exemplar:
    """Convert an override into an exemplar for the store.

    source="override" so retrieval can weight it above imports.
    """
    return Exemplar(
        id=None,
        assignment_id=assignment_id,
        text=ov.submission_text,
        total=ov.teacher_total,
        max_total=max_total,
        scores=ov.teacher_scores,
        comment=ov.teacher_feedback,
        source="override",
    )


def apply_override(
    override_store: OverrideStore,
    exemplar_store: ExemplarStore,
    *,
    assignment_id: str,
    submission_text: str,
    ai_draft: GradeDraft,
    teacher_scores: dict[str, float],
    teacher_feedback: str,
    max_total: float,
    rubric_version: str = "1",
) -> Override:
    """Full override loop: record the correction, add it as an exemplar, return the override.

    This is the single function the UI/API calls when a teacher changes a score.
    The corrected grade is immediately in the exemplar store and will be retrieved
    as an anchor for the next submission — calibration updates instantly.
    """
    ov = override_store.record(
        assignment_id=assignment_id,
        submission_text=submission_text,
        ai_draft=ai_draft,
        teacher_scores=teacher_scores,
        teacher_feedback=teacher_feedback,
        max_total=max_total,
        rubric_version=rubric_version,
    )
    ex = override_to_exemplar(ov, assignment_id, max_total)
    exemplar_store.add(ex)
    return ov


# ---------------------------------------------------------------------------
# Pattern detection -> rubric refinement proposals (Reflect-and-Revise)
# ---------------------------------------------------------------------------

@dataclass
class RefinementProposal:
    """A proposed rubric-text amendment, surfaced for teacher approval."""
    criterion_id: str
    direction: str          # "raising" | "lowering"
    pattern_count: int       # how many overrides cluster this way
    avg_delta: float         # mean score change
    evidence: list[str]      # excerpts from the overriding submissions
    suggested_text: str     # proposed amendment to the criterion descriptor
    status: str = "pending"  # pending | accepted | edited | rejected


def detect_patterns(
    overrides: list[Override],
    *,
    min_pattern: int = 3,
    evidence_excerpts: int = 3,
) -> list[RefinementProposal]:
    """Find criteria where >=min_pattern overrides cluster in the same direction.

    This is the Reflect-and-Revise trigger: if the teacher consistently raises or
    lowers a criterion the AI got wrong, the rubric text itself may need amendment
    so the AI does better next time — but the teacher decides.
    """
    by_criterion: dict[str, list[Override]] = {}
    for ov in overrides:
        for cid, delta in ov.criterion_delta.items():
            if abs(delta) < 0.01:
                continue
            by_criterion.setdefault(cid, []).append(ov)

    proposals: list[RefinementProposal] = []
    for cid, ovs in by_criterion.items():
        deltas = [o.criterion_delta[cid] for o in ovs]
        raised = sum(1 for d in deltas if d > 0)
        lowered = sum(1 for d in deltas if d < 0)

        for direction, count, sign in (("raising", raised, +1), ("lowering", lowered, -1)):
            if count < min_pattern:
                continue
            matching = [o for o in ovs if sign * o.criterion_delta[cid] > 0]
            avg = sum(o.criterion_delta[cid] for o in matching) / len(matching)
            evidence = [o.submission_text[:120] for o in matching[:evidence_excerpts]]

            if direction == "raising":
                suggested = (
                    f"Amend [{cid}] to clarify that work meeting this standard may be stronger "
                    f"than the current descriptor suggests — the teacher consistently scores "
                    f"higher ({avg:+.1f} pts on {count} overrides). Consider adding a higher "
                    f"anchor level or loosening the top descriptor."
                )
            else:
                suggested = (
                    f"Amend [{cid}] to clarify that work at this level should be scored lower "
                    f"— the teacher consistently scores lower ({avg:+.1f} pts on {count} "
                    f"overrides). Consider tightening the descriptor or adding a lower anchor."
                )

            proposals.append(RefinementProposal(
                criterion_id=cid,
                direction=direction,
                pattern_count=count,
                avg_delta=avg,
                evidence=evidence,
                suggested_text=suggested,
            ))

    return proposals