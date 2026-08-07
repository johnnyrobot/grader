import json
import importlib
import sqlite3
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.settings import SettingsPatch  # noqa: E402  (needs the path insert above)


def _save_settings(api, **values):
    """Seed the settings file the way the settings route does.

    Unnamed fields keep their defaults, so a test states only what it depends on.
    """
    return api._settings_store().update(SettingsPatch(**values))


@pytest.fixture
def api_module():
    """Import the engine fresh per test.

    No pydantic_ai stubbing: api.py imports it only inside _run_grading_job, so
    loading the engine no longer drags in the LLM library. Tests that grade pass
    a Judge instead.
    """
    sys.modules.pop("api", None)
    module = importlib.import_module("api")
    yield module
    sys.modules.pop("api", None)


def test_canvas_post_uses_canvas_user_id_from_imported_submission(tmp_path, monkeypatch, api_module):
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")
    _save_settings(
        api,
        canvas_url="https://canvas.example.test",
        canvas_token="saved-test-token",
        data_dir=str(tmp_path / "canvas"),
    )

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

    result = api.canvas_post(
        api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["grade_local"]),
        settings=api.current_settings(),
    )

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

    with pytest.raises(Exception) as exc_info:
        api.canvas_post(
            api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["grade_local"]),
            settings=api.current_settings(),
        )
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
        api.CanvasImportReq(course_id=10, assignment_id=20, download_attachments=True),
        settings=api.current_settings(),
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

    result = api.grade_batch(
        api.GradeBatch(assignment_id="canvas_10_20", submission_ids=["sub_keep"]),
        settings=api.current_settings(),
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
    _save_settings(
        api,
        canvas_url="https://canvas.example.test",
        canvas_token="saved-test-token",
        data_dir=str(attachment_root),
        model_text="grader-text:test",
        model_vision="grader-vision:test",
        model_embed="nomic-embed-text:test",
        review_first_threshold=0.6,
        auto_approve_threshold=0.9,
    )

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

    imported = api.canvas_import(
        api.CanvasImportReq(course_id=10, assignment_id=20), settings=api.current_settings()
    )

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
    result = api.grade_batch(
        api.GradeBatch(assignment_id="canvas_10_20"), settings=api.current_settings()
    )

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
    _save_settings(
        api,
        model_vision="grader-vision:test",
        data_dir=str(tmp_path / "canvas"),
    )
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

    result = api.transcribe_images(
        api.TranscribeReq(file_paths=[str(image_path)]), settings=api.current_settings()
    )

    assert result["text"] == "synthetic transcription"
    assert captured["model"] == "grader-vision:test"
    assert captured["paths"] == [image_path]


def test_settings_are_validated_masked_and_written_owner_only(tmp_path, monkeypatch, api_module):
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")

    result = api.update_settings(SettingsPatch(
        canvas_url="https://canvas.example.test/",
        canvas_token="1234567890abcdef",
        data_dir=str(tmp_path / "attachments"),
        review_first_threshold=0.65,
        auto_approve_threshold=0.9,
    ))

    assert result["settings"]["canvas_url"] == "https://canvas.example.test"
    assert result["settings"]["canvas_token_masked"] == "1234...cdef"
    assert "canvas_token" not in result["settings"]
    assert api.get_settings(api.current_settings())["canvas_token_masked"] == "1234...cdef"
    assert api.SETTINGS_FILE.stat().st_mode & 0o777 == 0o600

    # A rejected patch is the teacher's input being wrong, not the engine failing.
    with pytest.raises(HTTPException) as excinfo:
        api.update_settings(SettingsPatch(
            review_first_threshold=0.95, auto_approve_threshold=0.9,
        ))
    assert excinfo.value.status_code == 400
    assert "needs-review threshold" in str(excinfo.value.detail)

    # ...and it leaves the saved settings exactly as they were.
    assert api.current_settings().review_first_threshold == 0.65

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
        api.CanvasImportReq(course_id=10, assignment_id=20, download_attachments=True),
        settings=api.current_settings(),
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
    _save_settings(api, model_embed="nomic-embed-text:v1.5")
    api._exemplar_store.embedding_model = "nomic-embed-text:latest"

    recorded = {}

    def fake_apply_override(override_store, exemplar_store, **kwargs):
        recorded["view_model"] = exemplar_store.embedding_model
        raise RuntimeError("stop after the store is bound")

    # api.py no longer calls apply_override; ApprovalStore.override() does.
    monkeypatch.setattr("grader.approvals.apply_override", fake_apply_override)
    monkeypatch.setattr(api._rubric_store, "get",
                        lambda title: types.SimpleNamespace(total_points=5.0))

    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_ov", "a1", "s1", "student text", "graded", api._now()),
    )
    db.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, rubric_title, "
        "ai_scores_json, ai_feedback, ai_confidence, ai_flags_json, lane, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("gr_ov", "sub_ov", "a1", "Essay", json.dumps({"c1": 3.0}), "draft", 0.7, "[]",
         "standard", api._now()),
    )
    db.commit()

    with pytest.raises(RuntimeError):
        api.override_grade("gr_ov", api.OverrideRequest(
            teacher_scores={"c1": 4.0}, teacher_feedback="raised",
        ), settings=api.current_settings())

    # The handler must pass a view bound to the saved model...
    assert recorded["view_model"] == "nomic-embed-text:v1.5"
    # ...without touching the shared singleton other threads are using.
    assert api._exemplar_store.embedding_model == "nomic-embed-text:latest"


