import json
import importlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


class _Noop:
    def __init__(self, *args, **kwargs):
        pass

    def output_validator(self, func):
        return func


@pytest.fixture
def api_module(monkeypatch):
    pydantic_ai = types.ModuleType("pydantic_ai")
    pydantic_ai.Agent = _Noop
    pydantic_ai.ModelRetry = RuntimeError
    monkeypatch.setitem(sys.modules, "pydantic_ai", pydantic_ai)

    models = types.ModuleType("pydantic_ai.models")
    models_openai = types.ModuleType("pydantic_ai.models.openai")
    models_openai.OpenAIChatModel = _Noop
    monkeypatch.setitem(sys.modules, "pydantic_ai.models", models)
    monkeypatch.setitem(sys.modules, "pydantic_ai.models.openai", models_openai)

    output = types.ModuleType("pydantic_ai.output")
    output.NativeOutput = _Noop
    output.PromptedOutput = _Noop
    output.ToolOutput = _Noop
    monkeypatch.setitem(sys.modules, "pydantic_ai.output", output)

    providers = types.ModuleType("pydantic_ai.providers")
    providers_openai = types.ModuleType("pydantic_ai.providers.openai")
    providers_openai.OpenAIProvider = _Noop
    monkeypatch.setitem(sys.modules, "pydantic_ai.providers", providers)
    monkeypatch.setitem(sys.modules, "pydantic_ai.providers.openai", providers_openai)

    settings = types.ModuleType("pydantic_ai.settings")
    settings.ModelSettings = _Noop
    monkeypatch.setitem(sys.modules, "pydantic_ai.settings", settings)

    sys.modules.pop("api", None)
    sys.modules.pop("grader.agent", None)
    module = importlib.import_module("api")
    yield module
    sys.modules.pop("api", None)
    sys.modules.pop("grader.agent", None)


def test_canvas_post_uses_canvas_user_id_from_imported_submission(tmp_path, monkeypatch, api_module):
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")

    captured = {}

    def fake_post_grades_batch(course_id, assignment_id, grade_data):
        captured["course_id"] = course_id
        captured["assignment_id"] = assignment_id
        captured["grade_data"] = grade_data
        return {"workflow_state": "completed", "completion": 100}

    monkeypatch.setattr("grader.canvas.post_grades_batch", fake_post_grades_batch)

    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_local", "canvas_10_20", "98765", "student text", "graded", api._now()),
    )
    db.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, ai_scores_json, "
        "ai_feedback, ai_confidence, ai_flags_json, lane, approved_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "grade_local",
            "sub_local",
            "canvas_10_20",
            json.dumps({"Thesis": 4, "Evidence": 3}),
            "feedback",
            0.8,
            "[]",
            "standard",
            api._now(),
            api._now(),
        ),
    )
    db.commit()
    db.close()

    result = api.canvas_post(api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["grade_local"]))

    assert result["posted"] == 1
    assert captured["course_id"] == 10
    assert captured["assignment_id"] == 20
    assert list(captured["grade_data"].keys()) == ["98765"]
    assert captured["grade_data"]["98765"]["posted_grade"] == 7


def test_canvas_post_skips_grade_without_canvas_user_id(tmp_path, monkeypatch, api_module):
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")

    def fail_if_called(course_id, assignment_id, grade_data):
        raise AssertionError("Canvas should not be called without a Canvas user id")

    monkeypatch.setattr("grader.canvas.post_grades_batch", fail_if_called)

    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_local", "canvas_10_20", None, "student text", "graded", api._now()),
    )
    db.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, ai_scores_json, "
        "ai_feedback, ai_confidence, ai_flags_json, lane, approved_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "grade_local",
            "sub_local",
            "canvas_10_20",
            json.dumps({"Thesis": 4}),
            "feedback",
            0.8,
            "[]",
            "standard",
            api._now(),
            api._now(),
        ),
    )
    db.commit()
    db.close()

    with pytest.raises(Exception) as exc_info:
        api.canvas_post(api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["grade_local"]))
    assert "no approved grades to post" in str(exc_info.value)
