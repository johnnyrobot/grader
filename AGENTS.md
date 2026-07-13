# PROJECT KNOWLEDGE BASE

**Generated:** 2026-07-12
**Mode:** init-deep (update)

## OVERVIEW

Local-first macOS grading copilot for college instructors. A Python/FastAPI engine drafts
rubric-aligned grades with Pydantic AI + Ollama; a Tauri 2/React desktop app lets the teacher
review, override, and approve them. Canvas, document parsing, handwriting transcription,
citation verification, annotated write-back, release tooling, and CI gates are implemented but
still need real-world validation. Student work must remain on-device, and no grade may post
without explicit teacher approval.

## STRUCTURE

```
grader/
├── app/                         # Tauri 2 + React/TypeScript desktop app
│   ├── src/                     # wizard, queue, split view, model manager, settings, API client
│   └── src-tauri/               # Rust lifecycle, engine process, updater, bundle config
├── engine/
│   ├── api.py                   # authenticated loopback FastAPI API + SQLite/WAL queue/audit log
│   ├── grader/                  # grading, calibration, Canvas, parsing, citations, write-back
│   ├── gates/                   # quality, bias, egress, and performance release gates
│   ├── tests/                   # schema, metrics, triage, and fixture tests
│   ├── bench.py                 # P0 model/output-mode spike
│   ├── calibrate.py             # P1 anchor-vs-baseline experiment
│   └── verify_citations.py      # 29-fixture citation scoring harness
├── fixtures/
│   ├── citations/               # committed synthetic citation fixtures
│   └── golden_set/              # format/sample generator; real teacher data stays gitignored
├── scripts/
│   ├── make_models.sh           # build context-capped Ollama variants
│   ├── seed_canvas_sandbox.py   # guarded live Canvas round-trip seeder/verifier
│   ├── collect_golden.py        # Canvas/local golden-set collector and validator
│   ├── start_engine.sh          # desktop engine launcher
│   ├── sign_and_notarize.sh     # macOS release signing pipeline
│   ├── frontend_evidence.py     # frontend component/build/E2E/visual-sweep artifact
│   ├── updater_evidence.py      # updater manifest/signature/artifact evidence
│   └── generate_appcast.py      # Tauri updater manifest generator
├── docs/                        # handoff, product/spec, FERPA, rollout, phases P0-P5, research
├── .github/workflows/           # ship gates and tag-triggered release workflow
├── humanizer-main/              # separate agent-skill subproject; has its own AGENTS.md
├── RESEARCH.md                  # cited architectural rationale
├── pyproject.toml               # uv-managed Python project; package=false
└── uv.lock
```

Nested guidance exists in `engine/AGENTS.md`, `engine/grader/AGENTS.md`,
`scripts/AGENTS.md`, `fixtures/citations/AGENTS.md`, and `humanizer-main/AGENTS.md`.

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Product/status orientation | `docs/HANDOFF.md`, then `README.md` | Phase docs may lag code; verify implementation before quoting status |
| Grading behavior | `engine/grader/agent.py`, `schema.py`, `triage.py` | `grade()` is the non-raising entry point |
| Calibration/anchors | `engine/grader/exemplars.py`, `calibration.py`, `overrides.py` | P1 result artifacts live in `engine/results_pointer_*.json` |
| Desktop UX | `app/src/App.tsx`, `app/src/screens/`, `app/src/index.css` | Light-first semantic theme; queue and split view are the central workflow |
| Frontend verification | `app/src/**/*.test.tsx`, `app/e2e/neuroinclusive-ui.spec.ts` | 12 component tests plus four mocked Playwright E2E tests |
| Desktop ↔ engine boundary | `app/src/api.ts`, `engine/api.py` | Dynamic loopback HTTP port + per-launch bearer token; dev fallback remains `127.0.0.1:8756` |
| Engine process lifecycle | `app/src-tauri/src/engine.rs`, `lib.rs`, `scripts/start_engine.sh` | Starts/stops uvicorn with the Tauri app |
| Updater release evidence | `scripts/generate_appcast.py`, `scripts/updater_evidence.py`, `app/src-tauri/tauri.conf.json` | Requires signed `latest.json` and signed artifact |
| Canvas round-trip | `engine/grader/canvas.py`, Canvas routes in `engine/api.py` | Live sandbox helper is `scripts/seed_canvas_sandbox.py` |
| Document/handwriting | `engine/grader/parse.py`, `handwriting.py` | Vision transcribes; the text model grades |
| Citation verification | `engine/grader/citations.py`, `engine/verify_citations.py` | Network-dependent result artifact; rerun before quoting accuracy |
| Annotated output | `engine/grader/writeback.py` | Always writes a copy; never mutates the source |
| Release readiness | `engine/gates/`, `engine/gates/public_proxy.py`, `scripts/release_readiness.py`, `.github/workflows/`, `docs/phases/P5.md` | Public-proxy gates exist; teacher-local quality/bias still require authorized data |
| Data/licensing rules | `docs/DATA-PLAN.md`, `docs/FERPA.md`, `.gitignore` | Never commit student work or NC corpora |

