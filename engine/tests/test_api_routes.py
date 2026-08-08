"""The engine over HTTP, through the ASGI stack it actually runs on.

Every other engine test calls handlers as plain functions. That skips the parts
FastAPI owns: the bearer-token dependency, request-body validation, path and
query coercion, status codes, and which route a URL resolves to. None of it had
ever run under test, so a route could be unreachable, unauthenticated, or
answering 500 where it means 400 and the suite would stay green.
"""

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.db import Database  # noqa: E402  (needs the path insert above)
from grader.exemplars import ExemplarStore  # noqa: E402
from grader.overrides import OverrideStore  # noqa: E402
from grader.rubrics import RubricStore  # noqa: E402


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A fresh engine whose every store lives under tmp_path."""
    sys.modules.pop("api", None)
    module = importlib.import_module("api")

    monkeypatch.setattr(module, "DB_PATH", tmp_path / "engine.db")
    monkeypatch.setattr(module, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(module, "_exemplar_store", ExemplarStore(Database.memory()))
    monkeypatch.setattr(module, "_override_store", OverrideStore(Database.memory()))
    monkeypatch.setattr(module, "_rubric_store", RubricStore(Database.memory()))

    yield module
    sys.modules.pop("api", None)


@pytest.fixture
def client(api):
    with TestClient(api.app) as c:
        yield c


@pytest.fixture
def auth(api):
    return {"Authorization": f"Bearer {api.ENGINE_TOKEN}"}


# ---------------------------------------------------------------------------
# The bearer token — the only thing standing in front of a teacher's data
# ---------------------------------------------------------------------------

PROTECTED = [
    ("GET", "/api/settings"),
    ("GET", "/api/rubrics"),
    ("GET", "/api/queue"),
    ("GET", "/api/audit"),
    ("POST", "/api/import"),
    ("POST", "/api/grade/batch"),
    ("PUT", "/api/settings"),
]


@pytest.mark.parametrize("method,path", PROTECTED, ids=lambda v: str(v))
def test_a_request_without_a_token_is_refused(client, method, path):
    assert client.request(method, path, json={}).status_code == 401


@pytest.mark.parametrize("method,path", PROTECTED, ids=lambda v: str(v))
def test_a_request_with_the_wrong_token_is_refused(client, method, path):
    response = client.request(
        method, path, json={}, headers={"Authorization": "Bearer not-the-token"}
    )
    assert response.status_code == 403


def test_a_token_without_the_bearer_scheme_is_refused(client, api):
    response = client.get("/api/settings", headers={"Authorization": api.ENGINE_TOKEN})

    assert response.status_code == 401


def test_health_is_reachable_without_a_token(client):
    """The shell polls this to know the sidecar is up, before it has the token."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Request validation happens before a handler sees the body
# ---------------------------------------------------------------------------

def test_a_body_missing_a_required_field_is_rejected_as_unprocessable(client, auth):
    response = client.post("/api/import", json={"submissions": []}, headers=auth)

    assert response.status_code == 422


def test_an_uncapped_model_tag_is_rejected_at_the_boundary(client, auth):
    """Raw base tags default to context windows that eat tens of gigabytes."""
    response = client.post(
        "/api/grade/batch",
        json={"assignment_id": "a1", "model": "qwen3:8b"},
        headers=auth,
    )

    assert response.status_code == 422
    assert "grader-text" in response.text


def test_a_query_parameter_of_the_wrong_type_is_rejected(client, auth):
    response = client.get("/api/audit?limit=not-a-number", headers=auth)

    assert response.status_code == 422


def test_an_unknown_path_is_a_404_not_a_crash(client, auth):
    assert client.get("/api/no-such-route", headers=auth).status_code == 404


# ---------------------------------------------------------------------------
# Routing: two routes that once shadowed each other
# ---------------------------------------------------------------------------

def test_updates_check_resolves_to_its_own_handler(client, auth, monkeypatch):
    """/api/audit/{entity} once swallowed /api/updates/check."""
    class Response:
        status_code = 200

        def json(self):
            return {"version": "0.1.1", "notes": "", "pub_date": ""}

    monkeypatch.setattr("httpx.get", lambda url, timeout: Response())
    monkeypatch.setenv("GRADER_APP_VERSION", "0.1.0")

    body = client.get("/api/updates/check", headers=auth).json()

    assert body["update_available"] is True


