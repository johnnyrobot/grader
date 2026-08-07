"""Rubric ingestion: paste/build, import from Canvas, versioning.

The rubric is the grading contract (schema.py). This module handles getting rubrics
INTO the system from external sources: pasted text, structured input, or Canvas
(via canvas-pp-cli's `rubrics show`).

Rubric versions are immutable once created — every grade references its rubric version
so the audit trail can reconstruct what the model was told at grading time.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from typing import Any

from grader.schema import CriterionType, Rubric, RubricCriterion, RubricLevel

from grader.db import Database


# ---------------------------------------------------------------------------
# Versioning store
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS rubrics (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    canvas_id   TEXT,
    rubric_json TEXT NOT NULL,         -- full serialized Rubric
    created_at  TEXT NOT NULL,
    UNIQUE(title, version)
);
"""

# Valid criterion types by name (case-insensitive)
_CRITERION_TYPE_MAP = {
    "objective": CriterionType.OBJECTIVE,
    "analytic": CriterionType.ANALYTIC,
    "open_ended": CriterionType.OPEN_ENDED,
    "open-ended": CriterionType.OPEN_ENDED,
    "open ended": CriterionType.OPEN_ENDED,
    "essay": CriterionType.OPEN_ENDED,
    "holistic": CriterionType.OPEN_ENDED,
    "math": CriterionType.OBJECTIVE,
    "code": CriterionType.OBJECTIVE,
    "factual": CriterionType.OBJECTIVE,
}


def _parse_criterion_type(s: str) -> CriterionType:
    return _CRITERION_TYPE_MAP.get(s.strip().lower(), CriterionType.ANALYTIC)


