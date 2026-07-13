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
│   ├── signing_doctor.py        # signing/notary credential preflight + evidence
│   ├── frontend_evidence.py     # frontend component/build/E2E/visual-sweep artifact
│   ├── updater_evidence.py      # updater manifest/signature/artifact evidence
│   ├── computer_use_sweep_evidence.py # validates signed-app Computer Use captures
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
| Frontend verification | `app/src/**/*.test.tsx`, `app/e2e/neuroinclusive-ui.spec.ts` | 14 component tests plus four mocked Playwright E2E tests |
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
uv run python scripts/build_public_proxy.py mohler-asag --out artifacts/public_proxy/mohler_asag_diverse_anchored_pilot --grade --grade-perturbations --anchor-exemplars-per-source 40 --anchor-k 6
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir artifacts/public_proxy/mohler_asag_diverse_anchored_pilot
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir artifacts/public_proxy/mohler_asag_diverse_anchored_pilot
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir fixtures/public_proxy --allow-mechanics-fixture  # smoke only
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir fixtures/public_proxy --allow-mechanics-fixture     # smoke only
uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json --output artifacts/citation-results.json
uv run python scripts/frontend_evidence.py --out artifacts/release-evidence/frontend.json
uv run python scripts/signing_doctor.py --out artifacts/release-evidence/signing.json --no-fail
uv run python scripts/signing_doctor.py --print-repair-runbook > signing-repair-runbook.md
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD='<updater-key-password>'  # required for encrypted updater keys
uv run python scripts/updater_key_doctor.py --signing-key /path/to/tauri-updater-key.pem --out artifacts/release-evidence/updater-key.json
uv run python scripts/generate_appcast.py --dmg /path/to/Grader.dmg --version 0.1.0 --signing-key /path/to/tauri-updater-key.pem --output latest.json
uv run python scripts/updater_evidence.py --manifest latest.json --artifact /path/to/Grader.dmg --out artifacts/release-evidence/updater.json  # fails if artifacts predate current git commit
uv run python scripts/live_canvas_e2e.py --dry-run
uv run python scripts/clean_machine_evidence.py --print-manual-runbook > clean-machine-runbook.md
uv run python scripts/clean_machine_evidence.py --print-manual-template > clean-machine-manual-evidence.json
uv run python scripts/computer_use_sweep_evidence.py --capture-dir /path/to/computer-use-captures --out /path/to/evidence-root/computer_use_instructor_sweep.json
uv run python scripts/clean_machine_evidence.py --app /Applications/Grader.app --dmg /path/to/Grader.dmg --updater-manifest /path/to/latest.json --expected-version 0.1.0 --manual-evidence clean-machine-manual-evidence.json --evidence-root /path/to/screenshots-and-logs --out artifacts/release-evidence/clean-machine.json
uv run python scripts/release_evidence_package.py --out-dir artifacts/release-evidence/clean-machine-handoff
uv run python scripts/release_readiness.py --profile public_beta --expected-version 0.1.0 --public-proxy-dir artifacts/public_proxy/mohler_asag_diverse_anchored_pilot --citation-artifact artifacts/citation-results.json --canvas-artifact artifacts/release-evidence/canvas-live.json --privacy-artifact artifacts/release-evidence/privacy.json --performance-artifact artifacts/release-evidence/performance.json --frontend-artifact artifacts/release-evidence/frontend.json --rust-artifact artifacts/release-evidence/rust.json --updater-artifact artifacts/release-evidence/updater.json --updater-key-artifact artifacts/release-evidence/updater-key.json --clean-machine-artifact artifacts/release-evidence/clean-machine-local-current.json --out artifacts/release-readiness
```

The clean-machine manual template must be filled with `passed: true`, tester/timestamp/notes,
existing evidence files, and every check-specific `assertions` entry set to `true`; the Computer Use
row also needs a PASS JSON from `scripts/computer_use_sweep_evidence.py`. The release-evidence
package is a clean-Mac handoff bundle only; do not treat it as passing clean-machine evidence.

## CURRENT STATUS / NEXT WORK

- **P0 complete:** `qwen3:8b` + Pydantic AI tool output is schema-reliable; MiniCPM-V replaced
  Qwen3-VL for transcription.
- **P1 complete:** five calibration experiments ran. At k=6, anchors improved QWK by about
  +0.127/+0.135. The local anchored Mohler ASAG public-proxy artifact now passes quality
  at QWK 0.845 against a 0.623 human-ceiling-adjusted floor.
- **P2 in progress:** the complete light-first neuroinclusive UI is implemented across onboarding,
  queue, review, settings, and model management. Fourteen component tests and four mocked Playwright
  E2E tests pass. `scripts/frontend_evidence.py` now fail-closes unless the visual sweep records
  all 16 required screens, screenshots, 47 required instructor controls, 167 visible controls,
  automated accessibility checks, local-engine recovery alert coverage, and 200% text scaling. Still
  validate a clean-machine `.app`, crash recovery, real first-run model setup, and packaging.
- **P3 in progress:** Canvas client/API/offline queue exist and the guarded live app-level path
  passed for score/comment: import → targeted grade → teacher override/approval → post → Progress
  completed → read-back on `canvas.johnnyrobot.ai` course `106`, assignment `2841`, Canvas user `9`,
  score `2`, and one teacher feedback comment.
  Canvas rubric-assessment posting remains disabled until local rubric criteria can be mapped to real
  Canvas criterion IDs; attachment write-back still needs separate evidence.
- **P4 in progress:** parse/transcribe/write-back/citation modules exist. The 2026-07-12 citation
  rerun scored 20/29 (69%), with 0 false positives and 3/3 retractions caught; enabled mode still
  fails the >=90% phase goal, so beta readiness accepts it only as visibly experimental.
- **P5 in progress:** privacy passes in isolation (3/3 captured connections allowlisted) and the
  performance gate passes at 38.3s per 1,500-word essay. Public-proxy quality/fairness release mode
  rejects the tiny committed mechanics fixtures. The local anchored 40-item Mohler ASAG public-proxy
  artifact passes quality at QWK 0.845 and passes fairness across all five perturbation families.
  Updater/DMG/app evidence now fails closed when artifacts predate the current git commit. The latest
  local app/DMG build signs, notarizes, staples, installs to `/Applications/Grader.app` as
  `com.grader.app`, and passes macOS signature checks, but the final release artifact must be rebuilt
  after the last committed source change. The local signed-app Computer Use sweep now
  passes with 73 accessibility/screenshot capture pairs, local keyboard-only review evidence
  passes through Tab/Enter, `E`, text entry, Command+Enter, Shift+Tab, and Enter back to queue,
  local crash recovery evidence passes by force-stopping the engine sidecar and restarting it
  from the visible recovery alert, and local fresh-profile first-run evidence passes through
  onboarding, engine readiness, queue reachability, and installed-model reconciliation.
  `scripts/computer_use_sweep_evidence.py` still fails stale capture sets and clean-machine evidence
  requires a PASS sweep JSON. The encrypted updater key was rotated before public release, the public
  key/config now target `github.com/johnnyrobot/grader`, `scripts/updater_key_doctor.py` passes with
  the local Keychain-backed password, signed `latest.json` updater evidence now passes, and the queue
  UI has a native Tauri install/restart update action. The release-readiness JSON/Markdown now
  includes an end-to-end public-beta matrix that maps requirements to evidence. Teacher-local
  validation still requires authorized instructor data, and the updater-path check still needs release
  validation.
- **Coverage:** Python collects 142 cases (130 pass, 12 skip). Frontend has 14 passing component
  tests plus four passing Playwright E2E tests.
- **Public beta verdict:** not ready for unattended public testing with real student work. Limit
  current use to a developer-assisted alpha with synthetic or institution-approved test data.
- **Highest-value next action:** publish a real N+1 updater test artifact to the configured
  `github.com/johnnyrobot/grader` release channel, exercise the in-app updater path on a clean
  supported Mac, then add structured `updater_path` manual evidence before broadening beta claims.
- The existing modification under `.omo/run-continuation/` is unrelated operational state; do not
  overwrite or commit it unless explicitly requested.
