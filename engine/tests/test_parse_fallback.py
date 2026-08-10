"""What happens to a student's file when Docling cannot read it.

`parse()` catches every Docling failure and falls back to decoding the file's
raw bytes as UTF-8. For a genuinely textual file under an extension Docling
declines, that is the right rescue. For a PDF it is not: the bytes decode to
replacement characters and control codes, and that noise used to travel on as
the student's essay — stored as their submission, graded by the model, with the
0.3 confidence and the warning region dropped by every caller.

The rule these tests fix in place: a fallback read may return text, or it may
return nothing, but it may never return binary dressed as prose.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader.parse import parse, parse_multiple  # noqa: E402


BROKEN_PDF = (
    b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<</Type/Catalog>>\nendobj\n"
    b"\xde\xad\xbe\xef\x00\x01\x02trailer\n"
)


@pytest.fixture
def broken_pdf(tmp_path):
    path = tmp_path / "essay.pdf"
    path.write_bytes(BROKEN_PDF)
    return path


def test_an_unreadable_pdf_yields_no_text_rather_than_its_own_bytes(broken_pdf):
    parsed = parse(broken_pdf)

    assert parsed.markdown.strip() == ""
    assert "%PDF" not in parsed.markdown
    assert "�" not in parsed.markdown


def test_an_unreadable_pdf_reports_no_confidence(broken_pdf):
    parsed = parse(broken_pdf)

    assert parsed.confidence == 0.0
    assert parsed.low_confidence_regions != []
    assert "essay.pdf" in parsed.low_confidence_regions[0]


def test_a_text_file_under_an_unknown_extension_still_falls_back_to_its_text(tmp_path):
    """The fallback's real job survives: this is why it exists."""
    path = tmp_path / "answer.rtfd"
    path.write_text("The mitochondrion is the powerhouse of the cell.")

    parsed = parse(path)

    assert "powerhouse" in parsed.markdown
    assert parsed.confidence == 0.3


def test_a_mostly_text_file_with_a_few_odd_bytes_is_still_readable(tmp_path):
    """A stray control byte is not a reason to throw a student's essay away."""
    path = tmp_path / "answer.doc"
    path.write_bytes(b"A recursive function calls itself.\x00 It needs a base case.")

    parsed = parse(path)

    assert "recursive function" in parsed.markdown


def test_an_empty_file_yields_no_text(tmp_path):
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")

    parsed = parse(path)

    assert parsed.markdown.strip() == ""


def test_parse_multiple_degrades_one_bad_file_without_losing_the_good_ones(
    tmp_path, broken_pdf
):
    good = tmp_path / "good.txt"
    good.write_text("A queue is first in, first out.")

    results = parse_multiple([broken_pdf, good])

    assert results[0].markdown.strip() == ""
    assert "first in, first out" in results[1].markdown
