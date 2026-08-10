"""Citation verification is the only part of grading that talks to the internet.

Invariant #1 says no student PII leaves the device, with one carve-out: lookups
of a *cited source's* metadata — a DOI, a title, a URL. A bibliography entry is
that metadata and may go out. The student's own prose is not, and must not.

The distinction lives in `_check_claim`, which fetches the **source's** URL and
then compares the claim against the fetched text with `_local_claim_check`, in
process. Nothing had ever tested that, in a 345-statement module sitting at 18%
coverage that makes real outbound requests.

These tests capture every request the ladder makes and assert the student's
sentence is in none of them.
"""

import sys
from pathlib import Path

from urllib.parse import unquote_plus

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from grader import citations  # noqa: E402


# A phrase that would never occur in a real source, so its presence in any
# outbound request is unambiguous.
STUDENT_CLAIM = (
    "Zylphonic recursion demonstrates that quorum sensing collapses under "
    "nonlinear predation pressure, which proves my thesis."
)

CROSSREF_ITEM = {
    "DOI": "10.1234/example",
    "title": ["Quorum Sensing in Bacterial Populations"],
    "container-title": ["Journal of Microbiology"],
    "author": [{"family": "Bassler", "given": "Bonnie"}],
    "issued": {"date-parts": [[2019]]},
    "URL": "https://example.test/article",
}


@pytest.fixture
def captured(monkeypatch):
    """Record every outbound request the citation ladder makes."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "crossref.org" in request.url.host:
            return httpx.Response(200, json={"message": {"items": [CROSSREF_ITEM]}})
        if "doi.org" in request.url.host:
            return httpx.Response(200, json=CROSSREF_ITEM)
        # The source itself: plain text that does not contain the claim.
        return httpx.Response(
            200,
            text="<html><body><p>Bacteria coordinate gene expression by "
                 "population density.</p></body></html>",
            headers={"content-type": "text/html"},
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    return seen


def _requests_mention(seen: list[httpx.Request], needle: str) -> bool:
    """Did any request carry `needle` in its URL, headers, or body?

    Percent-decoded first, and `+` treated as a space. A query string sends
    "Quorum+Sensing", so a raw substring search would answer False for text
    that is plainly being transmitted — which would make every negative
    assertion here pass whether or not the claim leaked.
    """
    for request in seen:
        parts = [
            unquote_plus(str(request.url)),
            "\n".join(f"{k}: {unquote_plus(v)}" for k, v in request.headers.items()),
            unquote_plus((request.content or b"").decode("utf-8", errors="replace")),
        ]
        haystack = "\n".join(parts).lower()
        if needle.lower() in haystack:
            return True
    return False


# -- The invariant -----------------------------------------------------------


def test_a_students_claim_is_never_sent_anywhere_on_the_doi_path(captured):
    citations.verify(
        "Bassler, B. (2019). Quorum Sensing. doi:10.1234/example",
        claim=STUDENT_CLAIM,
    )

    assert captured, "the test is meaningless if nothing was requested"
    assert not _requests_mention(captured, "Zylphonic recursion")
    assert not _requests_mention(captured, "proves my thesis")


def test_a_students_claim_is_never_sent_anywhere_on_the_bibliographic_path(captured):
    """The path that does send text out — the reference — must send only that."""
    citations.verify(
        "Bassler, B. (2019). Quorum Sensing. Journal of Microbiology.",
        claim=STUDENT_CLAIM,
    )

    assert captured
    assert not _requests_mention(captured, "Zylphonic recursion")
    # The bibliography entry is the carve-out and is expected to go out.
    assert _requests_mention(captured, "Quorum Sensing")


def test_a_students_claim_is_never_sent_anywhere_on_the_url_path(captured):
    citations.verify(
        "Bassler, B. (2019). Quorum Sensing. https://example.test/article",
        claim=STUDENT_CLAIM,
    )

    assert captured
    assert not _requests_mention(captured, "Zylphonic recursion")


def test_only_allowlisted_hosts_are_contacted(captured):
    """Every host reached must be one docs/FERPA.md already accounts for."""
    citations.verify(
        "Bassler, B. (2019). Quorum Sensing. doi:10.1234/example",
        claim=STUDENT_CLAIM,
    )

    hosts = {request.url.host for request in captured}
    allowed = {
        "doi.org", "api.crossref.org", "api.openalex.org",
        "api.semanticscholar.org", "example.test",
    }
    assert hosts <= allowed, f"unexpected hosts contacted: {hosts - allowed}"


# -- The claim check is local ------------------------------------------------


def test_the_claim_comparison_happens_in_process():
    """No network at all: the claim never leaves, so this needs no transport."""
    supported = citations._local_claim_check(
        "quorum sensing depends on population density",
        "Bacteria coordinate gene expression by population density in quorum sensing.",
    )

    assert supported is True


def test_a_claim_absent_from_the_source_is_reported_unsupported():
    supported = citations._local_claim_check(
        "quorum sensing collapses under nonlinear predation pressure",
        "This paper concerns the migratory patterns of arctic terns.",
    )

    assert supported is False


def test_an_empty_claim_is_inconclusive_rather_than_unsupported():
    """Nothing asserted is not the same as something contradicted."""
    assert citations._local_claim_check("", "any source text") is None
    assert citations._local_claim_check("a claim", "") is None
