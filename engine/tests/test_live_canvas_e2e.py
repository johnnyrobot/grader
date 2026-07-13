import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts import live_canvas_e2e
from scripts.live_canvas_e2e import CanvasE2EConfig, redact, summarize_result, validate_course_name


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
        "read_back": [{"expected_score": 9, "actual_score": 9, "comments": 1}],
    }
    assert summarize_result(result)["status"] == "PASS"


def test_approve_grade_treats_already_approved_as_idempotent(monkeypatch):
    def fake_engine_request(config, method, path, data=None):
        raise RuntimeError(
            'POST /api/grades/gr_123/approve -> HTTP 409: {"detail":"already approved"}'
        )

    monkeypatch.setattr(live_canvas_e2e, "_engine_request", fake_engine_request)

    result = live_canvas_e2e._approve_grade(
        CanvasE2EConfig(
            course_id=106,
            assignment_id=2837,
            course_name="ZZ-GRADER-TEST-SANDBOX",
            apply=True,
        ),
        "gr_123",
    )

    assert result == {"status": "already_approved"}


def test_teacher_override_posts_nonzero_scores_and_feedback(monkeypatch):
    captured = {}

    def fake_engine_request(config, method, path, data=None):
        captured.update({"method": method, "path": path, "data": data})
        return {"overridden": True}

    monkeypatch.setattr(live_canvas_e2e, "_engine_request", fake_engine_request)

    result = live_canvas_e2e._teacher_override(
        CanvasE2EConfig(
            course_id=106,
            assignment_id=2838,
            course_name="ZZ-GRADER-TEST-SANDBOX",
            apply=True,
        ),
        {"id": "gr_123", "ai_scores": {"thesis": 0, "evidence": 0, "style": 0}},
    )

    assert result == {"overridden": True}
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/grades/gr_123/override"
    assert captured["data"]["teacher_scores"] == {"thesis": 2.0, "evidence": 2.0, "style": 0.0}
    assert "teacher-approved feedback" in captured["data"]["teacher_feedback"]


def test_select_imported_submission_prefers_target_canvas_user():
    imported = {
        "submissions": [
            {"id": "sub_a", "canvas_user_id": "9"},
            {"id": "sub_b", "canvas_user_id": "8"},
        ]
    }

    assert live_canvas_e2e._select_imported_submission(imported, 8) == "sub_b"
    assert live_canvas_e2e._select_imported_submission(imported, 99) is None
