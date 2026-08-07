"""Getting a Rubric into the system, and keeping old versions readable.

parse_pasted_rubric is the first thing a teacher touches and had no tests at
all. It is deliberately forgiving — a paste from Word, a Canvas page or a
syllabus should produce something usable — so what matters is which shapes it
accepts and what it does with the parts it cannot read.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.db import Database  # noqa: E402  (needs the path insert above)
from grader.rubrics import (  # noqa: E402
    RubricStore,
    amend_criterion,
    import_from_canvas,
    parse_pasted_rubric,
)
from grader.schema import CriterionType, Rubric, RubricCriterion, RubricLevel  # noqa: E402


# ---------------------------------------------------------------------------
# Pasted markdown tables
# ---------------------------------------------------------------------------

TABLE = """\
# Essay Rubric
| Criterion | Max | Type | Levels |
|-----------|-----|------|--------|
| Thesis | 10 | analytic | 10: Excellent — clear and arguable; 5: Adequate — present but vague |
| Evidence | 5 | objective | 5: Cited — every claim sourced |
"""


def test_a_pasted_table_becomes_criteria():
    rubric = parse_pasted_rubric(TABLE)

    assert rubric.title == "Essay Rubric"
    assert [c.title for c in rubric.criteria] == ["Thesis", "Evidence"]
    assert [c.max_points for c in rubric.criteria] == [10.0, 5.0]


def test_table_criterion_types_are_read_by_name():
    rubric = parse_pasted_rubric(TABLE)

    assert rubric.criteria[0].criterion_type == CriterionType.ANALYTIC
    assert rubric.criteria[1].criterion_type == CriterionType.OBJECTIVE


def test_table_levels_are_split_on_semicolons():
    thesis = parse_pasted_rubric(TABLE).criteria[0]

    assert [level.score for level in thesis.levels] == [10.0, 5.0]
    assert thesis.levels[0].label == "Excellent"
    assert thesis.levels[0].descriptor == "clear and arguable"


def test_criterion_ids_are_derived_from_the_title():
    """Scores come back keyed by id, so the id has to be stable and safe."""
    rubric = parse_pasted_rubric("""\
| Criterion | Max |
|-----------|-----|
| Use of Evidence & Sources | 10 |
""")

    assert rubric.criteria[0].id == "use_of_evidence_sources"


def test_a_table_row_without_a_criterion_name_is_skipped():
    rubric = parse_pasted_rubric("""\
| Criterion | Max |
|-----------|-----|
| Thesis | 10 |
|  |  |
| Evidence | 5 |
""")

    assert [c.title for c in rubric.criteria] == ["Thesis", "Evidence"]


def test_a_table_column_left_off_falls_back_to_a_default():
    rubric = parse_pasted_rubric("""\
| Criterion |
|-----------|
| Thesis |
""")

    assert rubric.criteria[0].max_points == 5.0
    assert rubric.criteria[0].criterion_type == CriterionType.ANALYTIC


# ---------------------------------------------------------------------------
# Pasted headings and bullets
# ---------------------------------------------------------------------------

HEADINGS = """\
# Lab Report
## Method (10 pts, objective)
- 10: Reproducible — every step stated
- 5: Partial — a reader would have to guess
## Analysis (20 pts, open_ended)
- 20: Insightful
"""


def test_headings_and_bullets_become_criteria_with_levels():
    rubric = parse_pasted_rubric(HEADINGS)

    assert rubric.title == "Lab Report"
    assert [c.title for c in rubric.criteria] == ["Method", "Analysis"]
    assert [c.max_points for c in rubric.criteria] == [10.0, 20.0]
    assert [len(c.levels) for c in rubric.criteria] == [2, 1]


def test_the_type_in_a_heading_is_read():
    rubric = parse_pasted_rubric(HEADINGS)

    assert rubric.criteria[0].criterion_type == CriterionType.OBJECTIVE
    assert rubric.criteria[1].criterion_type == CriterionType.OPEN_ENDED


def test_a_heading_without_points_gets_the_default():
    rubric = parse_pasted_rubric("""\
## Clarity
- 5: Clear
""")

    assert rubric.criteria[0].title == "Clarity"
    assert rubric.criteria[0].max_points == 5.0


def test_bullets_before_any_heading_are_not_orphaned_onto_a_criterion():
    rubric = parse_pasted_rubric("""\
# Rubric
- 5: this bullet belongs to nothing
## Thesis (10 pts)
- 10: Excellent
""")

    assert len(rubric.criteria) == 1
    assert [level.score for level in rubric.criteria[0].levels] == [10.0]


def test_prose_that_parses_to_nothing_still_yields_a_gradeable_rubric():
    """A teacher pasting a paragraph must get something back, not an exception."""
    rubric = parse_pasted_rubric("Grade this however seems fair to you.")

    assert len(rubric.criteria) == 1
    assert rubric.criteria[0].id == "overall"
    assert rubric.criteria[0].max_points == 5.0


def test_an_explicit_title_overrides_the_heading():
    rubric = parse_pasted_rubric(HEADINGS, title="Week 3 Lab")

    assert rubric.title == "Week 3 Lab"
    # ...and the heading is then just the first criterion-less line, not a criterion.
    assert [c.title for c in rubric.criteria] == ["Lab Report", "Method", "Analysis"]


def test_untitled_paste_gets_a_placeholder_title():
    rubric = parse_pasted_rubric("""\
