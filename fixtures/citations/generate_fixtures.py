#!/usr/bin/env python3
"""Generate the citation-verification fixture set for Grader.

Pulls real works from Crossref (polite pool) across several disciplines, then derives
corrupted variants from that genuine metadata. Each fixture carries the verdict the
verifier is expected to return, so the resolution ladder can be scored end-to-end.

The five verdicts under test (see docs/PRD.md FR-4):
  verified           real source, metadata matches, claim supported
  metadata_mismatch  source exists but the citation's fields are wrong
  not_found          no such work — fabricated title/DOI
  claim_unsupported  source exists and is cited correctly, but does not say what the student claims
  unreachable        source exists but cannot be retrieved (paywall, dead host, JS-only)

Retracted works are a sixth, orthogonal flag: the citation resolves fine, but citing it
is itself the finding.

Usage:
    python generate_fixtures.py                # writes citation_fixtures.json + sample_paper.md
    python generate_fixtures.py --rows 8       # more source works per discipline

Network: Crossref REST API + Retraction Watch (both free, keyless). No student data involved.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CONTACT = "johnny@johnnyrobot.ai"  # Crossref polite pool; app contact, never a student address
UA = f"GraderFixtureBuilder/0.1 (https://github.com/johnnyrobot/grader; mailto:{CONTACT})"
CROSSREF = "https://api.crossref.org/works"
RETRACTION_WATCH = "https://api.labs.crossref.org/data/retractionwatch"

# Disciplines chosen to mirror what a cross-subject grading app will actually see.
QUERIES = [
    ("education", "formative feedback student learning"),
    ("psychology", "cognitive load working memory"),
    ("biology", "CRISPR gene editing off-target effects"),
    ("history", "industrial revolution labor conditions"),
    ("computer_science", "transformer attention language model"),
    ("climate", "sea level rise projections"),
]

SEED = 20260711  # deterministic fixtures; bump to regenerate a different draw


def fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def crossref_works(query: str, rows: int) -> list[dict[str, Any]]:
    """Real, well-formed works with the metadata a bibliography entry needs."""
    params = urllib.parse.urlencode(
        {
            "query.bibliographic": query,
            "rows": rows * 3,  # over-fetch; many records lack authors/year
            "select": "DOI,title,author,issued,container-title,type,URL",
            "filter": "type:journal-article,has-full-text:true",
            "mailto": CONTACT,
        }
    )
    items = fetch_json(f"{CROSSREF}?{params}")["message"]["items"]
    keep = []
    for it in items:
        if not it.get("title") or not it.get("author") or not it.get("issued"):
            continue
        parts = it["issued"].get("date-parts") or [[None]]
        if not parts[0] or not parts[0][0]:
            continue
        if not any(a.get("family") for a in it["author"]):
            continue
        keep.append(it)
        if len(keep) >= rows:
            break
    return keep


def retracted_dois(limit: int = 5) -> list[dict[str, str]]:
    """Real retracted papers — citing these should be surfaced regardless of resolvability.

    Only true retractions: the feed also carries corrections and expressions of concern,
    which are a materially different finding and must not be conflated.
    """
    url = f"{RETRACTION_WATCH}?mailto={urllib.parse.quote(CONTACT)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - fixture generation degrades, never fails
        print(f"  ! Retraction Watch unavailable ({exc}); skipping retracted fixtures", file=sys.stderr)
        return []

    out: list[dict[str, str]] = []
    seen_journals: set[str] = set()
    for row in csv.DictReader(io.StringIO(raw)):
        if (row.get("RetractionNature") or "").strip() != "Retraction":
            continue
        doi = (row.get("OriginalPaperDOI") or "").strip()
        title = (row.get("Title") or "").strip()
        author = (row.get("Author") or "").strip()
        journal = (row.get("Journal") or "").strip()
        # Dates look like "5/26/2017 0:00" — take the year from the m/d/Y part.
        date_part = (row.get("OriginalPaperDate") or "").strip().split()[0]
        bits = date_part.split("/")
        year = bits[-1] if len(bits) == 3 else ""

        if not (doi.startswith("10.") and title and author and year.isdigit()):
            continue
        if journal in seen_journals:  # spread across venues rather than 5 from one journal
            continue
        seen_journals.add(journal)
        out.append(
            {
                "doi": doi,
                "title": title,
                "first_author": author.split(";")[0].split(",")[0].strip().split()[-1],
                "year": year,
                "journal": journal,
                "retraction_reason": (row.get("Reason") or "").strip().strip(";"),
            }
        )
        if len(out) >= limit:
            break
    return out


def meta(work: dict[str, Any]) -> dict[str, Any]:
    authors = [a for a in work.get("author", []) if a.get("family")]
    first = authors[0]
    return {
        "doi": work["DOI"],
        "title": work["title"][0],
        "first_author": first["family"],
        "first_author_given": first.get("given", ""),
        "all_authors": [f"{a.get('given','')} {a['family']}".strip() for a in authors[:6]],
        "year": str(work["issued"]["date-parts"][0][0]),
        "journal": (work.get("container-title") or ["Unknown Journal"])[0],
        "url": work.get("URL", f"https://doi.org/{work['DOI']}"),
    }


def apa(m: dict[str, Any], *, year: str | None = None, title: str | None = None,
        journal: str | None = None, author: str | None = None, doi: str | None = None) -> str:
    """Render an APA-ish reference string — the raw text a student actually submits."""
    a = author or (m["all_authors"][0] if m["all_authors"] else m["first_author"])
    if not author and len(m["all_authors"]) > 1:
        a = f"{m['all_authors'][0]}, et al."
    d = doi if doi is not None else m["doi"]
    tail = f" https://doi.org/{d}" if d else ""
    return f"{a} ({year or m['year']}). {title or m['title']}. {journal or m['journal']}.{tail}"


def build(works_by_field: dict[str, list[dict]], retracted: list[dict], rng: random.Random) -> list[dict]:
    fixtures: list[dict] = []
    fid = 0

    def add(**kw) -> None:
        nonlocal fid
        fid += 1
        fixtures.append({"id": f"CIT-{fid:03d}", **kw})

    flat = [(field, meta(w)) for field, ws in works_by_field.items() for w in ws]
    rng.shuffle(flat)

    # Partition the real works across the corruption types.
    def take(n: int) -> list[tuple[str, dict]]:
        got, flat[:] = flat[:n], flat[n:]
        return got

    # ---- 1. verified: real DOI, correct metadata, claim consistent with the work -------------
    for field, m in take(6):
        add(
            field=field,
            verdict="verified",
            corruption="none",
            reference_text=apa(m),
            doi=m["doi"],
            in_text_claim=f"Prior work has examined {m['title'][:60].lower()}...",
            ground_truth=m,
            why="Real DOI; metadata matches Crossref exactly; claim is a generic restatement of the title.",
            expected_resolution_layer="L0-doi",
        )

    # ---- 2. metadata_mismatch: work exists, citation fields are wrong ------------------------
    # Wrong year — the single most common real-world citation error.
    for field, m in take(2):
        wrong = str(int(m["year"]) + rng.choice([-3, -2, 2, 4]))
        add(
            field=field, verdict="metadata_mismatch", corruption="wrong_year",
            reference_text=apa(m, year=wrong),
            doi=m["doi"],
            in_text_claim=f"As shown in {m['first_author']} ({wrong})...",
            ground_truth=m,
            why=f"DOI resolves, but the student's year ({wrong}) contradicts Crossref ({m['year']}).",
            expected_resolution_layer="L0-doi",
        )
    # Wrong first author — often a citation-manager drag error.
    for field, m in take(2):
        add(
            field=field, verdict="metadata_mismatch", corruption="wrong_author",
            reference_text=apa(m, author="Fitzgerald, R. M."),
            doi=m["doi"],
            in_text_claim=f"Fitzgerald ({m['year']}) argues that...",
            ground_truth=m,
            why=f"DOI resolves to a work whose first author is {m['first_author']}, not Fitzgerald.",
            expected_resolution_layer="L0-doi",
        )
    # Wrong journal — the paper is real, the venue is invented (a classic LLM-hallucination tell).
    for field, m in take(2):
        add(
            field=field, verdict="metadata_mismatch", corruption="wrong_journal",
            reference_text=apa(m, journal="Journal of Advanced Interdisciplinary Studies"),
            doi=m["doi"],
            in_text_claim=f"Published in the Journal of Advanced Interdisciplinary Studies, {m['first_author']} ({m['year']}) found...",
            ground_truth=m,
            why=f"DOI resolves but the container-title is '{m['journal']}', not the cited venue.",
            expected_resolution_layer="L0-doi",
        )
    # DOI points at a *different real paper* than the title/authors given — the nastiest case,
    # because every field looks individually plausible.
    pairs = take(2)
    if len(pairs) == 2:
        (fa, ma), (fb, mb) = pairs
        add(
            field=fa, verdict="metadata_mismatch", corruption="doi_belongs_to_other_work",
            reference_text=apa(ma, doi=mb["doi"]),
            doi=mb["doi"],
            in_text_claim=f"{ma['first_author']} ({ma['year']}) reports...",
            ground_truth={"cited_as": ma, "doi_actually_resolves_to": mb},
            why="Both the described paper and the DOI are real, but they are different works. "
                "Requires comparing resolved metadata against the reference string, not just checking the DOI resolves.",
            expected_resolution_layer="L0-doi",
        )

    # ---- 3. not_found: the work does not exist ----------------------------------------------
    # Fabricated title in the style of a real author's real field — the canonical LLM hallucination.
    for field, m in take(3):
        fake_title = rng.choice([
            f"A Meta-Analytic Review of {m['title'].split()[0]} Effects Across Twelve Longitudinal Cohorts",
            f"Rethinking {m['title'].split()[0]}: Toward a Unified Theoretical Framework",
            f"{m['title'].split()[0]} Revisited: Evidence from a Multi-Site Replication",
        ])
        add(
            field=field, verdict="not_found", corruption="fabricated_title_real_author",
            reference_text=apa(m, title=fake_title, doi=None),
            doi=None,
            in_text_claim=f"{m['first_author']} ({m['year']}) demonstrated a large, robust effect...",
            ground_truth={"real_author": m["first_author"], "real_journal": m["journal"], "fabricated": True},
            why="Author and journal are real; this specific title does not exist. Crossref bibliographic "
                "search should return no acceptable-score match.",
            expected_resolution_layer="L0-crossref-bibliographic",
        )
    # Syntactically valid but unregistered DOI.
    for field, m in take(2):
        prefix = m["doi"].split("/")[0]
        bogus = f"{prefix}/{rng.randint(100000, 999999)}.{rng.randint(1000, 9999)}"
        add(
            field=field, verdict="not_found", corruption="unregistered_doi",
            reference_text=apa(m, title=f"Adaptive Frameworks for {m['title'].split()[0]} Assessment", doi=bogus),
            doi=bogus,
            in_text_claim="Recent work confirms this pattern...",
            ground_truth={"doi_prefix_is_real": prefix, "suffix_fabricated": True},
            why="DOI is well-formed and carries a real registrant prefix, but resolves to nothing. "
                "Guards against a verifier that only regex-validates DOI shape.",
            expected_resolution_layer="L0-doi",
        )
    # Entirely invented author + title + venue.
    add(
        field="education", verdict="not_found", corruption="wholly_fabricated",
        reference_text="Marchetti, L. K., & Osei-Brandon, T. (2023). Rubric-anchored cognition and the "
                       "transfer of evaluative judgment. Quarterly Review of Pedagogical Science, 41(2), 118-147. "
                       "https://doi.org/10.1041/qrps.2023.0219",
        doi="10.1041/qrps.2023.0219",
        in_text_claim="Marchetti and Osei-Brandon (2023) found that rubric exposure alone improved transfer by 34%.",
        ground_truth={"fabricated": True, "note": "Author, title, journal, and DOI are all invented."},
        why="The full-hallucination case: nothing resolves at any layer. The precise-sounding statistic "
            "is the tell that graders should catch.",
        expected_resolution_layer="L0-doi",
    )

    # ---- 4. claim_unsupported: source is real and correctly cited, claim is not in it --------
    for field, m in take(3):
        add(
            field=field, verdict="claim_unsupported", corruption="claim_not_in_source",
            reference_text=apa(m),
            doi=m["doi"],
            in_text_claim=rng.choice([
                f"{m['first_author']} ({m['year']}) proves that this effect holds universally across all populations and contexts.",
                f"According to {m['first_author']} ({m['year']}), the phenomenon has been definitively refuted.",
                f"{m['first_author']} ({m['year']}) reports a 97% success rate in every replication attempted.",
            ]),
            ground_truth=m,
            why="Citation resolves and matches perfectly — the failure is semantic. Only the fetch + "
                "local claim-check layer (L2/L3) can catch it; the metadata ladder will happily pass it.",
            expected_resolution_layer="L3-local-claim-check",
        )

    # ---- 5. unreachable: exists, cannot be retrieved -----------------------------------------
    for field, m in take(2):
        add(
            field=field, verdict="unreachable", corruption="paywalled",
            reference_text=apa(m),
            doi=m["doi"],
            in_text_claim=f"{m['first_author']} ({m['year']}) provides supporting evidence.",
            ground_truth=m,
            why="Metadata resolves (L0 succeeds) but full text sits behind a publisher paywall. "
                "The verifier must report 'verified metadata, claim unverifiable' — not a false 'not found'.",
            expected_resolution_layer="L2-fetch",
        )
    add(
        field="history", verdict="unreachable", corruption="dead_link",
        reference_text="Whitaker, J. (2011). Archival labor records of the Northern mills, 1840-1870. "
                       "Regional History Working Papers. http://archive.northmill-history.org/wp/1840-1870.pdf",
        doi=None,
        in_text_claim="Whitaker (2011) documents average shifts exceeding fourteen hours.",
        ground_truth={"host_resolves": False, "note": "Web-only source; host is gone."},
        why="Non-academic web source with no DOI and a dead host. Tests the L1/L2 fallback path and "
            "ensures a dead link is reported as 'unreachable', not 'fabricated'.",
        expected_resolution_layer="L2-fetch",
    )
    add(
        field="computer_science", verdict="unreachable", corruption="js_rendered_only",
        reference_text="Anthropic. (2026). Model context protocol specification. "
                       "https://modelcontextprotocol.io/specification",
        doi=None,
        in_text_claim="The specification defines tools, resources, and prompts as primitives.",
        ground_truth={"note": "Live source; content requires JS rendering."},
        why="Real, reachable source whose content only appears after JS execution. Exercises the "
            "crawl4ai/Playwright path; a naive httpx fetch would wrongly report it empty/unreachable.",
        expected_resolution_layer="L2-fetch-js",
    )

    # ---- 6. retracted (orthogonal flag): resolves cleanly, but citing it is the finding ------
    for r in retracted[:3]:
        add(
            field="retraction_check", verdict="verified", corruption="cites_retracted_work",
            flags=["retracted"],
            reference_text=f"{r['first_author']} ({r['year']}). {r['title']}. {r['journal']}. https://doi.org/{r['doi']}",
            doi=r["doi"],
            in_text_claim=f"{r['first_author']} ({r['year']}) established this result.",
            ground_truth={**r, "retracted": True},
            why="The citation resolves and its metadata matches — so the metadata ladder passes it. The "
                "finding is that the work was RETRACTED. Requires a Retraction Watch cross-check that is "
                "separate from resolution. A verifier without it will silently bless retracted science.",
            expected_resolution_layer="L0-doi + retraction-watch",
        )

    return fixtures


def sample_paper(fixtures: list[dict]) -> str:
    """A student-paper-shaped document whose bibliography embeds the fixtures end-to-end."""
    picks = [f for f in fixtures if f["field"] in {"education", "psychology"}][:8] or fixtures[:8]
    body = [
        "# The Role of Feedback in Student Learning",
        "",
        "*Submitted by: [TEST FIXTURE — not real student work]*",
        "",
        "## Introduction",
        "",
    ]
    for i, f in enumerate(picks, 1):
        body.append(f"{f['in_text_claim']} [{i}]")
        body.append("")
    body += ["## References", ""]
    for i, f in enumerate(picks, 1):
        body.append(f"[{i}] {f['reference_text']}")
        body.append("")
    body += [
        "---",
        "",
        "<!-- Expected verdicts (for the harness; strip before feeding to the grader):",
    ]
    for i, f in enumerate(picks, 1):
        body.append(f"  [{i}] {f['id']}: {f['verdict']} ({f['corruption']})")
    body.append("-->")
    return "\n".join(body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=5, help="source works to pull per discipline")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent)
    args = ap.parse_args()

    rng = random.Random(SEED)

    print("Pulling real works from Crossref (polite pool)...")
    works: dict[str, list[dict]] = {}
    for field, query in QUERIES:
        try:
            got = crossref_works(query, args.rows)
            works[field] = got
            print(f"  {field:18s} {len(got)} works")
        except Exception as exc:  # noqa: BLE001
            print(f"  {field:18s} FAILED: {exc}", file=sys.stderr)
            works[field] = []

    print("Pulling retracted works from Retraction Watch...")
    retracted = retracted_dois(5)
    print(f"  retracted          {len(retracted)} works")

    fixtures = build(works, retracted, rng)

    counts: dict[str, int] = {}
    for f in fixtures:
        counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1

    payload = {
        "name": "grader-citation-fixtures",
        "version": "1.0",
        "generated_from": "Crossref REST API (real works) + Retraction Watch",
        "note": "Real metadata, deliberately corrupted. No student data. Regenerate with generate_fixtures.py.",
        "verdicts_under_test": [
            "verified", "metadata_mismatch", "not_found", "claim_unsupported", "unreachable",
        ],
        "orthogonal_flags": ["retracted"],
        "counts": counts,
        "total": len(fixtures),
        "fixtures": fixtures,
    }

    out_json = args.out / "citation_fixtures.json"
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    out_md = args.out / "sample_paper.md"
    out_md.write_text(sample_paper(fixtures))

    print(f"\nWrote {len(fixtures)} fixtures -> {out_json}")
    for v, n in sorted(counts.items()):
        print(f"  {v:20s} {n}")
    print(f"Wrote sample paper       -> {out_md}")


if __name__ == "__main__":
    main()
