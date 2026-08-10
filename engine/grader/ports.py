"""What the grading pipeline needs from the outside world.

These protocols exist so that describing a grading run does not mean importing
the thing that performs one. `grader.agent` is the only module that touches
pydantic_ai; everything else names a Judge and lets a caller supply it.

That indirection is not decoration. Before it, `api.py` imported `grade` at
module scope and `triage` imported it too, so every test of every route had to
push four fake pydantic_ai submodules into `sys.modules` before it could import
the engine at all — even a test that never graded anything.
"""

from __future__ import annotations

from typing import Protocol

from grader.schema import GradeRun, Rubric


class Judge(Protocol):
    """Grades one submission against one rubric.

    Bound to a model at construction: nothing downstream may choose or change
    which model is used, so a running batch cannot be retargeted mid-flight.

    Never raises for a grading failure — a refusal, a malformed response, or a
    timeout comes back as a GradeRun with `ok` false and `error` set, because a
    batch has to survive one bad submission.
    """

    def grade(
        self,
        rubric: Rubric,
        submission: str,
        *,
        anchors: str = "",
        materials: str = "",
    ) -> GradeRun: ...
