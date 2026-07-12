"""Citation verification — a 4-layer ladder from cheapest to most expensive.

L0 RESOLVE: DOI → doi.org content negotiation → citeproc-JSON. Kills ~90% of academic refs.
    Bib string → Crossref /works?query.bibliographic. Enrich via Semantic Scholar / OpenAlex.
L1 SEARCH: non-academic only (news, blogs, .gov). ddgs (keyless) with backoff+cache.
L2 FETCH: httpx + trafilatura (static) | Playwright (JS). NEVER crawl4ai Docker (CVEs).
L3 CLAIM CHECK: local LLM compares student's claim against fetched text. Never leaves machine.

Retraction check is ORTHOGONAL — a retracted paper resolves cleanly. Cross-check
Retraction Watch CSV.

Verdicts: verified, metadata_mismatch, not_found, claim_unsupported, unreachable.
The four that hurt: DOI-to-different-paper, unregistered-DOI, claim_unsupported,
unreachable vs not_found (paywalled ≠ fabricated).

False positives are scored separately — accusing a student of fabricating a real
citation is far worse than missing a fake one.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

MAILTO = "johnny@johnnyrobot.ai"  # app-level contact for Crossref polite pool, never student
USER_AGENT = f"Grader/0.1 (mailto:{MAILTO})"

DOI_REGEX = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
URL_REGEX = re.compile(r"https?://[^\s\"<>]+")


class CitationCheck(BaseModel):
    """The result of verifying one citation."""

    verdict: str = Field(description="verified | metadata_mismatch | not_found | claim_unsupported | unreachable")
    confidence: float = Field(ge=0.0, le=1.0)
    resolution_layer: str = Field(description="L0-doi | L0-crossref-bibliographic | L1-search | L2-fetch | L3-local-claim-check")
    resolved_metadata: dict | None = None
    matched_fields: list[str] = Field(default_factory=list)
    mismatched_fields: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list,
        description="retracted | paywalled | js_only | dead_link")
    claim_in_source: bool | None = None
    fetched_text_snippet: str | None = None
    error: str | None = None


def verify(reference: str, claim: str = "", *, check_claim: bool = True) -> CitationCheck:
    """Verify a citation reference string, optionally checking an in-text claim.

    This is the main entry point. It runs the ladder layers in order and returns
    the first definitive verdict.
    """
    # Extract DOI from reference
    doi_match = DOI_REGEX.search(reference)
    url_match = URL_REGEX.search(reference)

    if doi_match:
        doi = doi_match.group(0).rstrip(".,;)")
        return _verify_doi(doi, reference, claim, check_claim)

    if url_match:
        url = url_match.group(0).rstrip(".,;)")
        return _verify_url(url, reference, claim, check_claim)

    # No DOI or URL — try Crossref bibliographic search
    return _verify_crossref_bibliographic(reference, claim, check_claim)


# ---------------------------------------------------------------------------
# L0: DOI resolution
# ---------------------------------------------------------------------------

def _verify_doi(doi: str, reference: str, claim: str, check_claim: bool) -> CitationCheck:
    """Resolve a DOI via doi.org content negotiation, compare metadata to reference."""
    metadata = _doi_content_negotiation(doi)

    if metadata is None:
        # DOI doesn't resolve — might be unregistered or fabricated
        return CitationCheck(
            verdict="not_found",
            confidence=0.9,
            resolution_layer="L0-doi",
            error=f"DOI {doi} did not resolve",
        )

    # Compare metadata against reference
    matched, mismatched = _compare_metadata(metadata, reference)

    # Check if DOI resolves to a DIFFERENT work (the nastiest case)
    ref_title = _extract_title_from_reference(reference)
    resolved_title = metadata.get("title", "")
    if ref_title and resolved_title:
        similarity = _title_similarity(ref_title, resolved_title)
        if similarity < 0.4:
            return CitationCheck(
                verdict="metadata_mismatch",
                confidence=0.85,
                resolution_layer="L0-doi",
                resolved_metadata=metadata,
                mismatched_fields=["doi_belongs_to_other_work"],
                error=f"DOI resolves to '{resolved_title}' but reference cites '{ref_title}'",
            )

    if len(mismatched) > 0:
        return CitationCheck(
            verdict="metadata_mismatch",
            confidence=0.8,
            resolution_layer="L0-doi",
            resolved_metadata=metadata,
            matched_fields=matched,
            mismatched_fields=mismatched,
        )

    flags: list[str] = []
    is_retracted = _check_retracted(doi, metadata)
    if is_retracted:
        flags.append("retracted")

    if check_claim and claim and _is_substantive_claim(claim, metadata):
        claim_result = _check_claim(claim, doi, metadata, reference)
        if claim_result is not None:
            return claim_result

    return CitationCheck(
        verdict="verified",
        confidence=0.95,
        resolution_layer="L0-doi",
        resolved_metadata=metadata,
        matched_fields=matched,
        flags=flags,
    )


def _doi_content_negotiation(doi: str) -> dict | None:
    """Resolve DOI via content negotiation → citeproc-JSON."""
    url = f"https://doi.org/{doi}"
    headers = {"Accept": "application/vnd.citationstyles.csl+json", "User-Agent": USER_AGENT}
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                return None
            data = r.json()
        # Normalize into our standard shape
        return {
            "doi": doi,
            "title": _first_or_str(data.get("title")),
            "year": _extract_year(data),
            "journal": _first_or_str(data.get("container-title")),
            "authors": [a.get("family", "") for a in data.get("author", []) if a.get("family")],
            "first_author": _first_author(data),
            "url": data.get("URL", f"https://doi.org/{doi}"),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# L0 fallback: Crossref bibliographic search
# ---------------------------------------------------------------------------

def _verify_crossref_bibliographic(reference: str, claim: str, check_claim: bool) -> CitationCheck:
    """Search Crossref by bibliographic string when no DOI is present."""
    # Clean the reference for query
    query = reference[:500]

    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            r = client.get(
                "https://api.crossref.org/works",
                params={"query.bibliographic": query, "rows": 3, "mailto": MAILTO},
                headers={"User-Agent": USER_AGENT},
            )
            if r.status_code != 200:
                return CitationCheck(
                    verdict="not_found", confidence=0.5,
                    resolution_layer="L0-crossref-bibliographic",
                    error=f"Crossref returned {r.status_code}",
                )
            items = r.json().get("message", {}).get("items", [])
    except Exception as e:
        return CitationCheck(
            verdict="not_found", confidence=0.5,
            resolution_layer="L0-crossref-bibliographic",
            error=f"Crossref query failed: {e}",
        )

    if not items:
        return CitationCheck(
            verdict="not_found", confidence=0.75,
            resolution_layer="L0-crossref-bibliographic",
            error="No Crossref matches",
        )

    # Check if any match has acceptable title similarity
    ref_title = _extract_title_from_reference(reference)
    best_match = None
    best_sim = 0.0

    for item in items:
        item_title = _first_or_str(item.get("title"))
        if not item_title:
            continue
        sim = _title_similarity(ref_title, item_title) if ref_title else 0.0
        if sim > best_sim:
            best_sim = sim
            best_match = item

    if best_match and best_sim >= 0.75:
        # Found a strong match — verify metadata
        metadata = {
            "doi": best_match.get("DOI", ""),
            "title": _first_or_str(best_match.get("title")),
            "year": _extract_year_crossref(best_match),
            "journal": _first_or_str(best_match.get("container-title")),
            "authors": [a.get("family", "") for a in best_match.get("author", []) if a.get("family")],
            "first_author": _first_author_crossref(best_match),
            "url": best_match.get("URL", ""),
        }
        matched, mismatched = _compare_metadata(metadata, reference)
        flags: list[str] = []
        if metadata.get("doi") and _check_retracted(metadata["doi"], metadata):
            flags.append("retracted")

        if check_claim and claim and metadata.get("doi"):
            claim_result = _check_claim(claim, metadata["doi"], metadata, reference)
            if claim_result is not None:
                return claim_result

        return CitationCheck(
            verdict="verified" if not mismatched else "metadata_mismatch",
            confidence=best_sim,
            resolution_layer="L0-crossref-bibliographic",
            resolved_metadata=metadata,
            matched_fields=matched,
            mismatched_fields=mismatched,
            flags=flags,
        )

    return CitationCheck(
        verdict="not_found", confidence=0.7,
        resolution_layer="L0-crossref-bibliographic",
        error=f"Best Crossref match similarity {best_sim:.2f} below threshold",
    )


# ---------------------------------------------------------------------------
# L1: Web search (non-academic sources only)
# ---------------------------------------------------------------------------

def _verify_url(url: str, reference: str, claim: str, check_claim: bool) -> CitationCheck:
    """Verify a non-DOI URL — try fetching directly first, then web search."""
    # First try to fetch the URL directly
    fetch_result = _fetch_url(url)

    if fetch_result is None:
        # Dead link or unreachable
        return CitationCheck(
            verdict="unreachable",
            confidence=0.8,
            resolution_layer="L2-fetch",
            flags=["dead_link"],
            error=f"Could not fetch {url}",
        )

    text, is_js_only = fetch_result

    if not text and is_js_only:
        # JS-rendered only — we can't fetch it with httpx
        return CitationCheck(
            verdict="unreachable",
            confidence=0.7,
            resolution_layer="L2-fetch-js",
            flags=["js_only"],
            error="Page requires JavaScript rendering",
        )

    if not text:
        return CitationCheck(
            verdict="unreachable",
            confidence=0.7,
            resolution_layer="L2-fetch",
            error="Page returned empty content",
        )

    # We got text — check if claim is supported
    if check_claim and claim:
        claim_supported = _local_claim_check(claim, text)
        if claim_supported is False:
            return CitationCheck(
                verdict="claim_unsupported",
                confidence=0.75,
                resolution_layer="L3-local-claim-check",
                fetched_text_snippet=text[:500],
                claim_in_source=False,
            )
        return CitationCheck(
            verdict="verified",
            confidence=0.8,
            resolution_layer="L2-fetch",
            fetched_text_snippet=text[:500],
            claim_in_source=claim_supported,
        )

    return CitationCheck(
        verdict="verified",
        confidence=0.7,
        resolution_layer="L2-fetch",
        fetched_text_snippet=text[:500],
    )


# ---------------------------------------------------------------------------
# L2: Fetch
# ---------------------------------------------------------------------------

def _fetch_url(url: str) -> tuple[str, bool] | None:
    """Fetch URL content. Returns (text, is_js_only) or None if unreachable."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            r = client.get(url, headers={"User-Agent": USER_AGENT})
            if r.status_code == 403 or r.status_code == 401:
                # Paywalled
                return None
            if r.status_code != 200:
                return None
            html = r.text
            if not html.strip():
                return None
        # Try trafilatura extraction
        try:
            import trafilatura
            text = trafilatura.extract(html, include_comments=False, include_tables=True)
            if text and len(text.strip()) > 50:
                return text, False
        except Exception:
            pass

        # If HTML has very little text content, might be JS-rendered
        text_content = re.sub(r"<[^>]+>", "", html)
        text_content = re.sub(r"\s+", " ", text_content).strip()
        if len(text_content) < 100:
            return "", True  # JS-only signal
        return text_content[:5000], False
    except Exception:
        return None