class RubricStore:
    """Versioned rubric store. Versions are immutable once created."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.executescript(SCHEMA)

    def save(self, rubric: Rubric, *, canvas_id: str | None = None) -> int:
        """Save a new version of a rubric (or the first version). Returns the row id."""
        existing = self.db.execute(
            "SELECT MAX(version) as v FROM rubrics WHERE title = ?", (rubric.title,)
        ).fetchone()
        version = (existing["v"] or 0) + 1 if existing["v"] else 1
        cur = self.db.execute(
            "INSERT INTO rubrics (title, version, canvas_id, rubric_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (rubric.title, version, canvas_id, rubric.model_dump_json(),
             datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get(self, title: str, *, version: int | None = None) -> Rubric | None:
        """Get a rubric by title. If version is None, returns the latest."""
        if version is not None:
            row = self.db.execute(
                "SELECT * FROM rubrics WHERE title = ? AND version = ?",
                (title, version),
            ).fetchone()
        else:
            row = self.db.execute(
                "SELECT * FROM rubrics WHERE title = ? ORDER BY version DESC LIMIT 1",
                (title,),
            ).fetchone()
        if not row:
            return None
        return Rubric.model_validate_json(row["rubric_json"])

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT title, MAX(version) as version, canvas_id, created_at "
            "FROM rubrics GROUP BY title ORDER BY title"
        ).fetchall()
        return [{"title": r["title"], "version": r["version"],
                 "canvas_id": r["canvas_id"], "created_at": r["created_at"]}
                for r in rows]


# ---------------------------------------------------------------------------
# Pasted-text parser: turns a markdown/table/text rubric into a Rubric
# ---------------------------------------------------------------------------

def parse_pasted_rubric(text: str, *, title: str | None = None) -> Rubric:
    """Parse a pasted rubric from common formats: markdown tables, bullet lists, or plain text.

    Accepts:
    - Markdown table with headers: Criterion | Max | Type | Levels
    - Bullet-list criteria with scored levels
    - Plain text with criterion headers (## or #) and level descriptions

    This is intentionally forgiving — a teacher pasting from Word, a Canvas rubric
    page, or a syllabus should get something usable without formatting lectures.
    """
    lines = [line.rstrip() for line in text.strip().splitlines() if line.strip()]

    # Detect title from first heading or first non-table line
    if title is None:
        if lines[0].startswith("#"):
            title = lines[0].lstrip("#").strip()
            lines = lines[1:]
        else:
            title = "Pasted Rubric"

    # Try markdown table format first
    if "|" in lines[0] and "---" in lines[1] if len(lines) > 1 else False:
        return _parse_table_rubric(lines, title)

    # Try bullet/heading format
    return _parse_text_rubric(lines, title)


def _parse_table_rubric(lines: list[str], title: str) -> Rubric:
    """Parse a markdown table: Criterion | Max | Type | Level descriptions..."""
    criteria: list[RubricCriterion] = []

    for line in lines[2:]:  # skip header + separator
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if not cols or not cols[0]:
            continue
        name = cols[0]
        max_pts = float(cols[1]) if len(cols) > 1 and cols[1] else 5.0
        ctype = _parse_criterion_type(cols[2]) if len(cols) > 2 else CriterionType.ANALYTIC
        levels = _parse_levels_from_text(cols[3]) if len(cols) > 3 else []

        cid = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "criterion"
        criteria.append(RubricCriterion(
            id=cid, title=name, max_points=max_pts,
            criterion_type=ctype, levels=levels,
        ))

    return Rubric(title=title, criteria=criteria)


def _parse_text_rubric(lines: list[str], title: str) -> Rubric:
    """Parse heading/bullet format:

    ## Criterion Name (5 pts, analytic)
    - 5: Excellent — descriptor text
    - 4: Good — descriptor text
    - 3: Adequate — descriptor text
    """
    criteria: list[RubricCriterion] = []
    current: dict[str, Any] | None = None
    levels: list[RubricLevel] = []

    def _flush() -> None:
        nonlocal levels
        if current is not None:
            current["levels"] = levels
            criteria.append(RubricCriterion(**current))
        levels = []
        current_ref[0] = None

    current_ref: list[dict | None] = [None]

    for line in lines:
        if line.startswith("## ") or line.startswith("# "):
            _flush()
            header_text = line.lstrip("#").strip()
            # Parse "Criterion Name (5 pts, analytic)" or "Criterion Name"
            max_pts = 5.0
            ctype = CriterionType.ANALYTIC
            match = re.match(r"^(.+?)\s*(?:\((\d+(?:\.\d+)?)\s*pts?[,)](.*?))?\)?$", header_text)
            if match:
                name = match.group(1).strip()
                if match.group(2):
                    max_pts = float(match.group(2))
                if match.group(3):
                    ctype = _parse_criterion_type(match.group(3).strip().rstrip(")"))
            else:
                name = header_text
            cid = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "criterion"
            current = {"id": cid, "title": name, "max_points": max_pts,
                       "criterion_type": ctype, "description": ""}
            current_ref[0] = current
        elif line.startswith("- ") and current_ref[0] is not None:
            level = _parse_level_line(line[2:])
            if level:
                levels.append(level)

    _flush()
    if not criteria:
        # Fallback: single criterion from the whole text
        criteria.append(RubricCriterion(
            id="overall", title="Overall", max_points=5.0,
            criterion_type=CriterionType.ANALYTIC,
        ))
    return Rubric(title=title, criteria=criteria)


def _parse_level_line(text: str) -> RubricLevel | None:
    """Parse '5: Excellent — descriptor' or '5 - Excellent: descriptor'."""
    # Try "score: label — descriptor" or "score - label: descriptor"
    match = re.match(r"^(\d+(?:\.\d+)?)\s*[:\-]\s*(.+?)(?:\s*[—–-]\s*(.+))?$", text.strip())
    if not match:
        return None
    score = float(match.group(1))
    label = match.group(2).strip()
    descriptor = match.group(3).strip() if match.group(3) else label
    return RubricLevel(score=score, label=label, descriptor=descriptor)


def _parse_levels_from_text(text: str) -> list[RubricLevel]:
    """Parse level descriptions from a table cell or free text."""
    levels: list[RubricLevel] = []
    for part in re.split(r"[;|]", text):
        level = _parse_level_line(part.strip())
        if level:
            levels.append(level)
    return levels


# ---------------------------------------------------------------------------
# Canvas import (via canvas-pp-cli subprocess)
# ---------------------------------------------------------------------------

def import_from_canvas(
    course_id: int | str,
    assignment_id: int | str,
    *,
    cli_path: str = "canvas-pp-cli",
) -> Rubric:
    """Import a rubric from Canvas via canvas-pp-cli's `rubrics show` command.

    canvas-pp-cli returns JSON in {meta, results} envelope (reads use {results},
    NOT {data} — gotcha #2 in docs/phases/P3.md). The rubric payload contains
    criteria with ratings (Canvas calls score levels "ratings").
    """
    import subprocess

    result = subprocess.run(
        [cli_path, "rubrics", "show", str(course_id), str(assignment_id), "--agent"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"canvas-pp-cli failed: {result.stderr[:200]}")

    envelope = json.loads(result.stdout)
    # Reads return {meta, results}, not {data}
    data = envelope.get("results") or envelope.get("data") or envelope
    if isinstance(data, list) and data:
        data = data[0]

    title = data.get("title", f"Canvas Rubric (course {course_id})")
    criteria: list[RubricCriterion] = []

    for i, crit in enumerate(data.get("criteria", [])):
        cid = crit.get("id", f"criterion_{i}")
        # Canvas rubric criterion ids are often long strings; shorten
        cid = re.sub(r"[^a-z0-9]+", "_", str(cid).lower()[:30]).strip("_") or f"c{i}"
        description = crit.get("description", crit.get("long_description", ""))
        max_pts = float(crit.get("points", 5.0))
        ctype = CriterionType.ANALYTIC

        levels: list[RubricLevel] = []
        for rating in crit.get("ratings", []):
            levels.append(RubricLevel(
                score=float(rating.get("points", 0)),
                label=rating.get("description", ""),
                descriptor=rating.get("long_description", rating.get("description", "")),
            ))
        if not levels:
            levels = [
                RubricLevel(score=max_pts, label="Full marks", descriptor="Meets all expectations."),
                RubricLevel(score=0, label="No marks", descriptor="Does not meet expectations."),
            ]

        criteria.append(RubricCriterion(
            id=cid, title=crit.get("description", f"Criterion {i+1}")[:80],
            description=description, max_points=max_pts,
            criterion_type=ctype, levels=levels,
        ))

    return Rubric(title=title, criteria=criteria)


# ---------------------------------------------------------------------------
# Rubric versioning: create a new version with amended text
# ---------------------------------------------------------------------------

def amend_criterion(
    rubric: Rubric,
    criterion_id: str,
    *,
    new_description: str | None = None,
    new_max_points: float | None = None,
    new_levels: list[RubricLevel] | None = None,
) -> Rubric:
    """Create a new rubric version with one criterion amended.

    This is the teacher's action after accepting/editing a RefinementProposal.
    The original rubric version is preserved (immutable); the new version is stored
    and used for subsequent grades.
    """
    new_criteria = []
    for c in rubric.criteria:
        if c.id == criterion_id:
            new_criteria.append(RubricCriterion(
                id=c.id,
                title=c.title,
                description=new_description or c.description,
                max_points=new_max_points if new_max_points is not None else c.max_points,
                criterion_type=c.criterion_type,
                levels=new_levels or c.levels,
            ))
        else:
            new_criteria.append(c.model_copy())
    return Rubric(title=rubric.title, criteria=new_criteria)