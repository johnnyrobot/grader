from grader.schema import (
    CriterionType,
    Flag,
    GradeDraft,
    Rubric,
    RubricCriterion,
    RubricLevel,
)


def _rubric():
    return Rubric(
        title="Test Rubric",
        criteria=[
            RubricCriterion(id="content", title="Content", max_points=5.0),
            RubricCriterion(id="clarity", title="Clarity", max_points=5.0,
                           criterion_type=CriterionType.OPEN_ENDED),
        ],
    )


def test_rubric_total_points():
    r = _rubric()
    assert r.total_points == 10.0


def test_rubric_render_includes_title():
    r = _rubric()
    rendered = r.render()
    assert "Test Rubric" in rendered
    assert "content" in rendered
    assert "clarity" in rendered


def test_rubric_render_includes_levels():
    r = Rubric(
        title="Leveled",
        criteria=[
            RubricCriterion(
                id="q", title="Q", max_points=3.0,
                levels=[
                    RubricLevel(score=3, label="Great", descriptor="Excellent"),
                    RubricLevel(score=0, label="Poor", descriptor="Weak"),
                ],
            ),
        ],
    )
    rendered = r.render()
    assert "Great" in rendered
    assert "Excellent" in rendered


def test_grade_draft_total():
    draft = GradeDraft(
        criterion_scores=[
            __import__("grader.schema", fromlist=["CriterionScore"]).CriterionScore(
                criterion_id="content", score=4.0, rationale="good"
            ),
            __import__("grader.schema", fromlist=["CriterionScore"]).CriterionScore(
                criterion_id="clarity", score=3.5, rationale="ok"
            ),
        ],
        feedback="decent work",
        confidence=0.8,
    )
    assert draft.total == 7.5


def test_validate_against_clean():
    r = _rubric()
    from grader.schema import CriterionScore
    draft = GradeDraft(
        criterion_scores=[
            CriterionScore(criterion_id="content", score=4.0, rationale="good"),
            CriterionScore(criterion_id="clarity", score=3.0, rationale="ok"),
        ],
        feedback="decent",
        confidence=0.8,
    )
    assert draft.validate_against(r) == []


def test_validate_against_unknown_criterion():
    r = _rubric()
    from grader.schema import CriterionScore
    draft = GradeDraft(
        criterion_scores=[
            CriterionScore(criterion_id="content", score=4.0, rationale="good"),
            CriterionScore(criterion_id="unknown", score=3.0, rationale="?"),
        ],
        feedback="",
        confidence=0.5,
    )
    problems = draft.validate_against(r)
    assert any("unknown" in p for p in problems)


def test_validate_against_score_out_of_range():
    r = _rubric()
    from grader.schema import CriterionScore
    draft = GradeDraft(
        criterion_scores=[
            CriterionScore(criterion_id="content", score=6.0, rationale="over"),
            CriterionScore(criterion_id="clarity", score=3.0, rationale="ok"),
        ],
        feedback="",
        confidence=0.5,
    )
    problems = draft.validate_against(r)
    assert any("outside" in p for p in problems)


def test_validate_against_missing_criterion():
    r = _rubric()
    from grader.schema import CriterionScore
    draft = GradeDraft(
        criterion_scores=[
            CriterionScore(criterion_id="content", score=4.0, rationale="good"),
        ],
        feedback="",
        confidence=0.5,
    )
    problems = draft.validate_against(r)
    assert any("clarity" in p for p in problems)


def test_validate_against_duplicate_criterion():
    r = _rubric()
    from grader.schema import CriterionScore
    draft = GradeDraft(
        criterion_scores=[
            CriterionScore(criterion_id="content", score=4.0, rationale="good"),
            CriterionScore(criterion_id="content", score=3.0, rationale="dup"),
            CriterionScore(criterion_id="clarity", score=3.0, rationale="ok"),
        ],
        feedback="",
        confidence=0.5,
    )
    problems = draft.validate_against(r)
    assert any("more than once" in p for p in problems)


def test_criterion_type_enum():
    assert CriterionType.OBJECTIVE == "objective"
    assert CriterionType.ANALYTIC == "analytic"
    assert CriterionType.OPEN_ENDED == "open_ended"


def test_flag_enum():
    assert Flag.LOW_CONFIDENCE == "low_confidence"
    assert Flag.NEEDS_HUMAN == "needs_human"