import json
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


def _passing_result(**overrides):
    result = {
        "progress_state": "completed",
        "posted": 1,
        "read_back": [{"expected_score": 9, "actual_score": 9, "comments": 1}],
        "attachment": {
            "filename": "ZZ-GRADER-TEST-feedback-gr_1.md",
            "read_back": True,
            "canvas_attachment_id": 5150,
            "submission_comment_id": 91,
            "in_shared_course_files": False,
        },
        "attachment_queue": {
            "filename": "ZZ-GRADER-TEST-feedback-gr_2.md",
            "queued": 1,
            "pending_file_preserved": True,
            "approval_intact": True,
            "flushed": 1,
            "read_back": True,
            "canvas_attachment_id": 5151,
            "submission_comment_id": 92,
            "in_shared_course_files": False,
        },
    }
    result.update(overrides)
    return result


def test_summarize_result_requires_completed_progress_and_readback_match():
    assert summarize_result(_passing_result())["status"] == "PASS"


def test_an_attachment_that_canvas_never_shows_back_is_not_a_pass():
    """A 201 on the upload is not evidence the file is on the submission."""
    result = _passing_result(
        attachment={"filename": "f.md", "read_back": False, "in_shared_course_files": False}
    )

    summary = summarize_result(result)

    assert summary["status"] == "FAIL"
    assert any("attachment" in f for f in summary["failures"])


def test_an_attachment_that_landed_in_shared_course_files_is_a_failure():
    """`/courses/:c/files` is where classmates can reach one student's work."""
    result = _passing_result(
        attachment={
            "filename": "f.md",
            "read_back": True,
            "canvas_attachment_id": 5150,
            "submission_comment_id": 91,
            "in_shared_course_files": True,
        }
    )

    summary = summarize_result(result)

    assert summary["status"] == "FAIL"
    assert any("course files" in f for f in summary["failures"])


def test_an_upload_failure_that_did_not_queue_is_a_failure():
    """FR-3.5: the outage must not cost the teacher their approval or the file."""
    result = _passing_result(
        attachment_queue={
            "filename": "f.md",
            "queued": 0,
            "pending_file_preserved": False,
            "approval_intact": True,
            "flushed": 0,
            "read_back": False,
            "in_shared_course_files": False,
        }
    )

    summary = summarize_result(result)

    assert summary["status"] == "FAIL"
    assert any("queue" in f for f in summary["failures"])


def test_a_run_with_no_attachment_evidence_at_all_is_a_failure():
    """The shape this harness produced before it covered FR-3.4 must not pass now."""
    result = {
        "progress_state": "completed",
        "posted": 1,
        "read_back": [{"expected_score": 9, "actual_score": 9, "comments": 1}],
    }

    assert summarize_result(result)["status"] == "FAIL"


def test_the_result_records_which_commit_it_ran_against():
    """AGENTS.md freshness-checks Canvas results against the current commit.

    Without a stamp the artifact cannot be told apart from one written three
    commits ago, and the release docs cite it as proof of current behaviour.
    """
    stamped = live_canvas_e2e.stamp_provenance({"status": "PASS"})

    assert stamped["source_commit"]
    assert stamped["generated_at"].endswith("+00:00")
    assert stamped["status"] == "PASS"


def test_a_run_outside_a_checkout_still_produces_an_artifact():
    """Evidence with unknown provenance beats a harness that crashes writing it."""
    stamped = live_canvas_e2e.stamp_provenance({"status": "PASS"}, commit=None)

    assert stamped["source_commit"] == "unknown"


def test_the_queued_file_check_reads_the_column_rather_than_grepping_it():
    """A substring hit anywhere in the row would pass — including in the error
    text, which quotes the path back."""
    item = {
        "pending_files": None,
        "last_error": "attachment upload failed for /d/feedback.md",
    }

    assert live_canvas_e2e._pending_file_is_preserved([item], "feedback.md") is False
    assert live_canvas_e2e._pending_file_is_preserved(
        [{"pending_files": json.dumps(["/d/feedback.md"])}], "feedback.md"
    ) is True


def test_a_queue_row_with_unreadable_pending_files_is_not_taken_as_preserved():
    assert live_canvas_e2e._pending_file_is_preserved(
        [{"pending_files": "not json"}], "feedback.md"
    ) is False


# -- Reading the attachment back ---------------------------------------------


def _submission_with_comment_attachment(filename):
    return {
        "score": 4.0,
        "submission_comments": [
            {"id": 90, "comment": "no files here", "attachments": []},
            {
                "id": 91,
                "comment": "GRADER TEST",
                "attachments": [
                    {"id": 5150, "display_name": filename, "filename": filename}
                ],
            },
        ],
    }


