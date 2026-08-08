"""Annotated files riding back to Canvas with the grade — PRD line 110 / FR-3.4.

`writeback.py` wrote annotated copies, `canvas.py` could upload a file, and
nothing joined them: `upload_file()` had zero callers, so no feedback file had
ever reached Canvas. It also aimed at the wrong place — `/courses/:c/files` is
shared course content, and a student's annotated submission does not belong
there. These tests pin both the join and the destination.
"""

import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.attachments import AttachmentStore, OutsideAttachmentRoot  # noqa: E402
from grader.canvas import OfflinePostQueue, upload_comment_attachment  # noqa: E402
from grader.db import Database  # noqa: E402
from grader.settings import SettingsPatch  # noqa: E402


# -- The store ---------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return AttachmentStore(Database.memory(), tmp_path)


@pytest.fixture
def a_file(tmp_path):
    path = tmp_path / "feedback.docx"
    path.write_text("annotated")
    return path


def test_an_attached_file_is_listed_for_that_grade(store, a_file):
    store.attach("gr_1", a_file)

    listed = store.for_grade("gr_1")

    assert [a.path for a in listed] == [a_file.resolve()]


def test_attaching_the_same_file_twice_does_not_post_it_twice(store, a_file):
    store.attach("gr_1", a_file)
    store.attach("gr_1", a_file)

    assert len(store.for_grade("gr_1")) == 1


def test_attachments_do_not_leak_between_grades(store, a_file):
    store.attach("gr_1", a_file)

    assert store.for_grade("gr_2") == []


def test_attaching_a_file_that_is_not_there_fails_at_the_moment_of_choosing(store, tmp_path):
    with pytest.raises(FileNotFoundError):
        store.attach("gr_1", tmp_path / "nope.docx")


def test_detaching_removes_it(store, a_file):
    store.attach("gr_1", a_file)

    assert store.detach("gr_1", a_file) is True
    assert store.for_grade("gr_1") == []


def test_detaching_something_never_attached_reports_so(store, a_file):
    assert store.detach("gr_1", a_file) is False


def test_a_file_deleted_after_attaching_is_reported_as_gone(store, a_file):
    store.attach("gr_1", a_file)
    a_file.unlink()

    assert store.for_grade("gr_1")[0].exists is False


# -- The upload --------------------------------------------------------------


def _canvas_upload_transport(seen: dict):
    """Canvas's 3-step flow: announce, POST the bytes, read the id back."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.setdefault("urls", []).append(str(request.url))
        if request.url.path.endswith("/comments/files"):
            return httpx.Response(
                200,
                json={
                    "upload_url": "https://files.example.test/put",
                    "upload_params": {"key": "abc"},
                },
            )
        if str(request.url) == "https://files.example.test/put":
            return httpx.Response(201, json={"id": 4242, "display_name": "feedback.docx"})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def test_an_attachment_uploads_to_the_submission_not_to_course_files(
    monkeypatch, a_file
):
    """`/courses/:c/files` is shared course content. A student's work is not that."""
    seen: dict = {}
    transport = _canvas_upload_transport(seen)
    real_client = httpx.Client

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=transport, **kw)
    )

    file_id = upload_comment_attachment(
        10, 20, "98765", a_file,
        base_url="https://canvas.example.test", token="t",
    )

    assert file_id == 4242
    announce = seen["urls"][0]
    assert announce == (
        "https://canvas.example.test/api/v1/courses/10/assignments/20"
        "/submissions/98765/comments/files"
    )
    assert "/courses/10/files" not in announce


def test_an_upload_that_returns_no_id_is_an_error(monkeypatch, a_file):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/comments/files"):
            return httpx.Response(200, json={"upload_url": "https://f.example.test/p",
                                             "upload_params": {}})
        return httpx.Response(201, json={"display_name": "feedback.docx"})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )

    with pytest.raises(RuntimeError, match="did not return id"):
        upload_comment_attachment(10, 20, "98765", a_file,
                                  base_url="https://canvas.example.test", token="t")


# -- The join, through the post route ----------------------------------------


@pytest.fixture
def api_module():
    sys.modules.pop("api", None)
    module = importlib.import_module("api")
    yield module
    sys.modules.pop("api", None)


