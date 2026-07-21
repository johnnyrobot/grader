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
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")
    api._save_settings({
        **api.DEFAULT_SETTINGS,
        "canvas_url": "https://canvas.example.test",
        "canvas_token": "saved-test-token",
        "data_dir": str(tmp_path / "canvas"),
    })

    captured = {}

    def fake_post_grades_batch(course_id, assignment_id, grade_data, **connection):
        captured["course_id"] = course_id
        captured["assignment_id"] = assignment_id
        captured["grade_data"] = grade_data
        captured["connection"] = connection
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
    assert captured["grade_data"]["98765"]["text_comment"] == "feedback"
    assert "rubric_assessment" not in captured["grade_data"]["98765"]
    assert captured["connection"] == {
        "base_url": "https://canvas.example.test",
        "token": "saved-test-token",
    }


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


def test_canvas_import_returns_local_submission_ids(tmp_path, monkeypatch, api_module):
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr("grader.canvas.list_ungraded", lambda course_id, assignment_id, **connection: [
        {"user_id": 98765, "body": "student text", "attachments": []}
    ])
    monkeypatch.setattr(
        "grader.canvas.download_submission_attachments",
        lambda submission, dest_dir, **connection: [],
    )

    result = api.canvas_import(
        api.CanvasImportReq(course_id=10, assignment_id=20, download_attachments=True)
    )

    assert result["imported"] == 1
    assert result["submissions"] == [{"id": result["submissions"][0]["id"], "canvas_user_id": "98765"}]
    assert result["submissions"][0]["id"].startswith("sub_")


def test_grade_batch_can_limit_to_requested_submission_ids(tmp_path, monkeypatch, api_module):
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api._rubric_store, "list", lambda: [{"title": "Rubric"}])
    monkeypatch.setattr(api._rubric_store, "get", lambda title: object())

    captured = {}

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            captured["args"] = args
            captured["daemon"] = daemon

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(api.threading, "Thread", FakeThread)

    db = api._db()
    for sub_id in ("sub_keep", "sub_skip"):
        db.execute(
            "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (sub_id, "canvas_10_20", sub_id, "student text", "imported", api._now()),
        )
    db.commit()
    db.close()

    result = api.grade_batch(
        api.GradeBatch(assignment_id="canvas_10_20", submission_ids=["sub_keep"])
    )

    assert result["total"] == 1
    assert captured["args"][2] == ["sub_keep"]
    assert captured["started"] is True


def test_saved_settings_drive_canvas_grade_batch_and_attachment_directory(
    tmp_path, monkeypatch, api_module
):
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")
    attachment_root = tmp_path / "canvas-downloads"
    api._save_settings({
        **api.DEFAULT_SETTINGS,
        "canvas_url": "https://canvas.example.test",
        "canvas_token": "saved-test-token",
        "data_dir": str(attachment_root),
        "model_text": "grader-text:test",
        "model_vision": "grader-vision:test",
        "model_embed": "nomic-embed-text:test",
        "review_first_threshold": 0.6,
        "auto_approve_threshold": 0.9,
    })

    canvas_calls = {}

    def fake_list(course_id, assignment_id, **connection):
        canvas_calls["list"] = connection
        return [{
            "user_id": 98765,
            "body": "student text",
            "attachments": [{"id": 4, "filename": "paper.txt", "url": "https://example.test"}],
        }]

    def fake_download(submission, dest_dir, **connection):
        canvas_calls["download"] = {"dest_dir": dest_dir, **connection}
        return []

    monkeypatch.setattr("grader.canvas.list_ungraded", fake_list)
    monkeypatch.setattr("grader.canvas.download_submission_attachments", fake_download)

    imported = api.canvas_import(api.CanvasImportReq(course_id=10, assignment_id=20))

    assert imported["imported"] == 1
    assert canvas_calls["list"] == {
        "base_url": "https://canvas.example.test",
        "token": "saved-test-token",
    }
    assert canvas_calls["download"]["token"] == "saved-test-token"
    assert canvas_calls["download"]["dest_dir"] == (
        attachment_root / "attachments" / "course_10" / "assign_20"
    )

    monkeypatch.setattr(api._rubric_store, "list", lambda: [{"title": "Rubric"}])
    monkeypatch.setattr(api._rubric_store, "get", lambda title: object())
    captured = {}

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            captured["args"] = args

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(api.threading, "Thread", FakeThread)
    result = api.grade_batch(api.GradeBatch(assignment_id="canvas_10_20"))

    assert result["total"] == 1
    assert captured["args"][4] == "grader-text:test"
    # The saved embedding model travels with the job...
    assert captured["args"][6:] == (0.6, 0.9, "nomic-embed-text:test")
    # ...rather than being written onto the store other threads share.
    assert api._exemplar_store.embedding_model == "nomic-embed-text"


