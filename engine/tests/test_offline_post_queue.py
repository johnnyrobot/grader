"""What the offline post queue does, and what nothing asks it to do.

`OfflinePostQueue` is wired into engine/api.py and exposed through two routes,
but until these tests it had no coverage at all. Its docstring promises the
behaviour `docs/PRD.md` FR-3.5 states as a requirement — "posts queue until
connectivity" — so the queue's own contract is tested here, and the route that
is supposed to fill it is tested alongside.

The approval invariant travels through this file: an entry only reaches the
queue after `_approvals.postable()` has cleared it, so flushing one cannot post
a grade the teacher never approved.
"""

import json
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.canvas import (  # noqa: E402  (needs the path insert above)
    MAX_POST_ATTEMPTS,
    OfflinePostQueue,
)
from grader.db import Database  # noqa: E402
from grader.settings import SettingsPatch  # noqa: E402


@pytest.fixture
def queue():
    return OfflinePostQueue(Database.memory())


def _enqueue(queue, user_id="98765", **kwargs):
    return queue.enqueue("10", "20", user_id, "7", **kwargs)


# -- The queue's own contract ------------------------------------------------


def test_an_enqueued_post_shows_up_as_pending(queue):
    queue_id = _enqueue(queue)

    pending = queue.pending()

    assert len(pending) == 1
    assert pending[0]["id"] == queue_id
    assert pending[0]["user_id"] == "98765"
    assert pending[0]["grade"] == "7"


def test_enqueue_records_the_approval_time_it_was_given(queue):
    _enqueue(queue, approved_at="2026-08-07T00:00:00+00:00")

    assert queue.pending()[0]["approved_at"] == "2026-08-07T00:00:00+00:00"


def test_marking_done_removes_the_entry(queue):
    queue_id = _enqueue(queue)

    queue.mark_done(queue_id)

    assert queue.pending() == []


def test_marking_failed_keeps_the_entry_and_counts_the_attempt(queue):
    queue_id = _enqueue(queue)

    queue.mark_failed(queue_id, "canvas unreachable")

    pending = queue.pending()
    assert len(pending) == 1
    assert pending[0]["attempts"] == 1
    assert pending[0]["last_error"] == "canvas unreachable"


def test_a_long_error_is_truncated_so_one_failure_cannot_bloat_the_row(queue):
    queue_id = _enqueue(queue)

    queue.mark_failed(queue_id, "x" * 1000)

    assert len(queue.pending()[0]["last_error"]) == 300


def test_flush_posts_each_pending_entry_and_clears_it(queue, monkeypatch):
    posted = []

    def fake_post_grade(course_id, assignment_id, user_id, **kwargs):
        posted.append((course_id, assignment_id, user_id, kwargs["grade"]))

    monkeypatch.setattr("grader.canvas.post_grade", fake_post_grade)
    _enqueue(queue, user_id="1")
    _enqueue(queue, user_id="2")

    result = queue.flush()

    assert result == {"posted": 2, "failed": 0, "abandoned": 0, "remaining": 0}
    assert posted == [("10", "20", "1", "7"), ("10", "20", "2", "7")]
    assert queue.pending() == []


