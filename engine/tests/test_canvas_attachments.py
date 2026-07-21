"""Downloaded Canvas attachments must be per-attachment and honestly reported.

Every student in an assignment downloads into one shared directory, and students name
files generically ("essay.pdf"). Two defects compound there: a colliding basename
overwrites another student's original, and the caller infers success from
`path.exists()`, which a leftover file can satisfy after a download has actually failed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

pytest = __import__("pytest")

from grader import canvas


def _submission(att_id, filename, url="https://canvas.test/files/1"):
    return {"attachments": [{"id": att_id, "filename": filename, "url": url}]}


def _ok_download(url, dest, *, token=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(url)
    return dest


def _failing_download(url, dest, *, token=None):
    raise RuntimeError("canvas returned 403")


def test_same_filename_from_two_submissions_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr("grader.canvas.download_attachment", _ok_download)

    a = canvas.download_submission_attachments(
        _submission(11, "essay.pdf", "student-a-bytes"), tmp_path
    )
    b = canvas.download_submission_attachments(
        _submission(22, "essay.pdf", "student-b-bytes"), tmp_path
    )

    assert a[0].path != b[0].path
    assert a[0].path.read_text() == "student-a-bytes"
    assert b[0].path.read_text() == "student-b-bytes"


def test_failed_download_is_not_masked_by_a_leftover_file(tmp_path, monkeypatch):
    sub = _submission(11, "essay.pdf")

    monkeypatch.setattr("grader.canvas.download_attachment", _ok_download)
    first = canvas.download_submission_attachments(sub, tmp_path)
    assert first[0].ok is True

    monkeypatch.setattr("grader.canvas.download_attachment", _failing_download)
    second = canvas.download_submission_attachments(sub, tmp_path)

    assert second[0].ok is False
    assert second[0].error
    # The stale file is still on disk; existence must not be read as success.
    assert second[0].path.exists()


def test_successful_download_is_reported_ok(tmp_path, monkeypatch):
    monkeypatch.setattr("grader.canvas.download_attachment", _ok_download)

    results = canvas.download_submission_attachments(_submission(11, "essay.pdf"), tmp_path)

    assert [r.ok for r in results] == [True]
    assert results[0].error is None


def test_traversal_in_canvas_filename_stays_inside_dest_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("grader.canvas.download_attachment", _ok_download)

    results = canvas.download_submission_attachments(
        _submission(11, "../../etc/passwd"), tmp_path
    )

    assert tmp_path in results[0].path.parents
