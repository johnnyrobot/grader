# CLAUDE.md

Use this file as the short operational brief for Claude-style coding agents in this repository.

## Boundaries

- Student work stays local. Never commit real student work, teacher-local golden sets, Canvas exports, credentials, model weights, DMGs, notarization artifacts, or clean-machine logs with secrets.
- Use `uv run python` or `uv run pytest` for Python commands.
- Canvas live writes are allowed only in courses whose names start with `ZZ-GRADER-TEST`.
- A Canvas post requires an approved grade in the data layer.
- Citation verification is experimental unless `engine/verify_citations.py` passes at >=90% accuracy, zero false positives, and 3/3 retractions caught.

## Current Public Beta Standard

The repo uses three evidence tiers:

- `public_proxy`: open human-scored data for supervised public beta evidence.
- `teacher_local`: instructor-owned historical grading for "grades like this teacher" evidence.
- `synthetic_fixture`: mechanics and regression only; never accuracy evidence.

The app is not public-beta ready until `scripts/release_readiness.py` reports
`supervised_public_beta`.

## Useful Commands

```bash
uv run pytest engine/tests/ -v
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir fixtures/public_proxy
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir fixtures/public_proxy
uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json --output artifacts/citation-results.json
uv run python scripts/live_canvas_e2e.py --dry-run
uv run python scripts/release_readiness.py --profile public_beta --out artifacts/release-readiness
```