# ---------------------------------------------------------------------------
# Settings over the wire: the token must never come back out
# ---------------------------------------------------------------------------

def test_settings_round_trip_never_returns_the_raw_token(client, auth, tmp_path):
    saved = client.put("/api/settings", headers=auth, json={
        "canvas_url": "https://canvas.example.test/",
        "canvas_token": "1234567890abcdef",
        "data_dir": str(tmp_path / "attachments"),
    })

    assert saved.status_code == 200
    assert "1234567890abcdef" not in saved.text

    fetched = client.get("/api/settings", headers=auth)

    assert fetched.status_code == 200
    assert "1234567890abcdef" not in fetched.text
    assert fetched.json()["canvas_token_masked"] == "1234...cdef"
    assert fetched.json()["canvas_url"] == "https://canvas.example.test"


def test_settings_the_teacher_typed_wrong_come_back_as_400(client, auth):
    response = client.put("/api/settings", headers=auth, json={
        "review_first_threshold": 0.95, "auto_approve_threshold": 0.9,
    })

    assert response.status_code == 400
    assert "needs-review threshold" in response.json()["detail"]


def test_a_plaintext_canvas_url_is_refused_over_the_wire(client, auth):
    response = client.put("/api/settings", headers=auth,
                          json={"canvas_url": "http://canvas.example.edu"})

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# A few handlers end to end
# ---------------------------------------------------------------------------

def test_a_pasted_rubric_is_stored_and_listed(client, auth):
    created = client.post("/api/rubrics", headers=auth, json={
        "rubric_text": "# Essay\n## Thesis (10 pts)\n- 10: Excellent\n",
    })

    assert created.status_code == 200
    assert created.json()["title"] == "Essay"
    assert created.json()["criteria_count"] == 1

    listed = client.get("/api/rubrics", headers=auth).json()
    assert [r["title"] for r in listed] == ["Essay"]


def test_an_empty_paste_is_a_400_not_a_500(client, auth):
    """A teacher clearing the box and saving must not look like an engine fault."""
    response = client.post("/api/rubrics", headers=auth, json={"rubric_text": "   "})

    assert response.status_code == 400


def test_a_rubric_that_was_never_saved_is_a_404(client, auth):
    assert client.get("/api/rubrics/Nothing", headers=auth).status_code == 404


def test_importing_submissions_puts_them_on_the_queue(client, auth):
    imported = client.post("/api/import", headers=auth, json={
        "assignment_id": "a1",
        "submissions": [{"student_ref": "s1", "text": "first"},
                        {"student_ref": "s2", "text": "second"}],
    })

    assert imported.status_code == 200
    assert imported.json()["imported"] == 2

    audit = client.get("/api/audit?entity=a1", headers=auth).json()
    assert [e["event"] for e in audit] == ["import"]
    assert audit[0]["payload"]["count"] == 2


def test_grading_an_assignment_with_nothing_imported_is_a_400(client, auth):
    client.post("/api/rubrics", headers=auth, json={
        "rubric_text": "# Essay\n## Thesis (10 pts)\n- 10: Excellent\n",
    })

    response = client.post("/api/grade/batch", headers=auth,
                           json={"assignment_id": "a1", "rubric_title": "Essay"})

    assert response.status_code == 400
    assert "no submissions" in response.json()["detail"]


def test_approving_a_grade_that_does_not_exist_is_a_404(client, auth):
    response = client.post("/api/grades/gr_nothing/approve", headers=auth)

    assert response.status_code == 404


def test_calibration_reports_cold_start_for_an_untouched_assignment(client, auth):
    response = client.get("/api/calibration/a1", headers=auth)

    assert response.status_code == 200
    assert response.json()["cold_start"]["unlocked"] is False


def test_the_queue_of_an_assignment_with_no_grades_is_empty(client, auth):
    response = client.get("/api/queue?assignment_id=a1", headers=auth)

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_the_offline_post_queue_is_empty_on_a_fresh_engine(client, auth, api, monkeypatch):
    from grader.canvas import OfflinePostQueue

    monkeypatch.setattr(api, "_post_queue", OfflinePostQueue(Database.memory()))

    response = client.get("/api/canvas/post-queue", headers=auth)

    assert response.status_code == 200
    assert response.json() == {"pending": 0, "items": []}


