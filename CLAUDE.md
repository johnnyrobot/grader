# CLAUDE.md

This file is a concise entry point for Claude-based coding sessions. Read `AGENTS.md` first and
follow the nearest nested `AGENTS.md` for files under `engine/`, `engine/grader/`, `scripts/`,
`fixtures/citations/`, or `humanizer-main/`.

## Product Boundary

Grader is a local-first macOS grading copilot for college instructors. The Python/FastAPI engine
uses capped Ollama models to draft rubric-aligned grades; the Tauri 2/React app lets the teacher
inspect, edit, override, and explicitly approve those drafts.

Non-negotiable rules:

- Student work must remain on-device. Never commit real student work, golden sets, handwriting
  samples, databases, credentials, private files, model weights, DMGs, notarization artifacts, or
  clean-machine logs with secrets.
- No grade may post to Canvas without an approved grade in the data layer.
- Original student files are immutable; annotations always go to a new output file.
- Vision transcribes and the text model grades. Never let a VLM grade directly.
- Use only `grader-text:8b` and `grader-vision:1.3b` in application grading paths.
- Keep `humanizer-main/` separate; it is not part of the Grader product.
- Canvas live writes are allowed only in courses whose names start with `ZZ-GRADER-TEST`.

## Current Desktop UI

The complete light-first neuroinclusive redesign is implemented across onboarding, queue, grade
review, settings, and model management.

- Presentation state: `app/src/theme.tsx`
- Semantic design tokens and component styling: `app/src/index.css`
- Shared shell: `app/src/components/AppShell.tsx`
- Screens: `app/src/screens/`
- Component tests: `app/src/**/*.test.tsx`
- Full mocked workflow: `app/e2e/neuroinclusive-ui.spec.ts`

Light mode is the default. Dark mode, spacious density, and reduced motion are persisted user
preferences. Keep status understandable without color, preserve visible focus and keyboard paths,
and maintain 200% text reflow. Frontend API calls stay in `app/src/api.ts`; grading, documents,
and Canvas behavior stay in Python.

## Public Beta Standard

The repo uses three evidence tiers:

- `public_proxy`: open human-scored data for supervised public beta evidence.
- `teacher_local`: instructor-owned historical grading for "grades like this teacher" evidence.
- `synthetic_fixture`: mechanics and regression only; never accuracy evidence.

The app is not public-beta ready until `scripts/release_readiness.py` reports
`supervised_public_beta`. Citation verification is experimental unless
`engine/verify_citations.py` passes at >=90% accuracy, zero false positives, and 3/3 retractions
caught.

## Commands

```bash
uv sync
uv run pytest engine/tests/ -q

cd app && npm install
cd app && npm test -- --run
cd app && npm run build
cd app/src-tauri && cargo check

# E2E: run these in separate terminals
cd app && npm run dev -- --host 127.0.0.1
cd app && npm run test:e2e

uv run python scripts/build_public_proxy.py mohler-asag --out artifacts/public_proxy/mohler_asag --grade --anchor-exemplars-per-source 40 --anchor-k 6
uv run python scripts/build_public_proxy.py mohler-asag --out artifacts/public_proxy/mohler_asag --grade --grade-perturbations --anchor-exemplars-per-source 40 --anchor-k 6
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir artifacts/public_proxy/mohler_asag
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir artifacts/public_proxy/mohler_asag
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir fixtures/public_proxy --allow-mechanics-fixture  # smoke only
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir fixtures/public_proxy --allow-mechanics-fixture     # smoke only
uv run python engine/gates/egress.py --capture-duration 15
uv run python engine/gates/perf.py --memory-floor 16
uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json --output artifacts/citation-results.json
uv run python scripts/live_canvas_e2e.py --dry-run
uv run python scripts/release_readiness.py --profile public_beta --out artifacts/release-readiness
```

## Current Evidence

As of 2026-07-13, Grader is suitable only for a developer-assisted alpha using synthetic or
institution-approved test data. It is not ready for an unattended public beta with real student
work.

- Python: 90 passed, 12 skipped.
- Frontend: 13 component tests and four Playwright E2E tests pass, including a 16-screen visual
  sweep with 167 visible controls recorded.
- Privacy: PASS in isolation, 3/3 captured connections allowlisted.
- Performance: PASS, 38.3 seconds for a 1,500-word essay.
- Public-proxy quality/fairness: release mode rejects tiny mechanics fixtures. The local anchored
  40-item Mohler ASAG run produced 40 scored rows, passes quality at QWK 0.845 against a 0.623
  human-ceiling-adjusted floor, and passes fairness across all five perturbation families.
- Teacher-local quality/fairness: unvalidated until authorized instructor data exists.
- Citations: PASS_EXPERIMENTAL for beta mode, 20/29 correct (69%), 0 false positives, and 3/3
  retractions caught; enabled mode still requires >=90% accuracy.
- Canvas: guarded live app-level E2E passes on `canvas.johnnyrobot.ai` course `106`, assignment
  `2841`: import, targeted grade for Canvas user `9`, teacher override/approval, post, Progress
  completed, score `2`, and one teacher feedback comment read back.
- Release readiness: local report currently returns `blocked` for signing credentials, stale release
  artifacts, and clean-machine evidence. `scripts/updater_evidence.py` and
  `scripts/clean_machine_evidence.py` now fail closed when the supplied `latest.json`, DMG, or app
  predate the current git commit. The latest local signed-app Computer Use sweep inspected 20 packaged
  app states and found the existing DMG stale: after review approval it still displayed both the
  approved message and "Teacher changes not saved yet." Fresh local signing/notarization is blocked
  because `codesign` returns `errSecInternalComponent` and notarytool profile `grader` is unavailable;
  run `uv run python scripts/signing_doctor.py --print-repair-runbook` for local-only recovery steps.
  After credentials are restored, rebuild/re-sign/notarize from current source, then rerun
  clean-machine first-run models, crash recovery, keyboard-only review, Computer Use instructor
  sweep, and updater path with structured manual evidence notes plus screenshot/log paths.

Before public beta: keep citation checks visibly experimental unless accuracy improves, restore
signing/notarization credentials, regenerate release artifacts from the current source commit, and
complete a signed/notarized clean-machine install plus first-run, crash-recovery, keyboard-only,
Computer Use instructor-sweep, and updater tests. Canvas
rubric-assessment posting remains disabled until local rubric criteria can be mapped to real Canvas
criterion IDs; attachment write-back still needs separate evidence.

## Working-Tree Caution

The modification under `.omo/run-continuation/` remains unrelated operational state. Never commit,
discard, or overwrite it unless the user explicitly requests it.