# ---------------------------------------------------------------------------
# Which rubric a submission is graded against
# ---------------------------------------------------------------------------

def _seed_submission(api, assignment_id="canvas_10_20", sub_id="sub_1"):
    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (sub_id, assignment_id, sub_id, "student text", "imported", api._now()),
    )
    db.commit()


def test_grade_batch_refuses_to_pick_between_rubrics(tmp_path, monkeypatch, api_module):
    """Grading against the wrong rubric is silent, so ambiguity must be loud.

    Before this guard, both grade_batch and override_grade took whichever rubric
    sorted first alphabetically, regardless of the assignment.
    """
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api._rubric_store, "list",
                        lambda: [{"title": "Essay 1"}, {"title": "Essay 2"}])
    _seed_submission(api)

    with pytest.raises(HTTPException) as excinfo:
        api.grade_batch(
            api.GradeBatch(assignment_id="canvas_10_20"), settings=api.current_settings()
        )

    assert excinfo.value.status_code == 400
    assert "Essay 1" in excinfo.value.detail and "Essay 2" in excinfo.value.detail


def test_grade_batch_grades_against_the_named_rubric(tmp_path, monkeypatch, api_module):
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api._rubric_store, "list",
                        lambda: [{"title": "Essay 1"}, {"title": "Essay 2"}])
    monkeypatch.setattr(api._rubric_store, "get",
                        lambda title: types.SimpleNamespace(title=title, criteria=[]))

    captured = {}

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            captured["args"] = args

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(api.threading, "Thread", FakeThread)
    _seed_submission(api)

    result = api.grade_batch(
        api.GradeBatch(assignment_id="canvas_10_20", rubric_title="Essay 2"),
        settings=api.current_settings(),
    )

    assert result["total"] == 1
    assert captured["args"][3].title == "Essay 2"


def test_override_scales_against_the_rubric_the_grade_was_produced_against(
    tmp_path, monkeypatch, api_module
):
    """A second saved rubric must not change how an existing grade is scaled."""
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(api._rubric_store, "list",
                        lambda: [{"title": "Essay 1"}, {"title": "Essay 2"}])
    monkeypatch.setattr(
        api._rubric_store, "get",
        lambda title: types.SimpleNamespace(total_points={"Essay 1": 5.0, "Essay 2": 100.0}[title]),
    )

    recorded = {}

    def fake_apply_override(override_store, exemplar_store, **kwargs):
        recorded["max_total"] = kwargs["max_total"]
        raise RuntimeError("stop once the rubric is resolved")

    # api.py no longer calls apply_override; ApprovalStore.override() does.
    monkeypatch.setattr("grader.approvals.apply_override", fake_apply_override)

    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_ov", "a1", "s1", "student text", "graded", api._now()),
    )
    db.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, rubric_title, "
        "ai_scores_json, ai_feedback, ai_confidence, ai_flags_json, lane, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("gr_ov", "sub_ov", "a1", "Essay 2", json.dumps({"c1": 3.0}), "draft", 0.7, "[]",
         "standard", api._now()),
    )
    db.commit()

    with pytest.raises(RuntimeError):
        api.override_grade("gr_ov", api.OverrideRequest(
            teacher_scores={"c1": 4.0}, teacher_feedback="raised",
        ), settings=api.current_settings())

    # "Essay 2", not the alphabetically-first "Essay 1".
    assert recorded["max_total"] == 100.0


def test_grade_records_gains_rubric_title_on_an_older_database(tmp_path, monkeypatch, api_module):
    """Databases made by an earlier build predate the column and must gain it."""
    api = api_module
    db_path = tmp_path / "grader_engine.db"
    monkeypatch.setattr(api, "DB_PATH", db_path)

    # The real grade_records as it shipped, minus rubric_title.
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        "CREATE TABLE grade_records ("
        "id TEXT PRIMARY KEY, submission_id TEXT NOT NULL, assignment_id TEXT NOT NULL, "
        "rubric_version INTEGER NOT NULL DEFAULT 1, ai_scores_json TEXT, ai_feedback TEXT, "
        "ai_confidence REAL, ai_flags_json TEXT, teacher_scores_json TEXT, "
        "teacher_feedback TEXT, lane TEXT, approved_at TEXT, posted_at TEXT, "
        "created_at TEXT NOT NULL);"
    )
    legacy.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, created_at) "
        "VALUES ('gr_old', 'sub_old', 'a1', '2026-01-01T00:00:00Z')"
    )
    legacy.commit()
    legacy.close()

    db = api._db()
    columns = {r["name"] for r in db.execute("PRAGMA table_info(grade_records)")}
    row = db.execute("SELECT * FROM grade_records WHERE id = 'gr_old'").fetchone()

    assert "rubric_title" in columns
    assert row["rubric_title"] is None  # existing rows survive, unattributed