def test_flushing_an_empty_queue_reports_nothing_to_do(client, auth, api, monkeypatch):
    from grader.canvas import OfflinePostQueue

    monkeypatch.setattr(api, "_post_queue", OfflinePostQueue(Database.memory()))

    response = client.post("/api/canvas/flush", headers=auth)

    assert response.status_code == 200
    assert response.json() == {
        "posted": 0, "failed": 0, "abandoned": 0, "remaining": 0,
    }


def test_posting_with_canvas_unreachable_queues_over_the_wire(
    client, auth, api, monkeypatch
):
    """The offline path, through the ASGI stack rather than a direct call.

    A direct handler call supplies its own Settings; this proves the route
    resolves, authenticates, and queues with the injected ones.
    """
    import json as _json

    from grader.canvas import OfflinePostQueue

    monkeypatch.setattr(api, "_post_queue", OfflinePostQueue(Database.memory()))
    client.post("/api/settings", headers=auth, json={
        "canvas_url": "https://canvas.example.test",
        "canvas_token": "1234567890abcdef",
    })
    monkeypatch.setattr(
        "grader.canvas.post_grades_batch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_1", "canvas_10_20", "98765", "text", "graded", api._now()),
    )
    db.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, ai_scores_json, "
        "ai_feedback, ai_confidence, ai_flags_json, lane, approved_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("gr_1", "sub_1", "canvas_10_20", _json.dumps({"Thesis": 4}), "fb", 0.8,
         "[]", "standard", api._now(), api._now()),
    )
    db.commit()

    response = client.post("/api/canvas/post", headers=auth, json={
        "course_id": 10, "assignment_id": 20, "grade_ids": ["gr_1"],
    })

    assert response.status_code == 200
    assert response.json()["queued"] == 1
    assert client.get("/api/canvas/post-queue", headers=auth).json()["pending"] == 1


def _seed_grade(api):
    import json as _json
    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_a", "canvas_10_20", "98765", "text", "graded", api._now()),
    )
    db.execute(
        "INSERT INTO grade_records (id, submission_id, assignment_id, ai_scores_json, "
        "ai_feedback, ai_confidence, ai_flags_json, lane, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("gr_a", "sub_a", "canvas_10_20", _json.dumps({"T": 4}), "fb", 0.8,
         "[]", "standard", api._now()),
    )
    db.commit()


def test_attaching_a_file_to_a_grade_lists_it_back(client, auth, api, tmp_path, monkeypatch):
    from grader.attachments import AttachmentStore

    monkeypatch.setattr(api, "_attachments", AttachmentStore(Database.memory()))
    _seed_grade(api)
    doc = tmp_path / "feedback.docx"
    doc.write_text("annotated")

    posted = client.post("/api/grades/gr_a/attachments", headers=auth,
                         json={"path": str(doc)})
    assert posted.status_code == 200

    listed = client.get("/api/grades/gr_a/attachments", headers=auth)
    assert listed.status_code == 200
    assert [a["path"] for a in listed.json()["attachments"]] == [str(doc.resolve())]
    assert listed.json()["attachments"][0]["exists"] is True


def test_attaching_to_a_grade_that_does_not_exist_is_a_404(
    client, auth, api, tmp_path, monkeypatch
):
    from grader.attachments import AttachmentStore

    monkeypatch.setattr(api, "_attachments", AttachmentStore(Database.memory()))
    doc = tmp_path / "feedback.docx"
    doc.write_text("annotated")

    response = client.post("/api/grades/gr_nothing/attachments", headers=auth,
                           json={"path": str(doc)})

    assert response.status_code == 404


def test_attaching_a_missing_file_is_a_404(client, auth, api, tmp_path, monkeypatch):
    from grader.attachments import AttachmentStore

    monkeypatch.setattr(api, "_attachments", AttachmentStore(Database.memory()))
    _seed_grade(api)

    response = client.post("/api/grades/gr_a/attachments", headers=auth,
                           json={"path": str(tmp_path / "gone.docx")})

    assert response.status_code == 404