def test_flush_rehydrates_the_json_columns_before_posting(queue, monkeypatch):
    seen = {}

    def fake_post_grade(course_id, assignment_id, user_id, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr("grader.canvas.post_grade", fake_post_grade)
    _enqueue(
        queue,
        rubric_assessment=json.dumps({"crit_1": {"points": 4}}),
        file_ids=json.dumps([11, 12]),
        comment="nice work",
    )

    queue.flush()

    assert seen["rubric_assessment"] == {"crit_1": {"points": 4}}
    assert seen["comment_file_ids"] == [11, 12]
    assert seen["comment"] == "nice work"


def test_a_failing_post_stays_queued_so_it_can_be_retried(queue, monkeypatch):
    def fake_post_grade(*args, **kwargs):
        raise RuntimeError("canvas unreachable")

    monkeypatch.setattr("grader.canvas.post_grade", fake_post_grade)
    _enqueue(queue)

    result = queue.flush()

    assert result == {"posted": 0, "failed": 1, "abandoned": 0, "remaining": 1}
    assert queue.pending()[0]["attempts"] == 1


def test_one_failing_entry_does_not_block_the_others(queue, monkeypatch):
    def fake_post_grade(course_id, assignment_id, user_id, **kwargs):
        if user_id == "bad":
            raise RuntimeError("canvas rejected this one")

    monkeypatch.setattr("grader.canvas.post_grade", fake_post_grade)
    _enqueue(queue, user_id="bad")
    _enqueue(queue, user_id="good")

    result = queue.flush()

    assert result == {"posted": 1, "failed": 1, "abandoned": 0, "remaining": 1}
    assert [row["user_id"] for row in queue.pending()] == ["bad"]


def test_an_entry_stops_being_retried_once_it_has_burned_its_attempts(queue, monkeypatch):
    """A grade Canvas will never accept must not re-post on every flush forever."""
    tries = []

    def fake_post_grade(course_id, assignment_id, user_id, **kwargs):
        tries.append(user_id)
        raise RuntimeError("student is not enrolled")

    monkeypatch.setattr("grader.canvas.post_grade", fake_post_grade)
    _enqueue(queue)

    for _ in range(MAX_POST_ATTEMPTS + 3):
        result = queue.flush()

    assert len(tries) == MAX_POST_ATTEMPTS
    assert result == {"posted": 0, "failed": 0, "abandoned": 1, "remaining": 1}


def test_an_abandoned_entry_stays_visible_with_its_last_error(queue, monkeypatch):
    """Giving up on retrying is not the same as discarding it silently."""
    monkeypatch.setattr(
        "grader.canvas.post_grade",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("student is not enrolled")),
    )
    _enqueue(queue)

    for _ in range(MAX_POST_ATTEMPTS + 1):
        queue.flush()

    pending = queue.pending()
    assert len(pending) == 1
    assert pending[0]["attempts"] == MAX_POST_ATTEMPTS
    assert pending[0]["last_error"] == "student is not enrolled"


# -- The route that is supposed to fill it -----------------------------------


@pytest.fixture
def api_module():
    sys.modules.pop("api", None)
    module = importlib.import_module("api")
    yield module
    sys.modules.pop("api", None)


def _seed_approved_grade(api, *, student_ref="98765"):
    db = api._db()
    db.execute(
        "INSERT INTO submissions (id, assignment_id, student_ref, text, state, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("sub_local", "canvas_10_20", student_ref, "student text", "graded", api._now()),
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


def test_an_approved_grade_queues_when_canvas_is_unreachable(
    tmp_path, monkeypatch, api_module
):
    """FR-3.5: "posts queue until connectivity".

    Canvas being down is the ordinary offline case, not a lost approval. The
    teacher already approved this grade; the post must survive the outage.
    """
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(api, "_post_queue", OfflinePostQueue(Database.memory()))
    api._settings_store().update(
        SettingsPatch(
            canvas_url="https://canvas.example.test",
            canvas_token="saved-test-token",
            data_dir=str(tmp_path / "canvas"),
        )
    )

    def canvas_is_down(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("grader.canvas.post_grades_batch", canvas_is_down)
    _seed_approved_grade(api)

    result = api.canvas_post(
        api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["grade_local"]),
        settings=api.current_settings(),
    )

    assert result["queued"] == 1
    assert result["posted"] == 0
    pending = api._post_queue.pending()
    assert len(pending) == 1
    assert pending[0]["user_id"] == "98765"
    assert pending[0]["grade"] == "7"


def test_a_queued_post_is_not_marked_as_posted(tmp_path, monkeypatch, api_module):
    """Queued is not posted. mark_posted must wait for Canvas to accept it."""
    api = api_module
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "grader_engine.db")
    monkeypatch.setattr(api, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(api, "_post_queue", OfflinePostQueue(Database.memory()))
    api._settings_store().update(
        SettingsPatch(
            canvas_url="https://canvas.example.test",
            canvas_token="saved-test-token",
            data_dir=str(tmp_path / "canvas"),
        )
    )
    monkeypatch.setattr(
        "grader.canvas.post_grades_batch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )
    _seed_approved_grade(api)

    api.canvas_post(
        api.CanvasPostReq(course_id=10, assignment_id=20, grade_ids=["grade_local"]),
        settings=api.current_settings(),
    )

    row = api._db().execute(
        "SELECT posted_at FROM grade_records WHERE id = ?", ("grade_local",)
    ).fetchone()
    assert row["posted_at"] is None