def test_the_attachment_is_found_on_the_submission_comment_that_carries_it():
    found = live_canvas_e2e._find_comment_attachment(
        _submission_with_comment_attachment("feedback.md"), "feedback.md"
    )

    assert found == {"canvas_attachment_id": 5150, "submission_comment_id": 91}


def test_another_students_file_name_is_not_mistaken_for_this_one():
    assert (
        live_canvas_e2e._find_comment_attachment(
            _submission_with_comment_attachment("feedback.md"), "other.md"
        )
        is None
    )


def test_a_submission_with_no_comments_reads_back_nothing():
    assert live_canvas_e2e._find_comment_attachment({}, "feedback.md") is None


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


IMPORTED = {
    "submissions": [
        {"id": "sub_a", "canvas_user_id": "9"},
        {"id": "sub_b", "canvas_user_id": "8"},
        {"id": "sub_c", "canvas_user_id": "7"},
    ]
}


def test_select_submissions_prefers_target_canvas_user():
    assert live_canvas_e2e._select_submissions(IMPORTED, 8, 1) == ["sub_b"]


def test_select_submissions_puts_the_target_first_and_fills_the_rest():
    """The attachment queue case needs its own submission: a posted grade
    cannot be posted again, and the probe has to reach the post route."""
    assert live_canvas_e2e._select_submissions(IMPORTED, 8, 2) == ["sub_b", "sub_a"]


def test_select_submissions_finds_nothing_when_the_target_was_not_imported():
    assert live_canvas_e2e._select_submissions(IMPORTED, 99, 1) == []


def test_select_submissions_returns_what_it_has_when_that_is_too_few():
    assert live_canvas_e2e._select_submissions({"submissions": []}, None, 2) == []


def test_select_submissions_skips_students_who_submitted_nothing():
    """A score read back off an empty submission is weak evidence.

    Canvas keeps a row for a student who never submitted, and the engine
    imports it, so the harness would otherwise grade nothing and read the
    result back as if it had graded work.
    """
    selected = live_canvas_e2e._select_submissions(
        IMPORTED, None, 2, with_work={"8", "7"}
    )

    assert selected == ["sub_b", "sub_c"]


def test_select_submissions_blocks_rather_than_grading_an_empty_submission():
    assert live_canvas_e2e._select_submissions(IMPORTED, None, 2, with_work={"9"}) == []


def test_the_batch_grades_against_the_rubric_this_harness_created(monkeypatch):
    """A teacher's engine has other rubrics saved, and grading against the
    wrong one is silent — the scores still look plausible."""
    calls = []

    def fake_engine_request(config, method, path, data=None):
        calls.append((path, data))
        if path == "/health":
            return {"status": "ok"}
        if path == "/api/canvas/import":
            return {"imported": 2, "submissions": [
                {"id": "sub_a", "canvas_user_id": "9"},
                {"id": "sub_b", "canvas_user_id": "8"},
            ]}
        if path == "/api/grade/batch":
            raise SystemExit("far enough")
        return {}

    monkeypatch.setattr(live_canvas_e2e, "_engine_request", fake_engine_request)
    monkeypatch.setattr("grader.canvas.missing_canvas_credentials", lambda: [])
    monkeypatch.setattr(
        live_canvas_e2e, "_canvas_user_ids_with_work", lambda *a: {"8", "9"}
    )

    with pytest.raises(SystemExit):
        live_canvas_e2e.run(
            CanvasE2EConfig(
                course_id=106,
                assignment_id=2841,
                course_name="ZZ-GRADER-TEST-SANDBOX",
                apply=True,
            )
        )

    created = next(data for path, data in calls if path == "/api/rubrics")
    batched = next(data for path, data in calls if path == "/api/grade/batch")
    assert batched["rubric_title"] == created["title"] == live_canvas_e2e.RUBRIC_TITLE


# -- Forcing the upload failure ----------------------------------------------


def test_the_canvas_url_is_put_back_even_when_the_probe_raises(monkeypatch):
    """The probe retargets the engine at an unroutable Canvas. Leaving it there
    would break the next real post from this machine."""
    puts = []

    def fake_engine_request(config, method, path, data=None):
        if method == "PUT" and path == "/api/settings":
            puts.append(data["canvas_url"])
            return {}
        if path == "/api/settings":
            return {"canvas_url": "https://canvas.example.test"}
        raise RuntimeError("boom")

    monkeypatch.setattr(live_canvas_e2e, "_engine_request", fake_engine_request)
    config = CanvasE2EConfig(
        course_id=106,
        assignment_id=2841,
        course_name="ZZ-GRADER-TEST-SANDBOX",
        apply=True,
    )

    with pytest.raises(RuntimeError, match="boom"):
        with live_canvas_e2e._canvas_unreachable(config):
            live_canvas_e2e._engine_request(config, "POST", "/api/canvas/post")

    assert puts == [live_canvas_e2e.UNROUTABLE_CANVAS, "https://canvas.example.test"]