## CODE MAP

| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `grade()` | function | `engine/grader/agent.py:122` | Rubric + text → `GradeRun`; catches failures instead of raising |
| `Rubric` / `GradeDraft` | models | `engine/grader/schema.py:59`, `:98` | Validated grading contract and output |
| `ExemplarStore` | class | `engine/grader/exemplars.py:95` | SQLite anchors; similarity + score-band balancing |
| `triage()` | function | `engine/grader/triage.py:64` | Confidence/flags/criterion type → review lane |
| `compute_health()` | function | `engine/grader/calibration.py:103` | Rolling QWK, override rate, and calibration state |
| `apply_override()` | function | `engine/grader/overrides.py:192` | Teacher correction → approved grade + new exemplar |
| `RubricStore` | class | `engine/grader/rubrics.py:59` | Versioned rubric persistence/import/amendment |
| `OfflinePostQueue` | class | `engine/grader/canvas.py:284` | Durable Canvas posts when connectivity returns |
| `parse()` | function | `engine/grader/parse.py:34` | Document/image → local markdown and metadata |
| `transcribe_pages()` | function | `engine/grader/handwriting.py:117` | Apple Vision/VLM transcription before grading |
| `verify()` / `verify_batch()` | functions | `engine/grader/citations.py:52`, `:707` | Layered metadata, reachability, claim, and retraction checks |
| `annotate()` | function | `engine/grader/writeback.py:226` | Dispatches copy-only DOCX/PDF/XLSX annotation |
| FastAPI routes | endpoints | `engine/api.py:287` onward | Queue, grading, Canvas, documents, citations, settings, updates |
| `start_engine()` | function | `app/src-tauri/src/engine.rs` | Launches authenticated local engine process |

## CONVENTIONS

- Run Python via **`uv run python`** (or `uv run pytest`); this is a `package = false` project.
- Python is 3.12+; new modules use `from __future__ import annotations`.
- Grade at temperature 0. Measure uncertainty with separate consistency samples.
- Put enforceable output constraints in Pydantic schemas, not prompt prose.
- `grade()` never raises. Failures become `GradeRun.error` and remain visible in the queue.
- `build_model()` is the only grading seam that knows the Ollama/OpenAI-compatible base URL.
- Use only capped tags: `grader-text:8b` and `grader-vision:1.3b`.
- Vision transcribes, text grades. Scanned work defaults to human review.
- The engine binds to loopback and protected routes require `GRADER_ENGINE_TOKEN`.
- SQLite uses WAL for queue/audit durability. The audit log is append-only.
- A Canvas post requires a teacher-approved grade in the data layer, not merely in the UI.
- Original student files are immutable; annotated work is written to a new destination.
- Frontend calls belong in `app/src/api.ts`; grading/document/Canvas logic belongs in Python.
- Light mode is the default. Theme, density, and motion preferences live in `app/src/theme.tsx`.
- Use semantic CSS variables/classes from `app/src/index.css`; do not add new hard-coded gray/dark
  utility palettes to feature screens.
