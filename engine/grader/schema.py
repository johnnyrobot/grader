"""Grading contract: rubrics in, structured grades out.

These models ARE the reliability mechanism. Pydantic AI validates the model's output
against them and re-prompts on failure, so anything we can express as a constraint here
is something the LLM cannot get wrong — score out of range, missing criterion, invented
criterion id. Push rules into the schema rather than into prose in the prompt.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class CriterionType(str, Enum):
    """Drives review weighting, not scoring.

    Research (arXiv 2603.18765) finds LLM graders carry significant style bias on
    open-ended work — informal tone costs ~1.9/10 points, non-native phrasing ~1.35 —
    and the bias survives explicit "ignore style" instructions. Math/code show almost
    none. So open_ended criteria get routed to human review more aggressively.
    """

    OBJECTIVE = "objective"      # right/wrong: math, code, factual recall
    ANALYTIC = "analytic"        # structured judgment against explicit descriptors
    OPEN_ENDED = "open_ended"    # holistic prose judgment — highest bias risk


class RubricLevel(BaseModel):
    score: float = Field(description="Points awarded at this level")
    label: str = Field(description="e.g. 'Proficient'")
    descriptor: str = Field(description="What work at this level actually looks like")


class RubricCriterion(BaseModel):
    id: str = Field(description="Stable id, e.g. 'thesis'")
    title: str
    description: str = ""
    max_points: float
    criterion_type: CriterionType = CriterionType.ANALYTIC
    levels: list[RubricLevel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_levels(self) -> RubricCriterion:
        # 3-5 anchored levels on a 0-5 scale give the strongest LLM-human agreement;
        # 0-10 is consistently the weakest (arXiv 2601.03444). Warn, don't block —
        # the teacher is the source of truth and may have institutional constraints.
        if self.levels and not (3 <= len(self.levels) <= 6):
            import warnings
            warnings.warn(
                f"Criterion {self.id!r} has {len(self.levels)} levels; 3-5 anchored levels "
                "score most reliably.",
                stacklevel=2,
            )
        return self


class Rubric(BaseModel):
    title: str
    criteria: list[RubricCriterion]

    @property
    def total_points(self) -> float:
        return sum(c.max_points for c in self.criteria)

    def render(self) -> str:
        """The rubric as the model sees it. Explicit descriptors are what make it work."""
        out = [f"# Rubric: {self.title}", f"Total: {self.total_points:g} points", ""]
        for c in self.criteria:
            out.append(f"## [{c.id}] {c.title}  ({c.max_points:g} pts, {c.criterion_type.value})")
            if c.description:
                out.append(c.description)
            for lv in sorted(c.levels, key=lambda x: -x.score):
                out.append(f"  - {lv.score:g} ({lv.label}): {lv.descriptor}")
            out.append("")
        return "\n".join(out)


class Flag(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    CITATION_ISSUES = "citation_issues"
    OFF_TOPIC = "off_topic"
    POSSIBLE_MISCONDUCT = "possible_misconduct"
    UNREADABLE = "unreadable"
    NEEDS_HUMAN = "needs_human"


class CriterionScore(BaseModel):
    criterion_id: str = Field(description="Must match a rubric criterion id exactly")
    score: float = Field(description="Points awarded; must not exceed the criterion's max")
    rationale: str = Field(
        description="Why this score, citing specific evidence from the submission. "
                    "This is shown to the teacher — it must justify, not restate."
    )


class GradeDraft(BaseModel):
    """A DRAFT. The teacher owns the final score; nothing posts without their approval."""

    criterion_scores: list[CriterionScore]
    feedback: str = Field(
        description="Formative feedback addressed to the student: what they did well, what to "
                    "fix, and how. Must be actionable — name the next step, don't just judge."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Your confidence this grade matches what the instructor would give. Be honest: "
                    "low confidence routes the work to human review, which is the correct outcome "
                    "when the submission is ambiguous, off-rubric, or unlike the examples.",
    )
    flags: list[Flag] = Field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(cs.score for cs in self.criterion_scores)

    def validate_against(self, rubric: Rubric) -> list[str]:
        """Structural checks the schema alone can't express (they need the rubric).

        Returns problems; empty means clean. Callers feed these back as a ModelRetry so
        the model repairs its own output instead of us silently accepting a broken grade.
        """
        problems: list[str] = []
        by_id = {c.id: c for c in rubric.criteria}
        seen: set[str] = set()

        for cs in self.criterion_scores:
            crit = by_id.get(cs.criterion_id)
            if crit is None:
                problems.append(
                    f"criterion_id {cs.criterion_id!r} is not in the rubric "
                    f"(valid ids: {sorted(by_id)})"
                )
                continue
            if cs.criterion_id in seen:
                problems.append(f"criterion_id {cs.criterion_id!r} scored more than once")
            seen.add(cs.criterion_id)
            if not (0 <= cs.score <= crit.max_points):
                problems.append(
                    f"score {cs.score} for {cs.criterion_id!r} is outside 0..{crit.max_points:g}"
                )

        for missing in sorted(set(by_id) - seen):
            problems.append(f"criterion {missing!r} was not scored")
        return problems
