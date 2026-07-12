# CITATION FIXTURES

29 synthetic citation verification fixtures with expected verdicts. Real metadata from Crossref, deliberately corrupted. No student data.

## STRUCTURE

```
citations/
├── generate_fixtures.py      # pulls Crossref + Retraction Watch, derives corrupted variants
├── citation_fixtures.json    # 29 fixtures with verdicts, corruption types, ground truth
├── sample_paper.md           # student-paper-shaped doc embedding 8 fixtures in bibliography
└── README.md                 # human-readable fixture documentation
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Regenerate fixtures | `generate_fixtures.py` | `python fixtures/citations/generate_fixtures.py --rows 8` |
| Inspect a fixture | `citation_fixtures.json` | Each has id, verdict, corruption, reference_text, ground_truth, why |
| Test the verifier end-to-end | `sample_paper.md` | Bibliography embeds 8 fixtures; expected verdicts in HTML comment |
| Add a new corruption type | `generate_fixtures.py` → `build()` | Partition real works, derive variant, `add()` to fixtures list |

## CONVENTIONS

- **Deterministic**: `SEED=20260711` — bump to regenerate a different draw
- **No student data**: all metadata from Crossref REST API + Retraction Watch (both free, keyless)
- **Crossref polite pool**: `mailto:johnny@johnnyrobot.ai` in User-Agent (app contact, never student)
- **Contact email is app-level**, never a student address
- Fixture generation **degrades, never fails** — `except Exception` with `noqa: BLE001` is intentional

## Verdicts under test

| Verdict | Meaning | Resolution layer |
|---------|---------|-----------------|
| `verified` | Real source, metadata matches, claim supported | L0-doi |
| `metadata_mismatch` | Source exists but citation fields wrong | L0-doi |
| `not_found` | No such work — fabricated | L0-doi or L0-crossref-bibliographic |
| `claim_unsupported` | Source real, correctly cited, claim not in it | L3-local-claim-check |
| `unreachable` | Source exists but cannot be retrieved | L2-fetch or L2-fetch-js |
| `retracted` (flag) | Resolves cleanly but citing it is the finding | L0-doi + retraction-watch |

## NOTES

- Corruption types: `wrong_year`, `wrong_author`, `wrong_journal`, `doi_belongs_to_other_work`, `fabricated_title_real_author`, `unregistered_doi`, `wholly_fabricated`, `claim_not_in_source`, `paywalled`, `dead_link`, `js_rendered_only`, `cites_retracted_work`
- `doi_belongs_to_other_work` is the nastiest case — every field looks individually plausible, requires comparing resolved metadata against reference string
- `wholly_fabricated` is the full-hallucination case — precise-sounding statistics are the tell
- `js_rendered_only` tests the crawl4ai/Playwright path — a naive httpx fetch would wrongly report empty/unreachable
- Fixtures are committed (no license, no PII) — `.gitignore` explicitly allows `fixtures/citations/`