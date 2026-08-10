"""Making the sandbox reusable, without ever touching a real student.

`live_canvas_e2e.py` consumes two ungraded submissions per run. Topping that up
by creating two more fake students every time is how a throwaway course grows
without bound; clearing the grades off the ones already there is the same run
again. Clearing a grade is a write, and a write to the wrong course is a real
student's grade deleted — so the guards are what these tests are about.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.seed_canvas_sandbox import SANDBOX_MARKER, STUDENT_PREFIX, reset_grades


class FakeCanvas:
    """Records writes instead of making them."""

    def __init__(self, course_name: str, submissions: list[dict]) -> None:
        self.course_name = course_name
        self.submissions = submissions
        self.puts: list[tuple[str, dict]] = []
        self.apply = True

    def get(self, path: str, **kwargs):
        if path.startswith("/courses/") and "/assignments/" not in path:
            return {"id": 106, "name": self.course_name}
        return self.submissions

    def put(self, path: str, data: dict):
        self.puts.append((path, data))
        return {}


def _submissions():
    return [
        {"user_id": 9, "score": 4.0, "user": {"name": f"{STUDENT_PREFIX} Student 01"}},
        {"user_id": 10, "score": None, "user": {"name": f"{STUDENT_PREFIX} Student 02"}},
        {"user_id": 99, "score": 3.0, "user": {"name": "Dana Whitfield"}},
    ]


def test_only_the_fake_students_have_their_grades_cleared():
    """A real name in a sandbox course is still a real student's grade."""
    cv = FakeCanvas("ZZ-GRADER-TEST-SANDBOX (safe to delete)", _submissions())

    cleared = reset_grades(cv, 106, 2841)

    assert cleared == [9]
    assert [path for path, _ in cv.puts] == [
        "/courses/106/assignments/2841/submissions/9"
    ]


def test_clearing_a_grade_sends_an_empty_posted_grade():
    cv = FakeCanvas("ZZ-GRADER-TEST-SANDBOX (safe to delete)", _submissions())

    reset_grades(cv, 106, 2841)

    assert cv.puts[0][1] == {"submission[posted_grade]": ""}


def test_a_submission_with_no_grade_is_left_alone():
    """Nothing to clear is not a reason to write to Canvas."""
    cv = FakeCanvas("ZZ-GRADER-TEST-SANDBOX (safe to delete)", _submissions())

    cleared = reset_grades(cv, 106, 2841)

    assert 10 not in cleared


def test_a_course_without_the_sandbox_marker_is_refused():
    cv = FakeCanvas("Biology 101", _submissions())

    with pytest.raises(SystemExit, match=SANDBOX_MARKER):
        reset_grades(cv, 106, 2841)

    assert cv.puts == []
