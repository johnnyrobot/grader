import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.live_canvas_e2e import redact, summarize_result, validate_course_name


def test_validate_course_name_allows_zz_boundary():
    validate_course_name("ZZ-GRADER-TEST-SANDBOX (safe to delete)")


def test_validate_course_name_rejects_unmarked_course():
    with pytest.raises(ValueError):
        validate_course_name("Production Biology 101")


def test_redact_removes_tokens_and_student_emails():
    text = "token=abc123 student@example.edu https://canvas.example/api"
    redacted = redact(text)
    assert "abc123" not in redacted
    assert "student@example.edu" not in redacted


def test_summarize_result_requires_completed_progress_and_readback_match():
    result = {
        "progress_state": "completed",
        "posted": 1,
        "read_back": [{"expected_score": 9, "actual_score": 9}],
    }
    assert summarize_result(result)["status"] == "PASS"