# ---------------------------------------------------------------------------
# Running a grading job
# ---------------------------------------------------------------------------

def _fake_draft(schema):
    return schema.GradeDraft(
        criterion_scores=[schema.CriterionScore(criterion_id="c1", score=4.0, rationale="ok")],
        feedback="feedback", confidence=0.9, flags=[],
    )


class FakeJudge:
    """A Judge that grades without a model. Nothing here touches pydantic_ai."""

    def __init__(self):
        from grader import schema as schema_mod

        self.graded = []
        self._schema = schema_mod

    def grade(self, rubric, submission, *, anchors="", materials=""):
        self.graded.append(submission)
        return self._schema.GradeRun(
            draft=_fake_draft(self._schema), mode="tool", model="fake",
            seconds=0.1, retries=0,
        )


def _stub_grading(api, monkeypatch, *, anchor_count=0):
    """Run the pipeline without Ollama: a fake Judge, canned triage, no exemplars."""
    # triage runs for real: FakeJudge grades at 0.9 confidence, above the
    # threshold, so no consistency vote fires and no model is needed.
    retriever = types.SimpleNamespace(retrieved=[])
    retriever.count = lambda assignment_id: anchor_count
    retriever.retrieve = lambda *a, **kw: retriever.retrieved.append(a) or []
    monkeypatch.setattr(api._exemplar_store, "for_model", lambda model: retriever)
    return retriever


def test_grading_job_accounts_for_every_submission_it_was_given(
    tmp_path, monkeypatch, api_module
):
    """A submission that can't be read is an outcome, not a silent omission.

    The desktop app tracks a batch by done/total and renders the results list, so a
    dropped submission leaves the job looking unfinished and the teacher without a
    row explaining why.
    """
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    _stub_grading(api, monkeypatch)

    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_real", "a1", "s1", "student text", "imported", api._now()),
    )
    db.commit()

    api._jobs["job_1"] = {"status": "queued", "total": 2, "done": 0, "assignment_id": "a1"}
    api._run_grading_job(
        "job_1", "a1", ["sub_real", "sub_vanished"],
        rubric=types.SimpleNamespace(title="Essay", criteria=[]),
        model="grader-text:8b", materials="",
        low_confidence=0.6, high_confidence=0.9, embedding_model="nomic-embed-text:v1.5",
        judge=FakeJudge(),
    )

    job = api._jobs["job_1"]
    assert job["done"] == 2
    assert len(job["results"]) == 2
    missing = [r for r in job["results"] if r["submission_id"] == "sub_vanished"]
    assert missing and missing[0]["error"]


def test_grading_job_defers_to_the_single_cold_start_threshold(
    tmp_path, monkeypatch, api_module
):
    """How many exemplars unlock AI drafting is one product rule, defined once.

    calibration.COLD_START_MIN is that definition. A second copy in the grading
    loop can drift from it silently, since both start at 3.
    """
    from grader import calibration

    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    retriever = _stub_grading(api, monkeypatch, anchor_count=4)
    monkeypatch.setattr(calibration, "COLD_START_MIN", 5)

    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_1", "a1", "s1", "student text", "imported", api._now()),
    )
    db.commit()

    api._jobs["job_c"] = {"status": "queued", "total": 1, "done": 0, "assignment_id": "a1"}
    api._run_grading_job(
        "job_c", "a1", ["sub_1"],
        rubric=types.SimpleNamespace(title="Essay", criteria=[]),
        model="grader-text:8b", materials="",
        low_confidence=0.6, high_confidence=0.9, embedding_model="nomic-embed-text:v1.5",
        judge=FakeJudge(),
    )

    # 4 exemplars, threshold raised to 5: still cold, so no anchors are retrieved.
    assert retriever.retrieved == []


def test_canvas_post_will_not_post_the_same_grade_twice(tmp_path, monkeypatch, api_module):
    """Re-posting overwrites the score harmlessly but appends the comment again.

    The student sees their feedback duplicated, once per retry, and nothing in the
    request tells the teacher it already went.
    """
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")

    calls = []

    def fake_post_grades_batch(course_id, assignment_id, grade_data, **connection):
        calls.append(grade_data)
        return {"workflow_state": "completed", "completion": 100}

    monkeypatch.setattr("grader.canvas.post_grades_batch", fake_post_grades_batch)

    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_p", "canvas_10_20", "98765", "student text", "graded", api._now()),
    )
    db.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, ai_scores_json, "
        "ai_feedback, ai_confidence, ai_flags_json, lane, approved_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("gr_p", "sub_p", "canvas_10_20", json.dumps({"c1": 4.0}), "feedback", 0.8, "[]",
         "standard", api._now(), api._now()),
    )
    db.commit()

    first = api.canvas_post(
        api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["gr_p"]),
        settings=api.current_settings(),
    )
    assert first["posted"] == 1

    with pytest.raises(HTTPException) as excinfo:
        api.canvas_post(
            api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["gr_p"]),
            settings=api.current_settings(),
        )

    assert "already posted" in str(excinfo.value.detail)
    assert len(calls) == 1  # Canvas heard about this grade exactly once