def test_saved_vision_model_drives_transcription(tmp_path, monkeypatch, api_module):
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")
    api._save_settings({
        **api.DEFAULT_SETTINGS,
        "model_vision": "grader-vision:test",
        "data_dir": str(tmp_path / "canvas"),
    })
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"synthetic image fixture")
    captured = {}

    def fake_transcribe(paths, *, model):
        captured["paths"] = paths
        captured["model"] = model
        return {
            "text": "synthetic transcription",
            "avg_confidence": 0.9,
            "low_confidence_pages": [],
            "needs_review": False,
            "page_results": [],
        }

    monkeypatch.setattr("grader.handwriting.transcribe_pages", fake_transcribe)

    result = api.transcribe_images(api.TranscribeReq(file_paths=[str(image_path)]))

    assert result["text"] == "synthetic transcription"
    assert captured["model"] == "grader-vision:test"
    assert captured["paths"] == [image_path]


def test_settings_are_validated_masked_and_written_owner_only(tmp_path, monkeypatch, api_module):
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")

    result = api.update_settings(api.SettingsUpdate(
        canvas_url="https://canvas.example.test/",
        canvas_token="1234567890abcdef",
        data_dir=str(tmp_path / "attachments"),
        review_first_threshold=0.65,
        auto_approve_threshold=0.9,
    ))

    assert result["settings"]["canvas_url"] == "https://canvas.example.test"
    assert result["settings"]["canvas_token_masked"] == "1234...cdef"
    assert "canvas_token" not in result["settings"]
    assert api.get_settings()["canvas_token_masked"] == "1234...cdef"
    assert api.SETTINGS_FILE.stat().st_mode & 0o777 == 0o600

    with pytest.raises(Exception, match="needs-review threshold"):
        api.SettingsState(
            **{
                **api.DEFAULT_SETTINGS,
                "review_first_threshold": 0.95,
                "auto_approve_threshold": 0.9,
            }
        )

    with pytest.raises(Exception, match="capped grader-text"):
        api.GradeBatch(assignment_id="essay", model="qwen3:8b")


def test_updates_check_route_is_not_shadowed_by_audit_route(monkeypatch, api_module):
    api = api_module
    routes = [
        route
        for route in api.app.routes
        if getattr(route, "path", None) in {"/api/updates/check", "/api/audit"}
    ]
    by_path = {route.path: route for route in routes}

    assert by_path["/api/updates/check"].endpoint.__name__ == "check_updates"
    assert by_path["/api/audit"].endpoint.__name__ == "get_audit"

    class Response:
        status_code = 200

        def json(self):
            return {
                "version": "0.1.1",
                "notes": "Patch",
                "pub_date": "2026-07-13T00:00:00Z",
            }

    captured = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setenv("GRADER_APP_VERSION", "0.1.0")

    result = api.check_updates()

    assert captured["url"] == "https://github.com/johnnyrobot/grader-releases/releases/latest/download/latest.json"
    assert result["update_available"] is True
    assert result["latest_version"] == "0.1.1"