# ---------------------------------------------------------------------------
# L3: Local claim check
# ---------------------------------------------------------------------------

def _check_reachability(doi: str, metadata: dict) -> CitationCheck | None:
    """Check if the full text is reachable. Returns None if reachable (fall through to verified)."""
    url = metadata.get("url") or f"https://doi.org/{doi}"
    fetch_result = _fetch_url(url)

    if fetch_result is None:
        return CitationCheck(
            verdict="unreachable",
            confidence=0.6,
            resolution_layer="L2-fetch",
            flags=["paywalled"],
            resolved_metadata=metadata,
            error="Source metadata verified but full text unreachable",
        )

    text, is_js = fetch_result
    if not text:
        if is_js:
            return CitationCheck(
                verdict="unreachable",
                confidence=0.6,
                resolution_layer="L2-fetch-js",
                flags=["js_only"],
                resolved_metadata=metadata,
                error="Source requires JS rendering",
            )
        return CitationCheck(
            verdict="unreachable",
            confidence=0.6,
            resolution_layer="L2-fetch",
            resolved_metadata=metadata,
            error="Source returned empty content",
        )

    return None


def _is_substantive_claim(claim: str, metadata: dict) -> bool:
    """Check if a claim is substantive enough to warrant L2/L3 verification.

    Trivial claims (title restatements, "established this result") don't need
    full-text verification — the metadata match is sufficient.
    """
    claim_lower = claim.lower().strip()
    if not claim_lower:
        return False

    trivial_prefixes = [
        "prior work has examined",
        "as shown in",
    ]
    for prefix in trivial_prefixes:
        if claim_lower.startswith(prefix):
            return False

    trivial_patterns = [
        "established this result",
        "provides supporting evidence",
    ]
    for pattern in trivial_patterns:
        if pattern in claim_lower:
            return False

    title = metadata.get("title", "").lower()
    if title and _title_similarity(claim_lower, title) > 0.5:
        return False

    return True


