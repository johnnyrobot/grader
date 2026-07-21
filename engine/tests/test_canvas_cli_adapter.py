import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader import canvas


def test_run_passes_stdin_json_to_canvas_cli(monkeypatch):
    captured = {}

    def fake_run(cmd, *, capture_output, input, text, timeout, env):
        captured.update({
            "cmd": cmd,
            "capture_output": capture_output,
            "input": input,
            "text": text,
            "timeout": timeout,
            "env": env,
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


def test_run_passes_saved_canvas_credentials_only_through_environment(monkeypatch):
    captured = {}

    def fake_run(cmd, *, capture_output, input, text, timeout, env):
        captured["cmd"] = cmd
        captured["env"] = env
        return SimpleNamespace(returncode=0, stdout='{"results":[]}', stderr="")

    monkeypatch.setattr(canvas.subprocess, "run", fake_run)

    canvas._run(
        "submissions",
        "index",
        "10",
        "20",
        base_url="https://canvas.example.test/",
        token="saved-test-token",
    )

    assert captured["env"]["CANVAS_BASE_URL"] == "https://canvas.example.test"
    assert captured["env"]["CANVAS_API_TOKEN"] == "saved-test-token"
    assert captured["env"]["CANVAS_ACCESS_TOKEN"] == "saved-test-token"
    assert "saved-test-token" not in " ".join(captured["cmd"])


def test_post_grade_uses_current_canvas_cli_flag_names(monkeypatch):
    captured = {}

    def fake_run(*args, timeout=30, stdin_json=None, base_url=None, token=None):
        captured["args"] = args
        captured["timeout"] = timeout
        captured["stdin_json"] = stdin_json
        captured["base_url"] = base_url
        captured["token"] = token
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

    def fake_run(*args, timeout=30, stdin_json=None, base_url=None, token=None):
        captured["args"] = args
        captured["timeout"] = timeout
        captured["stdin_json"] = stdin_json
        captured["base_url"] = base_url
        captured["token"] = token
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

    def fake_run(*args, timeout=30, stdin_json=None, base_url=None, token=None):
        captured["args"] = args
        return {"results": {"id": 2146, "workflow_state": "completed", "completion": 100}}

    monkeypatch.setattr(canvas, "_run", fake_run)

    progress = canvas._poll_progress(106, 2146)

    assert progress["workflow_state"] == "completed"
    assert captured["args"] == ("progress", "show", "2146")


def test_get_submission_requests_submission_comments_include(monkeypatch):
    captured = {}

    def fake_run(*args, timeout=30, stdin_json=None, base_url=None, token=None):
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


def test_resolve_canvas_credentials_from_canvas_pp_cli_files(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    creds_path = tmp_path / "credentials.toml"
    config_path.write_text('base_url = "https://canvas.example.test"\n')
    creds_path.write_text('access_token = "test-token"\n')

    monkeypatch.delenv("CANVAS_BASE_URL", raising=False)
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)
    monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)
    monkeypatch.delenv("CANVAS_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(canvas, "CONFIG_PATHS", [config_path])
    monkeypatch.setattr(canvas, "CREDS_PATHS", [creds_path])

    assert canvas.resolve_canvas_base_url() == "https://canvas.example.test"
    assert canvas.resolve_canvas_token() == "test-token"
    assert canvas.missing_canvas_credentials() == []


def test_env_canvas_credentials_override_canvas_pp_cli_files(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    creds_path = tmp_path / "credentials.toml"
    config_path.write_text('base_url = "https://canvas-file.example.test"\n')
    creds_path.write_text('access_token = "file-token"\n')

    monkeypatch.setenv("CANVAS_BASE_URL", "https://canvas-env.example.test")
    monkeypatch.setenv("CANVAS_API_TOKEN", "env-token")
    monkeypatch.setattr(canvas, "CONFIG_PATHS", [config_path])
    monkeypatch.setattr(canvas, "CREDS_PATHS", [creds_path])

    assert canvas.resolve_canvas_base_url() == "https://canvas-env.example.test"
    assert canvas.resolve_canvas_token() == "env-token"
