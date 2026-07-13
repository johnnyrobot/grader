import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader import canvas


def test_run_passes_stdin_json_to_canvas_cli(monkeypatch):
    captured = {}

    def fake_run(cmd, *, capture_output, input, text, timeout):
        captured.update({
            "cmd": cmd,
            "capture_output": capture_output,
            "input": input,
            "text": text,
            "timeout": timeout,
        })
        return SimpleNamespace(returncode=0, stdout='{"success":true,"data":{"id":123}}', stderr="")

    monkeypatch.setattr(canvas.subprocess, "run", fake_run)

    result = canvas._run("submissions", "bulk-update", "10", "--stdin", stdin_json={"x": 1})

    assert result["success"] is True
    assert captured["cmd"] == [
        "canvas-pp-cli",
        "submissions",
        "bulk-update",
        "10",
        "--stdin",
        "--agent",
        "--data-source",
        "live",
        "--no-cache",
    ]
    assert json.loads(captured["input"]) == {"x": 1}
    assert captured["text"] is True


def test_post_grade_uses_current_canvas_cli_flag_names(monkeypatch):
    captured = {}

    def fake_run(*args, timeout=30, stdin_json=None):
        captured["args"] = args
        captured["timeout"] = timeout
        captured["stdin_json"] = stdin_json
        return {"success": True, "data": {"id": "submission_1"}}

    monkeypatch.setattr(canvas, "_run", fake_run)

    result = canvas.post_grade(
        10,
        20,
        30,
        grade=7,
        rubric_assessment={"criterion_1": {"points": 4}},
        comment="Looks good.",
        comment_file_ids=[123],
    )

    assert result == {"id": "submission_1"}
    assert captured["args"] == (
        "submissions",
        "update",
        "10",
        "20",
        "30",
        "--submission-posted-grade=7",
        "--comment-text-comment=Looks good.",
        '--rubric-assessment={"criterion_1": {"points": 4}}',
        "--comment-file-ids=123",
    )
    assert captured["stdin_json"] is None


def test_post_grades_batch_flattens_grade_data_for_stdin(monkeypatch):
    captured = {}

    def fake_run(*args, timeout=30, stdin_json=None):
        captured["args"] = args
        captured["timeout"] = timeout
        captured["stdin_json"] = stdin_json
        return {"success": True, "data": {"workflow_state": "completed", "completion": 100}}

    monkeypatch.setattr(canvas, "_run", fake_run)

    progress = canvas.post_grades_batch(
        10,
        20,
        {
            "98765": {
                "posted_grade": 7,
                "rubric_assessment": {"criterion_1": {"points": 4}},
                "text_comment": "Teacher approved.",
                "file_ids": [321],
            }
        },
    )

    assert progress["workflow_state"] == "completed"
    assert captured["args"] == ("submissions", "bulk-update", "10", "--stdin")
    assert captured["stdin_json"] == {
        "grade_data": {
            "20": {
                "98765": {
                    "posted_grade": "7",
                    "rubric_assessment": {"criterion_1": {"points": 4}},
                    "text_comment": "Teacher approved.",
                    "file_ids": [321],
                }
            }
        }
    }


def test_poll_progress_uses_progress_id_only(monkeypatch):
    captured = {}

    def fake_run(*args, timeout=30, stdin_json=None):
        captured["args"] = args
        return {"results": {"id": 2146, "workflow_state": "completed", "completion": 100}}

    monkeypatch.setattr(canvas, "_run", fake_run)

    progress = canvas._poll_progress(106, 2146)

    assert progress["workflow_state"] == "completed"
    assert captured["args"] == ("progress", "show", "2146")


def test_get_submission_requests_submission_comments_include(monkeypatch):
    captured = {}

    def fake_run(*args, timeout=30, stdin_json=None):
        captured["args"] = args
        return {"results": {"id": 29, "submission_comments": [{"id": 12}]}}

    monkeypatch.setattr(canvas, "_run", fake_run)

    result = canvas.get_submission(106, 2840, 8)

    assert result["submission_comments"] == [{"id": 12}]
    assert captured["args"] == (
        "submissions",
        "show",
        "106",
        "2840",
        "8",
        "--include",
        "submission_comments",
    )
