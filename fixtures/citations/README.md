# Citation-Verification Fixtures

Synthetic fixtures for Grader's citation-verification ladder (`docs/PRD.md` FR-4). No public
corpus of student papers with bibliographies exists, so we build one: **real metadata from
Crossref, deliberately corrupted in the specific ways the verifier must catch.**

Safe to commit — no student data, no licensed corpora. Regenerate any time:

```bash
python generate_fixtures.py          # 29 fixtures + a sample student paper
python generate_fixtures.py --rows 8 # more source works per discipline
```

Sources: Crossref REST API (real works, 6 disciplines) + Retraction Watch (real retractions).
Both free and keyless. `SEED = 20260711` makes the draw deterministic.

## Files
| File | What |
|---|---|
| `citation_fixtures.json` | 29 fixtures, each with the verdict the verifier must return |
| `sample_paper.md` | A student-paper-shaped doc whose bibliography embeds the fixtures (expected verdicts in an HTML comment — strip before grading) |
| `generate_fixtures.py` | The generator |

## The five verdicts under test

| Verdict | n | Corruptions | Which layer must catch it |
|---|---|---|---|
| `verified` | 9 | none (6 clean + 3 retracted) | L0 — DOI resolves, metadata matches |
| `metadata_mismatch` | 7 | wrong year · wrong author · wrong journal · **DOI belongs to a different real paper** | L0 — resolve, then *compare* to the reference string |
| `not_found` | 6 | fabricated title w/ real author · unregistered-but-well-formed DOI · wholly invented work | L0 — DOI + Crossref bibliographic search both miss |
| `claim_unsupported` | 3 | source is real and cited correctly; the *claim* isn't in it | **L3 only** — metadata ladder passes it happily |
| `unreachable` | 4 | paywalled · dead host · JS-rendered-only | L2 — must not be misreported as `not_found` |

Plus an orthogonal **`retracted`** flag (3 fixtures, real Retraction Watch entries): these resolve
*cleanly* and their metadata matches — the finding is the retraction itself. A verifier without a
Retraction Watch cross-check will silently bless retracted science.

## The cases that actually hurt

Most of the set is easy. These four are the ones worth building for:

- **`doi_belongs_to_other_work`** — the described paper *and* the DOI are both real, but they're
  different works. Every field looks individually valid. Catches a verifier that checks "does the
  DOI resolve?" instead of "does the DOI resolve *to this*?"
- **`unregistered_doi`** — well-formed, real registrant prefix, resolves to nothing. Catches a
  verifier that regex-validates DOI shape and calls it a day.
- **`claim_unsupported`** — the metadata ladder is *useless* here; the citation is perfect. Only
  fetching the source and checking it locally against the student's claim finds it. This is the
  case that justifies L2/L3 existing at all.
- **`unreachable` vs `not_found`** — a paywalled or dead-hosted source is a real source. Reporting
  it as fabricated is the most damaging false positive an academic-integrity tool can make.

## Scoring

Each fixture carries `expected_resolution_layer`, so the harness can assert *where* a verdict was
reached, not just that it was right. A `verified` reached by guessing is not a pass.

The false-positive direction matters more than raw accuracy: calling a real citation fabricated
harms a student. Track FP rate on `verified` + `unreachable` separately from overall accuracy —
the same asymmetry the answer-key "weak hint" research warns about (`RESEARCH.md` §8).