- Status must remain understandable without color. Preserve visible text, focus indicators,
  keyboard paths, reduced motion, and 200% text reflow when changing the UI.

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER commit student work**, even de-identified. Real golden sets, handwriting samples,
  databases, and private files are gitignored for FERPA reasons.
- **NEVER commit NC-licensed corpora** (PERSUADE 2.0, ELLIPSE, AAC&U VALUE, IAM).
- **NEVER run crawl4ai's Docker server**; only the Apache-licensed library path is allowed.
- **NEVER use PyMuPDF (AGPL), marker (GPL/OpenRAIL), or self-hosted Firecrawl (AGPL).**
  Use Docling, pypdf, python-docx, and openpyxl.
- **NEVER let a VLM grade directly.** Transcribe first, then call the normal text grader.
- **NEVER use Qwen3-VL for vision.** Its un-disableable reasoning caused 140s+ item latency;
  MiniCPM-V measured about 5.8s on the same task.
- **NEVER use raw Ollama base tags for app grading.** Default contexts can consume tens of GB.
- **NEVER pipe `ollama list` to `grep -q` under `set -o pipefail`.** Snapshot first; otherwise
  SIGPIPE can make installed models appear absent.
- **NEVER report Canvas bulk posting complete until its Progress object reaches `completed`.**
- **NEVER parse canvas-pp-cli reads as `{data}`.** Reads are `{meta, results}`; mutations use
  `{action, resource, success, data, partial_failure}`.
- **NEVER point sandbox tooling at an unmarked course.** `ZZ-GRADER-TEST` is the safety boundary.
- **Do not re-research or relitigate locked decisions** without new evidence. Read
  `RESEARCH.md` and `docs/research/` first.
- **Do not conflate `humanizer-main/` with Grader.** It is a separate subproject.

## UNIQUE STYLES

- Research identifiers in docstrings justify rubric scale, temperature, QWK, retrieval, and
  criterion-type bias handling. Read those citations before changing thresholds.
- `CriterionType.OPEN_ENDED` never auto-clears to high confidence because style bias persists.
- `Agreement.mean_error` is signed: positive means AI-lenient; negative means AI-harsh.
- Anchor retrieval round-robins score bands; pure nearest-neighbor over-selects high scores.
- Teacher overrides are training signals: approved corrections become anchors and repeated
  patterns can produce rubric-refinement proposals.
- The UI is teacher-in-the-loop by construction: queue → inspect → edit/override → approve → post.

## COMMANDS

```bash
# Setup and capped models
uv sync
ollama pull qwen3:8b && ollama pull minicpm-v4.6:latest
./scripts/make_models.sh

# Tests and local engine
uv run pytest engine/tests/ -v
GRADER_ENGINE_TOKEN=dev uv run uvicorn engine.api:app --host 127.0.0.1 --port 8756 --reload

# Desktop development/build checks
cd app && npm install && npm run tauri dev
cd app && npm test -- --run
cd app && npm run build
cd app && npm run dev -- --host 127.0.0.1  # then, in another terminal: npm run test:e2e
cd app/src-tauri && cargo check

# Experiments and citation verification
uv run python engine/bench.py
uv run python engine/calibrate.py --n-test 20 --n-exemplars 60 --k 6
uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json

# Release gates
uv run python engine/gates/quality.py
uv run python engine/gates/bias.py
uv run python engine/gates/egress.py --capture-duration 15
uv run python engine/gates/perf.py --memory-floor 16

# Golden set (real output stays gitignored)
uv run python scripts/collect_golden.py validate --golden-dir fixtures/golden_set/
uv run python scripts/collect_golden.py stats --golden-dir fixtures/golden_set/

# Guarded Canvas sandbox
python scripts/seed_canvas_sandbox.py --apply --students 6
python scripts/seed_canvas_sandbox.py --teardown --course-id 106 --apply

# Fixtures
python fixtures/citations/generate_fixtures.py

# Public beta readiness gates
uv run python scripts/build_public_proxy.py mohler-asag --out artifacts/public_proxy/mohler_asag --grade --anchor-exemplars-per-source 40 --anchor-k 6
uv run python scripts/build_public_proxy.py mohler-asag --out artifacts/public_proxy/mohler_asag --grade --grade-perturbations --anchor-exemplars-per-source 40 --anchor-k 6
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir artifacts/public_proxy/mohler_asag
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir artifacts/public_proxy/mohler_asag
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir fixtures/public_proxy --allow-mechanics-fixture  # smoke only
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir fixtures/public_proxy --allow-mechanics-fixture     # smoke only
uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json --output artifacts/citation-results.json
uv run python scripts/live_canvas_e2e.py --dry-run
uv run python scripts/release_readiness.py --profile public_beta --out artifacts/release-readiness
```

