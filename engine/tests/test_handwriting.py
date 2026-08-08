"""Transcription, and the confidence the rest of the product routes on.

`handwriting.py` sat at 12% coverage while being the module the scanned-work
REVIEW_FIRST rule exists for. Its confidence numbers are heuristics — Apple
Vision returns no score, so text length and an alphanumeric ratio stand in for
one — and those heuristics decide whether the VLM gets called at all and
whether a page is flagged for the teacher.

These pin what the heuristics actually do, including where they are generous.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader import handwriting  # noqa: E402
from grader.handwriting import transcribe_image, transcribe_pages  # noqa: E402


@pytest.fixture
def an_image(tmp_path):
    path = tmp_path / "page1.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return path


# -- The layered fallback ----------------------------------------------------


def test_a_missing_image_is_not_transcribed(tmp_path):
    text, confidence = transcribe_image(tmp_path / "nope.png")

    assert confidence == 0.0


def test_a_confident_apple_vision_read_does_not_pay_for_the_vlm(monkeypatch, an_image):
    """Layer 2 is ~6s/page; layer 1 is free. Skipping it is the whole design."""
    monkeypatch.setattr(handwriting, "_apple_vision", lambda p: ("A queue is FIFO.", 0.95))
    monkeypatch.setattr(
        handwriting, "_vlm_transcribe",
        lambda p, model="": pytest.fail("the VLM must not run on a confident read"),
    )

    text, confidence = transcribe_image(an_image)

    assert text == "A queue is FIFO."
    assert confidence == 0.95


def test_a_poor_apple_vision_read_escalates_to_the_vlm(monkeypatch, an_image):
    monkeypatch.setattr(handwriting, "_apple_vision", lambda p: ("gibb3r1sh", 0.30))
    monkeypatch.setattr(
        handwriting, "_vlm_transcribe", lambda p, model="": ("A queue is FIFO.", 0.80)
    )

    text, confidence = transcribe_image(an_image)

    assert text == "A queue is FIFO."
    assert confidence == 0.80


def test_the_better_of_the_two_reads_wins(monkeypatch, an_image):
    """A VLM that does worse than Apple Vision must not overwrite it."""
    monkeypatch.setattr(handwriting, "_apple_vision", lambda p: ("plausible text", 0.60))
    monkeypatch.setattr(handwriting, "_vlm_transcribe", lambda p, model="": ("junk", 0.20))

    text, confidence = transcribe_image(an_image)

    assert text == "plausible text"
    assert confidence == 0.60


def test_both_layers_failing_yields_no_text(monkeypatch, an_image):
    """Empty, not a marker string — parse.py's caller tests `.strip()`."""
    monkeypatch.setattr(handwriting, "_apple_vision", lambda p: ("", 0.0))
    monkeypatch.setattr(handwriting, "_vlm_transcribe", lambda p, model="": ("", 0.0))

    text, confidence = transcribe_image(an_image)

    assert text == ""
    assert confidence == 0.0


# -- What the confidence heuristics actually say -----------------------------


def _with_fake_ocr(monkeypatch, lines):
    """Stand in for Apple Vision, which returns (text, score, box) triples."""

    class _FakeOCR:
        def __init__(self, path, recognition_level=""):
            pass

        def __iter__(self):
            return iter([(line, 1.0, None) for line in lines])

    monkeypatch.setitem(
        sys.modules, "ocrmac",
        type("m", (), {"ocrmac": type("o", (), {"OCR": _FakeOCR})}),
    )


def test_a_read_of_nothing_scores_zero(monkeypatch, an_image):
    _with_fake_ocr(monkeypatch, [])

    assert handwriting._apple_vision(an_image) == ("", 0.0)


def test_a_very_short_read_stays_uncertain_so_the_vlm_still_runs(monkeypatch, an_image):
    """Under ten characters could be a real answer or a failed read."""
    _with_fake_ocr(monkeypatch, ["yes"])

    text, confidence = handwriting._apple_vision(an_image)

    assert text == "yes"
    assert confidence == 0.5, "must stay below the 0.85 short-circuit"


def test_a_clean_prose_read_scores_high_enough_to_skip_the_vlm(monkeypatch, an_image):
    _with_fake_ocr(monkeypatch, ["A queue is a first in, first out structure."])

    _, confidence = handwriting._apple_vision(an_image)

    assert confidence >= 0.85


def test_a_garbled_read_scores_low_enough_to_escalate(monkeypatch, an_image):
    """The alphanumeric ratio is the only signal Apple Vision gives us."""
    _with_fake_ocr(monkeypatch, ["#@%^&*!~`+=<>{}[]|\\/?;:#@%^&*!~`+=<>{}"])

    _, confidence = handwriting._apple_vision(an_image)

    assert confidence < 0.85


# -- Multi-page aggregation --------------------------------------------------


def test_pages_are_joined_with_a_visible_break(monkeypatch, tmp_path):
    monkeypatch.setattr(handwriting, "transcribe_image",
                        lambda p, model="": (f"text of {p.name}", 0.9))
    pages = [tmp_path / "p1.png", tmp_path / "p2.png"]

    result = transcribe_pages(pages)

    assert "--- Page break ---" in result["text"]
    assert "text of p1.png" in result["text"]
    assert "text of p2.png" in result["text"]


def test_one_bad_page_flags_the_whole_batch_for_review(monkeypatch, tmp_path):
    scores = iter([0.95, 0.40])
    monkeypatch.setattr(handwriting, "transcribe_image",
                        lambda p, model="": ("text", next(scores)))

    result = transcribe_pages([tmp_path / "p1.png", tmp_path / "p2.png"])

    assert result["needs_review"] is True
    assert result["low_confidence_pages"] == [1]


def test_all_good_pages_need_no_review(monkeypatch, tmp_path):
    monkeypatch.setattr(handwriting, "transcribe_image", lambda p, model="": ("text", 0.95))

    result = transcribe_pages([tmp_path / "p1.png", tmp_path / "p2.png"])

    assert result["needs_review"] is False
    assert result["avg_confidence"] == pytest.approx(0.95)


def test_no_pages_reports_no_confidence_but_also_no_review(monkeypatch):
    """Recorded because it is a rough edge, not because it is right.

    An empty batch averages to 0.0 confidence and still says needs_review
    False. /api/transcribe rejects an empty path list before this can be
    reached, which is the only reason it does not matter today.
    """
    result = transcribe_pages([])

    assert result["avg_confidence"] == 0.0
    assert result["needs_review"] is False