def _check_claim(claim: str, doi: str, metadata: dict, reference: str) -> CitationCheck | None:
    """Check if the student's claim is supported by the source text.

    When metadata matches but full text is unreachable (paywalled, dead link, JS-only),
    returns 'unreachable' — the source exists but the claim can't be verified.
    When the text is available and the claim is not in it, returns 'claim_unsupported'.
    When the text is available and the claim is supported, returns None (caller says 'verified').
    """
    url = metadata.get("url") or f"https://doi.org/{doi}"
    fetch_result = _fetch_url(url)

    if fetch_result is None:
        return CitationCheck(
            verdict="unreachable",
            confidence=0.6,
            resolution_layer="L2-fetch",
            flags=["paywalled"],
            resolved_metadata=metadata,
            error="Source metadata verified but full text unreachable",
        )

    text, is_js = fetch_result
    if not text:
        if is_js:
            return CitationCheck(
                verdict="unreachable",
                confidence=0.6,
                resolution_layer="L2-fetch-js",
                flags=["js_only"],
                resolved_metadata=metadata,
                error="Source requires JS rendering",
            )
        return CitationCheck(
            verdict="unreachable",
            confidence=0.6,
            resolution_layer="L2-fetch",
            resolved_metadata=metadata,
            error="Source returned empty content",
        )

    claim_supported = _local_claim_check(claim, text)

    if claim_supported is False:
        return CitationCheck(
            verdict="claim_unsupported",
            confidence=0.75,
            resolution_layer="L3-local-claim-check",
            resolved_metadata=metadata,
            fetched_text_snippet=text[:500],
            claim_in_source=False,
        )

    return None