def _engine_with_approved_grade(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(
        api, "_attachments", AttachmentStore(Database.memory(), tmp_path)
    )
    monkeypatch.setattr(api, "_post_queue", OfflinePostQueue(Database.memory()))
    api._settings_store().update(
        SettingsPatch(
            canvas_url="https://canvas.example.test",
            canvas_token="saved-test-token",
            data_dir=str(tmp_path / "canvas"),
        )
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
        ("gr_1", "sub_1", "canvas_10_20", json.dumps({"Thesis": 4}), "fb", 0.8,
         "[]", "standard", api._now(), api._now()),
    )
    db.commit()


def test_an_attached_file_is_uploaded_and_sent_with_the_grade(
    tmp_path, monkeypatch, api_module, a_file
):
    api = api_module
    _engine_with_approved_grade(api, tmp_path, monkeypatch)
    api._attachments.attach("gr_1", a_file)

    monkeypatch.setattr(
        "grader.canvas.upload_comment_attachment",
        lambda c, a, u, p, **kw: 4242,
    )
    captured = {}

    def fake_batch(course_id, assignment_id, grade_data, **kw):
        captured["grade_data"] = grade_data
        return {"workflow_state": "completed", "completion": 100}

    monkeypatch.setattr("grader.canvas.post_grades_batch", fake_batch)

    result = api.canvas_post(
        api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["gr_1"]),
        settings=api.current_settings(),
    )

    assert result["posted"] == 1
    assert captured["grade_data"]["98765"]["file_ids"] == [4242]


def test_a_grade_with_no_attachments_sends_no_file_ids(
    tmp_path, monkeypatch, api_module
):
    api = api_module
    _engine_with_approved_grade(api, tmp_path, monkeypatch)
    captured = {}

    def fake_batch(course_id, assignment_id, grade_data, **kw):
        captured["grade_data"] = grade_data
        return {"workflow_state": "completed", "completion": 100}

    monkeypatch.setattr("grader.canvas.post_grades_batch", fake_batch)

    api.canvas_post(
        api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["gr_1"]),
        settings=api.current_settings(),
    )

    assert "file_ids" not in captured["grade_data"]["98765"]


def test_an_attachment_the_teacher_deleted_does_not_block_the_grade(
    tmp_path, monkeypatch, api_module, a_file
):
    """The grade is what has to reach the student."""
    api = api_module
    _engine_with_approved_grade(api, tmp_path, monkeypatch)
    api._attachments.attach("gr_1", a_file)
    a_file.unlink()

    monkeypatch.setattr(
        "grader.canvas.upload_comment_attachment",
        lambda *a, **k: pytest.fail("must not upload a file that is gone"),
    )
    captured = {}

    def fake_batch(course_id, assignment_id, grade_data, **kw):
        captured["grade_data"] = grade_data
        return {"workflow_state": "completed", "completion": 100}

    monkeypatch.setattr("grader.canvas.post_grades_batch", fake_batch)

    result = api.canvas_post(
        api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["gr_1"]),
        settings=api.current_settings(),
    )

    assert result["posted"] == 1
    assert "file_ids" not in captured["grade_data"]["98765"]


def test_ids_already_uploaded_survive_into_the_queue_when_the_post_fails(
    tmp_path, monkeypatch, api_module, a_file
):
    """Canvas took the file, then went down. Flushing must not re-upload it."""
    api = api_module
    _engine_with_approved_grade(api, tmp_path, monkeypatch)
    api._attachments.attach("gr_1", a_file)

    monkeypatch.setattr(
        "grader.canvas.upload_comment_attachment", lambda c, a, u, p, **kw: 4242
    )
    monkeypatch.setattr(
        "grader.canvas.post_grades_batch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    result = api.canvas_post(
        api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["gr_1"]),
        settings=api.current_settings(),
    )

    assert result["queued"] == 1
    assert json.loads(api._post_queue.pending()[0]["file_ids"]) == [4242]


# -- What may be sent --------------------------------------------------------


def test_a_file_outside_the_data_directory_cannot_be_attached(store, tmp_path):
    """Attaching decides which bytes leave the machine.

    Without this bound the route is an arbitrary local file read wired to an
    outbound Canvas upload — `~/.ssh/id_rsa`, or the settings file holding the
    teacher's Canvas token, named as "feedback".
    """
    outside = tmp_path.parent / "secrets.txt"
    outside.write_text("ssh-rsa AAAA...")

    with pytest.raises(OutsideAttachmentRoot):
        store.attach("gr_1", outside)

    assert store.for_grade("gr_1") == []


def test_a_traversal_path_cannot_escape_the_data_directory(store, tmp_path):
    outside = tmp_path.parent / "secrets.txt"
    outside.write_text("ssh-rsa AAAA...")

    with pytest.raises(OutsideAttachmentRoot):
        store.attach("gr_1", tmp_path / ".." / "secrets.txt")


def test_a_symlink_pointing_out_of_the_data_directory_cannot_be_attached(
    store, tmp_path
):
    outside = tmp_path.parent / "secrets.txt"
    outside.write_text("ssh-rsa AAAA...")
    link = tmp_path / "feedback.docx"
    link.symlink_to(outside)

    with pytest.raises(OutsideAttachmentRoot):
        store.attach("gr_1", link)
