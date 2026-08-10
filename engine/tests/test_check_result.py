"""The shared check contract: one result shape, and the rules that travel with it.

Six gates used to declare this type six times, and the evidence scripts spoke a
second dialect of it. What matters here is not that the copies collapsed but
that the two rules hold: absent evidence is MISSING, and loading one check's
artifact consults nothing but that artifact.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from gates.result import (  # noqa: E402  (needs the path insert above)
    FAIL,
    MISSING,
    PASS,
    SKIP,
    CheckResult,
    artifact_ref,
)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def test_only_failure_blocks():
    assert CheckResult(FAIL, "").exit_code() == 1
    assert CheckResult(PASS, "").exit_code() == 0


def test_a_skipped_gate_does_not_block_ci():
    """Gates degrade when the golden set or Ollama isn't there, by design."""
    assert CheckResult(SKIP, "no golden set").exit_code() == 0


def test_missing_evidence_does_not_block_a_gate_run():
    """Whether a gap may ship is the release verdict's call, not one gate's."""
    assert CheckResult(MISSING, "never ran").exit_code() == 0


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

def test_the_banner_is_the_only_thing_a_subclass_changes(capsys):
    class QualityResult(CheckResult):
        label = "QUALITY GATE"

    QualityResult(PASS, "QWK 0.81 ≥ 0.70 floor", {"qwk": 0.81}).print()

    out = capsys.readouterr().out
    assert "✓ QUALITY GATE: PASS" in out
    assert "QWK 0.81 ≥ 0.70 floor" in out
    assert "qwk: 0.81" in out


def test_an_unrecognised_status_prints_rather_than_raising(capsys):
    """A gate that has already done its work must not die formatting the answer."""
    CheckResult("PASS_EXPERIMENTAL", "accepted with caveats").print()

    assert "PASS_EXPERIMENTAL" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Absent evidence is MISSING, never PASS and never FAIL
# ---------------------------------------------------------------------------

def test_an_artifact_that_was_never_written_is_missing(tmp_path):
    result = CheckResult.load(tmp_path / "nope.json")

    assert result.status == MISSING
    assert result.details["artifact"]["exists"] is False


def test_no_path_at_all_is_missing():
    result = CheckResult.load(None)

    assert result.status == MISSING
    assert result.details["artifact"]["path"] is None


def test_a_caller_may_declare_absent_evidence_an_acceptable_skip(tmp_path):
    result = CheckResult.load(tmp_path / "nope.json", missing=SKIP)

    assert result.status == SKIP


def test_an_unreadable_artifact_is_missing_not_a_traceback(tmp_path):
    """One corrupt file must not take down the whole readiness run."""
    path = tmp_path / "broken.json"
    path.write_text("{not json")

    result = CheckResult.load(path)

    assert result.status == MISSING
    assert "unreadable" in result.summary


def test_an_artifact_that_is_not_an_object_is_missing(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]")

    assert CheckResult.load(path).status == MISSING


def test_an_artifact_with_no_status_at_all_is_missing(tmp_path):
    path = tmp_path / "quiet.json"
    path.write_text(json.dumps({"qwk": 0.81}))

    result = CheckResult.load(path)

    assert result.status == MISSING
    assert result.details["qwk"] == 0.81


# ---------------------------------------------------------------------------
# Reading what earlier scripts wrote
# ---------------------------------------------------------------------------

def test_a_written_result_reads_back_the_same(tmp_path):
    path = tmp_path / "evidence.json"
    CheckResult(PASS, "all clear", {"hosts": ["localhost"]}).write(path)

    loaded = CheckResult.load(path)

    assert loaded.status == PASS
    assert loaded.summary == "all clear"
    assert loaded.details["hosts"] == ["localhost"]


def test_release_status_is_read_as_the_status(tmp_path):
    """Older artifacts spell it `release_status`."""
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"release_status": "PASS_EXPERIMENTAL"}))

    assert CheckResult.load(path).status == "PASS_EXPERIMENTAL"


def test_a_nested_citation_release_status_is_found(tmp_path):
    path = tmp_path / "citations.json"
    path.write_text(json.dumps({"citation_mode": {"release_status": "PASS"}}))

    assert CheckResult.load(path).status == PASS


def test_an_explicit_status_wins_over_the_legacy_spelling(tmp_path):
    path = tmp_path / "both.json"
    path.write_text(json.dumps({"status": "FAIL", "release_status": "PASS"}))

    assert CheckResult.load(path).status == FAIL


def test_a_structured_summary_is_not_flattened_into_a_sentence(tmp_path):
    """Several checks use `summary` for a block of their own, not a sentence.

    A reader doing summary["visual_sweep_screens"] must still find it there.
    """
    path = tmp_path / "frontend.json"
    path.write_text(json.dumps({
        "status": PASS,
        "summary": {"visual_sweep_screens": 16, "visual_sweep_visible_controls": 47},
    }))

    result = CheckResult.load(path)

    assert result.details["summary"]["visual_sweep_screens"] == 16
    assert result.summary == ""  # no sentence was invented from the block
    assert result.to_dict()["summary"]["visual_sweep_visible_controls"] == 47


def test_a_string_summary_survives_a_round_trip_unchanged(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"status": PASS, "summary": "component tests passed"}))

    result = CheckResult.load(path)

    assert result.summary == "component tests passed"
    assert result.to_dict()["summary"] == "component tests passed"


# ---------------------------------------------------------------------------
# A check's result is its own
# ---------------------------------------------------------------------------

def test_loading_one_artifact_reads_only_that_artifact(tmp_path, monkeypatch):
    """No loader may consult a neighbour: one flaky check must not rewrite another."""
    target = tmp_path / "mine.json"
    target.write_text(json.dumps({"status": "PASS"}))
    (tmp_path / "other.json").write_text(json.dumps({"status": "FAIL"}))

    opened = []
    real_read = Path.read_text
    monkeypatch.setattr(Path, "read_text",
                        lambda self, *a, **k: (opened.append(self), real_read(self, *a, **k))[1])

    CheckResult.load(target)

    assert set(opened) == {target}


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_an_artifact_ref_records_which_bytes_produced_the_verdict(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text('{"status": "PASS"}')

    ref = artifact_ref(path)

    assert ref["exists"] is True
    assert ref["size_bytes"] == len('{"status": "PASS"}')
    assert len(ref["sha256"]) == 64
    assert ref["mtime"].endswith("+00:00")


def test_the_hash_changes_when_the_evidence_does(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text('{"status": "PASS"}')
    first = artifact_ref(path)["sha256"]

    path.write_text('{"status": "FAIL"}')

    assert artifact_ref(path)["sha256"] != first


def test_writing_creates_the_directory_it_needs(tmp_path):
    path = CheckResult(PASS, "ok").write(tmp_path / "a" / "b" / "evidence.json")

    assert json.loads(path.read_text())["status"] == PASS


@pytest.mark.parametrize("status", [PASS, FAIL, SKIP, MISSING])
def test_every_status_round_trips(tmp_path, status):
    path = tmp_path / "evidence.json"
    CheckResult(status, "summary text").write(path)

    assert CheckResult.load(path).status == status