## Thesis (10 pts)
- 10: Excellent
""")

    assert rubric.title == "Pasted Rubric"


def test_pasting_nothing_is_rejected_rather_than_crashing():
    """Empty input reaches the parser whenever a teacher clears the box and saves."""
    with pytest.raises(ValueError):
        parse_pasted_rubric("   \n\n  ")


# ---------------------------------------------------------------------------
# Canvas import
# ---------------------------------------------------------------------------

def _canvas_run(payload, *, returncode=0, stderr=""):
    class Result:
        pass

    result = Result()
    result.returncode = returncode
    result.stdout = json.dumps(payload)
    result.stderr = stderr
    return lambda *args, **kwargs: result


def test_canvas_rubrics_are_read_from_the_results_envelope(monkeypatch):
    """canvas-pp-cli reads return {meta, results} — never {data}."""
    import subprocess

    monkeypatch.setattr(subprocess, "run", _canvas_run({
        "meta": {"ok": True},
        "results": {
            "title": "Canvas Essay Rubric",
            "criteria": [{
                "id": "_1234",
                "description": "Thesis",
                "points": 10,
                "ratings": [
                    {"points": 10, "description": "Full marks",
                     "long_description": "Clear and arguable"},
                    {"points": 0, "description": "None"},
                ],
            }],
        },
    }))

    rubric = import_from_canvas(1, 2)

    assert rubric.title == "Canvas Essay Rubric"
    assert rubric.criteria[0].title == "Thesis"
    assert rubric.criteria[0].max_points == 10.0
    assert [level.score for level in rubric.criteria[0].levels] == [10.0, 0.0]


def test_a_canvas_criterion_without_ratings_gets_a_two_level_default(monkeypatch):
    """A criterion with no levels cannot be scored against, so one is supplied."""
    import subprocess

    monkeypatch.setattr(subprocess, "run", _canvas_run({
        "results": {"title": "R", "criteria": [
            {"id": "c", "description": "Thesis", "points": 8},
        ]},
    }))

    levels = import_from_canvas(1, 2).criteria[0].levels

    assert [level.score for level in levels] == [8.0, 0.0]


def test_a_failing_canvas_cli_is_an_error_not_an_empty_rubric(monkeypatch):
    """Grading against a silently-empty rubric would score every student zero."""
    import subprocess

    monkeypatch.setattr(subprocess, "run",
                        _canvas_run({}, returncode=1, stderr="not authorized"))

    with pytest.raises(RuntimeError, match="not authorized"):
        import_from_canvas(1, 2)


# ---------------------------------------------------------------------------
# Versioning: a stored version is what the model was told at grading time
# ---------------------------------------------------------------------------

def _rubric(title="Essay", max_points=10.0):
    return Rubric(title=title, criteria=[
        RubricCriterion(
            id="thesis", title="Thesis", max_points=max_points,
            criterion_type=CriterionType.ANALYTIC,
            levels=[
                RubricLevel(score=max_points, label="Excellent", descriptor="clear"),
                RubricLevel(score=0, label="Absent", descriptor="none"),
            ],
        ),
    ])


@pytest.fixture
def store():
    return RubricStore(Database.memory())


def test_saving_the_same_title_twice_makes_a_second_version(store):
    store.save(_rubric())
    store.save(_rubric(max_points=20.0))

    assert store.get("Essay", version=1).criteria[0].max_points == 10.0
    assert store.get("Essay", version=2).criteria[0].max_points == 20.0


def test_getting_without_a_version_returns_the_latest(store):
    store.save(_rubric())
    store.save(_rubric(max_points=20.0))

    assert store.get("Essay").criteria[0].max_points == 20.0


def test_an_older_version_stays_readable_after_a_newer_one_lands(store):
    """Every grade references its rubric version; the audit trail depends on this."""
    store.save(_rubric())
    first = store.get("Essay", version=1)
    store.save(_rubric(max_points=20.0))

    assert store.get("Essay", version=1) == first


def test_an_unknown_rubric_is_absent_rather_than_an_error(store):
    assert store.get("Never Saved") is None


def test_listing_reports_one_row_per_title_at_its_latest_version(store):
    store.save(_rubric(title="Essay"))
    store.save(_rubric(title="Essay", max_points=20.0))
    store.save(_rubric(title="Lab"))

    listed = store.list()

    assert [(r["title"], r["version"]) for r in listed] == [("Essay", 2), ("Lab", 1)]


# ---------------------------------------------------------------------------
# amend_criterion: a new version, never an edit in place
# ---------------------------------------------------------------------------

def test_amending_leaves_the_original_rubric_untouched():
    original = _rubric()

    amended = amend_criterion(original, "thesis", new_max_points=20.0)

    assert amended.criteria[0].max_points == 20.0
    assert original.criteria[0].max_points == 10.0


def test_amending_only_touches_the_named_criterion():
    original = Rubric(title="Essay", criteria=[
        _rubric().criteria[0],
        RubricCriterion(id="evidence", title="Evidence", max_points=5.0,
                        criterion_type=CriterionType.OBJECTIVE),
    ])

    amended = amend_criterion(original, "thesis", new_description="Sharper wording")

    assert amended.criteria[0].description == "Sharper wording"
    assert amended.criteria[1] == original.criteria[1]


def test_amending_an_unknown_criterion_changes_nothing():
    original = _rubric()

    amended = amend_criterion(original, "no_such_criterion", new_max_points=99.0)

    assert amended == original