## CURRENT STATUS / NEXT WORK

- **P0 complete:** `qwen3:8b` + Pydantic AI tool output is schema-reliable; MiniCPM-V replaced
  Qwen3-VL for transcription.
- **P1 complete:** five calibration experiments ran. At k=6, anchors improved QWK by about
  +0.127/+0.135. The local anchored Mohler ASAG public-proxy artifact now passes quality
  at QWK 0.845 against a 0.623 human-ceiling-adjusted floor.
- **P2 in progress:** the complete light-first neuroinclusive UI is implemented across onboarding,
  queue, review, settings, and model management. Twelve component tests and four mocked Playwright
  E2E tests pass, including a 16-screen visual sweep with 167 visible controls, automated
  accessibility checks, local-engine recovery alert coverage, and 200% text scaling. Still validate
  a clean-machine `.app`, crash recovery, real first-run model setup, and packaging.
- **P3 in progress:** Canvas client/API/offline queue exist and the guarded live app-level path
  passed for score/comment: import → targeted grade → teacher override/approval → post → Progress
  completed → read-back on `canvas.johnnyrobot.ai` course `106`, assignment `2841`, Canvas user `8`.
  Canvas rubric-assessment posting remains disabled until local rubric criteria can be mapped to real
  Canvas criterion IDs; attachment write-back still needs separate evidence.
- **P4 in progress:** parse/transcribe/write-back/citation modules exist. The 2026-07-12 citation
  rerun scored 20/29 (69%), with 0 false positives and 3/3 retractions caught; enabled mode still
  fails the >=90% phase goal, so beta readiness accepts it only as visibly experimental.
- **P5 in progress:** privacy passes in isolation (3/3 captured connections allowlisted) and the
  performance gate passes at 38.3s per 1,500-word essay. Public-proxy quality/fairness release mode
  rejects the tiny committed mechanics fixtures. The local anchored 40-item Mohler ASAG public-proxy
  artifact passes quality at QWK 0.845 and passes fairness across all five perturbation families.
  Updater evidence is currently blocked until signed `latest.json` references the signed release
  artifact. Fresh local signing/notarization is currently blocked: `codesign` returns
  `errSecInternalComponent` and notarytool profile `grader` is unavailable. Teacher-local validation
  still requires authorized instructor data, and clean-machine behavioral checks still need release
  validation.
- **Coverage:** Python collects 98 cases (86 pass, 12 skip). Frontend has 12 passing component
  tests plus four passing Playwright E2E tests.
- **Public beta verdict:** not ready for unattended public testing with real student work. Limit
  current use to a developer-assisted alpha with synthetic or institution-approved test data.
- **Highest-value next action:** restore signing/notarization credentials, generate signed updater
  evidence, then collect clean-machine first-run model setup, crash recovery, keyboard-only review,
  and updater-path evidence before broadening beta claims.
- The existing modification under `.omo/run-continuation/` is unrelated operational state; do not
  overwrite or commit it unless explicitly requested.