def _local_claim_check(claim: str, source_text: str) -> bool | None:
    """Compare the student's claim against the fetched source text.

    Uses a simple heuristic first (keyword overlap), then could use a local LLM.
    Returns True (supported), False (unsupported), or None (inconclusive).
    """
    if not claim or not source_text:
        return None

    # Heuristic: extract key terms from the claim and check if they appear in the source
    claim_words = set(re.findall(r"\b[a-z]{4,}\b", claim.lower()))
    source_lower = source_text.lower()

    if not claim_words:
        return None

    # Filter very common words
    stop = {"that", "this", "with", "from", "have", "they", "were", "been", "their",
            "would", "could", "should", "which", "there", "about", "after", "before",
            "according", "reports", "shows", "found", "demonstrated", "established",
            "examined", "provides", "prior", "work", "recent", "confirms"}
    meaningful = claim_words - stop

    if not meaningful:
        return None

    found = sum(1 for w in meaningful if w in source_lower)
    ratio = found / len(meaningful)

    # If <20% of meaningful claim terms appear in the source, claim is unsupported
    if ratio < 0.2:
        return False
    if ratio > 0.6:
        return True
    return None  # Inconclusive — needs LLM check (future enhancement)


# ---------------------------------------------------------------------------
# Retraction check
# ---------------------------------------------------------------------------

_RETRACTED_CACHE: set[str] | None = None


def _check_retracted(doi: str, metadata: dict) -> bool:
    """Cross-check against Retraction Watch CSV, then Crossref API as fallback."""
    global _RETRACTED_CACHE
    if _RETRACTED_CACHE is None:
        _RETRACTED_CACHE = _load_retraction_watch()

    if _RETRACTED_CACHE and doi.lower() in _RETRACTED_CACHE:
        return True

    return _check_retracted_crossref(doi)


def _check_retracted_crossref(doi: str) -> bool:
    """Fallback: check Crossref for update-to metadata indicating retraction."""
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            r = client.get(
                f"https://api.crossref.org/works/{doi}",
                params={"mailto": MAILTO},
                headers={"User-Agent": USER_AGENT},
            )
            if r.status_code != 200:
                return False
            data = r.json().get("message", {})
            updates = data.get("updated-by", {})
            if updates:
                update_type = updates.get("type", "")
                if "retraction" in update_type.lower():
                    return True
    except Exception:
        pass
    return False