def test_canvas_import_reports_failure_when_download_fails(tmp_path, monkeypatch, api_module):
    """A failed download must never be replaced by whatever file sits at that path.

    `p.exists()` alone cannot tell a fresh download from a leftover, and grafting a
    leftover file's text onto this student's submission would grade them on another
    document.
    """
    from grader.canvas import AttachmentDownload

    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")

    stale = tmp_path / "attachments" / "leftover.pdf"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("a different student's essay")

    monkeypatch.setattr(
        "grader.canvas.list_ungraded",
        lambda course_id, assignment_id, **connection: [
            {"user_id": 555, "body": "", "attachments": [
                {"id": 7, "filename": "essay.pdf", "url": "https://canvas.test/f/7"}
            ]},
        ],
    )
    monkeypatch.setattr(
        "grader.canvas.download_submission_attachments",
        lambda submission, dest_dir, **connection: [
            AttachmentDownload(path=stale, ok=False, error="canvas returned 403")
        ],
    )
    monkeypatch.setattr(
        "grader.parse.parse",
        lambda path, **kwargs: pytest.fail("must not parse a file whose download failed"),
    )

    result = api.canvas_import(
        api.CanvasImportReq(course_id=10, assignment_id=20, download_attachments=True)
    )

    assert result["failures"], "failed download should be reported"
    assert any("555" in str(f.get("user_id")) for f in result["failures"])


def test_override_grade_does_not_mutate_the_shared_exemplar_store(tmp_path, monkeypatch, api_module):
    """The module-level store is shared with in-flight grading threads.

    Reassigning its embedding_model from a request retargets retrieval for a batch
    already running, so handlers must bind a per-request view instead.
    """
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")
    api._save_settings({**api.DEFAULT_SETTINGS, "model_embed": "nomic-embed-text:v1.5"})
    api._exemplar_store.embedding_model = "nomic-embed-text:latest"

    recorded = {}

    def fake_apply_override(override_store, exemplar_store, **kwargs):
        recorded["view_model"] = exemplar_store.embedding_model
        raise RuntimeError("stop after the store is bound")

    monkeypatch.setattr(api, "apply_override", fake_apply_override)

    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_ov", "a1", "s1", "student text", "graded", api._now()),
    )
    db.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, ai_scores_json, "
        "ai_feedback, ai_confidence, ai_flags_json, lane, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("gr_ov", "sub_ov", "a1", json.dumps({"c1": 3.0}), "draft", 0.7, "[]",
         "standard", api._now()),
    )
    db.commit()
    db.close()

    with pytest.raises(RuntimeError):
        api.override_grade("gr_ov", api.OverrideRequest(
            teacher_scores={"c1": 4.0}, teacher_feedback="raised",
        ))

    # The handler must pass a view bound to the saved model...
    assert recorded["view_model"] == "nomic-embed-text:v1.5"
    # ...without touching the shared singleton other threads are using.
    assert api._exemplar_store.embedding_model == "nomic-embed-text:latest"


def test_canvas_url_accepts_https(api_module):
    api = api_module
    settings = api.SettingsState(canvas_url="https://canvas.example.edu/")
    assert settings.canvas_url == "https://canvas.example.edu"


def test_canvas_url_rejects_plaintext_http_for_remote_hosts(api_module):
    """A Canvas token over plaintext HTTP is a credential in the clear."""
    api = api_module
    with pytest.raises(Exception) as exc:
        api.SettingsState(canvas_url="http://canvas.example.edu")
    assert "https" in str(exc.value).lower()


def test_canvas_url_allows_plaintext_http_on_loopback(api_module):
    """Local Canvas dev instances legitimately run without TLS."""
    api = api_module
    for url in ("http://localhost:3000", "http://127.0.0.1:8900"):
        assert api.SettingsState(canvas_url=url).canvas_url == url


def test_canvas_url_rejects_hostnames_that_merely_start_with_localhost(api_module):
    api = api_module
    with pytest.raises(Exception):
        api.SettingsState(canvas_url="http://localhost.attacker.example")


def test_invalid_settings_file_is_logged_before_falling_back(tmp_path, monkeypatch, api_module, caplog):
    """A silent reset to defaults looks identical to 'settings were never saved'."""
    api = api_module
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text("{not valid json")

    with caplog.at_level("WARNING"):
        result = api._load_settings()

    assert result == api.DEFAULT_SETTINGS
    assert any("settings" in r.message.lower() for r in caplog.records)