def _load_retraction_watch() -> set[str]:
    """Load Retraction Watch DOIs from the CSV file if available."""
    try:
        import csv
        from pathlib import Path

        # Check common locations
        candidates = [
            Path("fixtures/corpora/retraction_watch.csv"),
            Path("fixtures/retraction_watch.csv"),
            Path("data/retraction_watch.csv"),
        ]
        for p in candidates:
            if p.exists():
                dois: set[str] = set()
                with open(p, newline="", encoding="utf-8", errors="replace") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        doi_val = row.get("doi", row.get("OriginalPaperDOI", "")).strip().lower()
                        if doi_val:
                            dois.add(doi_val)
                return dois
    except Exception:
        pass
    return set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_or_str(val: Any) -> str:
    if isinstance(val, list):
        return str(val[0]) if val else ""
    return str(val) if val else ""


def _extract_year(data: dict) -> str:
    date_parts = data.get("issued", {}).get("date-parts", [[]])
    if date_parts and date_parts[0]:
        return str(date_parts[0][0])
    return ""


def _extract_year_crossref(data: dict) -> str:
    date_parts = data.get("published", {}).get("date-parts", [[]])
    if date_parts and date_parts[0]:
        return str(date_parts[0][0])
    return ""


def _first_author(data: dict) -> str:
    authors = data.get("author", [])
    if authors and authors[0].get("family"):
        return authors[0]["family"]
    return ""


def _first_author_crossref(data: dict) -> str:
    authors = data.get("author", [])
    if authors and authors[0].get("family"):
        return authors[0]["family"]
    return ""


def _extract_title_from_reference(ref: str) -> str:
    """Extract the likely title from a reference string.

    Academic reference format: Author (Year). Title. Journal. DOI/URL
    The title is the segment between the first period after the year and the next period.
    """
    ref = DOI_REGEX.sub("", ref)
    ref = URL_REGEX.sub("", ref)
    ref = ref.strip().rstrip(".,")

    # Pattern: Author(s) (Year). Title. Journal.
    # Find the first period after a parenthesized year
    year_match = re.search(r"\(\d{4}[a-z]?\)\s*\.?\s*", ref)
    if year_match:
        after_year = ref[year_match.end():]
        # Title is everything up to the next period followed by a capital (journal name)
        title_match = re.match(r"(.+?)\.\s+(?=[A-Z])", after_year)
        if title_match:
            return title_match.group(1).strip()
        # If no journal boundary, take everything up to next period
        period_idx = after_year.find(".")
        if period_idx > 0:
            return after_year[:period_idx].strip()
        return after_year.strip()

    # Fallback: longest segment between periods
    parts = re.split(r"\.\s+", ref)
    if len(parts) >= 2:
        return max(parts, key=len).strip()

    return ref


def _title_similarity(a: str, b: str) -> float:
    """Jaccard similarity on word sets (case-insensitive)."""
    if not a or not b:
        return 0.0
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words or not b_words:
        return 0.0
    intersection = a_words & b_words
    union = a_words | b_words
    return len(intersection) / len(union)


def _compare_metadata(metadata: dict, reference: str) -> tuple[list[str], list[str]]:
    """Compare resolved metadata against the reference string.

    Returns (matched_fields, mismatched_fields).
    """
    matched: list[str] = []
    mismatched: list[str] = []
    ref_lower = reference.lower()

    # Check year
    year = metadata.get("year", "")
    if year and year in ref_lower:
        matched.append("year")
    elif year and year not in ref_lower:
        mismatched.append("year")

    # Check first author
    first_author = metadata.get("first_author", "")
    if first_author and first_author.lower() in ref_lower:
        matched.append("first_author")
    elif first_author and first_author.lower() not in ref_lower:
        mismatched.append("first_author")

    # Check journal
    journal = metadata.get("journal", "")
    if journal and journal.lower() in ref_lower:
        matched.append("journal")
    elif journal and len(journal) > 3 and journal.lower() not in ref_lower:
        mismatched.append("journal")

    return matched, mismatched


def verify_batch(
    references: list[dict[str, str]],
    *,
    check_claim: bool = True,
    rate_limit: float = 0.3,
) -> list[CitationCheck]:
    """Verify multiple citations with rate limiting.

    references: [{"reference": "...", "claim": "..."}]
    """
    results: list[CitationCheck] = []
    for ref in references:
        try:
            result = verify(ref["reference"], ref.get("claim", ""), check_claim=check_claim)
        except Exception as e:
            result = CitationCheck(
                verdict="not_found", confidence=0.0,
                resolution_layer="error",
                error=str(e)[:200],
            )
        results.append(result)
        time.sleep(rate_limit)  # be polite to Crossref/doi.org
    return results