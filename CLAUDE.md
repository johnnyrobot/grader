# CLAUDE.md

Grader is a **local-first macOS grading copilot** for college instructors. A Python/FastAPI engine
uses capped Ollama models (Pydantic AI) to draft rubric-aligned grades; a Tauri 2 / React desktop
app lets the teacher inspect, edit, override, and **explicitly approve** every draft before anything
posts to Canvas. Student work never leaves the device.

Read the nearest nested `AGENTS.md` when working under `engine/`, `engine/grader/`, `scripts/`,
`fixtures/citations/`, or `humanizer-main/`.

## Architecture
```
app/                     Tauri 2 + React/TS desktop app
  src/                   screens/, components/AppShell.tsx, api.ts (all engine calls), theme.tsx, index.css
  src-tauri/             Rust: engine.rs/lib.rs lifecycle, updater, tauri.conf.json bundle config
engine/
  api.py                 authenticated loopback FastAPI; SQLite/WAL queue + append-only audit log
  grader/                grading, calibration, Canvas, parsing, handwriting, citations, write-back
  gates/                 release gates: quality.py, bias.py, egress.py, perf.py, public_proxy.py
  tests/                 pytest suite (schema, metrics, triage, fixtures)
  bench.py calibrate.py verify_citations.py   P0/P1 experiments + 29-fixture citation harness
fixtures/                citations/ (synthetic, committed); golden_set/ (real data gitignored)
scripts/                 make_models.sh, start_engine.sh, Canvas + release/signing/updater tooling
docs/                    HANDOFF, product/spec, FERPA, DATA-PLAN, rollout, phases P0–P5, research
.github/workflows/       gates.yml (ship gates) + release.yml (tag-triggered)
humanizer-main/          SEPARATE subproject — not part of the Grader product; has its own AGENTS.md
RESEARCH.md              cited architectural rationale (read before relitigating locked decisions)
pyproject.toml uv.lock   uv-managed, package = false
```
**Data flow:** desktop (`app/src/api.ts`) → dynamic loopback HTTP port + per-launch bearer token →
`engine/api.py` routes → `engine/grader/*`. `grade()` (`engine/grader/agent.py`) turns rubric+text
into a `GradeRun`; `triage()` assigns a review lane; teacher `apply_override()` produces the
approved grade and a new exemplar; only then may `engine/grader/canvas.py` post.

Key symbols: `grade()` (agent.py), `Rubric`/`GradeDraft` (schema.py), `ExemplarStore`
(exemplars.py), `triage()` (triage.py), `compute_health()` (calibration.py), `apply_override()`
(overrides.py), `RubricStore` (rubrics.py), `OfflinePostQueue` (canvas.py), `parse()` (parse.py),
`transcribe_pages()` (handwriting.py), `verify()`/`verify_batch()` (citations.py), `annotate()`
(writeback.py), `start_engine()` (src-tauri/src/engine.rs).

## Commands
```bash
# Python engine
uv sync                                   # package = false project
uv run pytest engine/tests/ -q
PYTHONPATH=engine GRADER_ENGINE_TOKEN=dev uv run uvicorn engine.api:app --host 127.0.0.1 --port 8756 --reload

# Capped Ollama models (never use raw base tags in app grading)
ollama pull qwen3:8b && ollama pull minicpm-v4.6:latest
./scripts/make_models.sh                  # builds grader-text:8b and grader-vision:1.3b

# Desktop app
cd app && npm install && npm run tauri dev
cd app && npm test -- --run               # vitest component tests
cd app && npm run build                   # tsc && vite build
cd app/src-tauri && cargo check
cd app && npm run dev -- --host 127.0.0.1 # then, in another terminal: npm run test:e2e (Playwright)

# Release gates
uv run python engine/gates/quality.py
uv run python engine/gates/bias.py
uv run python engine/gates/egress.py --capture-duration 15
uv run python engine/gates/perf.py --memory-floor 16

# Guarded Canvas sandbox (ONLY ZZ-GRADER-TEST* courses)
python scripts/seed_canvas_sandbox.py --apply --students 6
```
The full public-beta release pipeline (public-proxy build, citation verify, signing/notarization,
updater appcast, clean-machine + Computer Use evidence, `scripts/release_readiness.py`) is long and
evidence-driven — see `scripts/` and `docs/phases/P5.md`. The repo is not public-beta ready until
`scripts/release_readiness.py` reports `supervised_public_beta`.

## Conventions
- Run Python via **`uv run python`** / `uv run pytest`. Python ≥ 3.12; new modules use
  `from __future__ import annotations`.
- **Grade at temperature 0.** Measure uncertainty with separate consistency samples.
- Put enforceable output constraints in **Pydantic schemas, not prompt prose**.
- `grade()` **never raises** — failures become `GradeRun.error` and stay visible in the queue.
- `build_model()` is the only grading seam that knows the Ollama/OpenAI-compatible base URL.
- Use only capped tags **`grader-text:8b`** and **`grader-vision:1.3b`**. Vision transcribes, the
  text model grades.
- Engine binds to loopback; protected routes require `GRADER_ENGINE_TOKEN`. SQLite uses WAL; the
  audit log is append-only.
- Frontend calls belong in `app/src/api.ts`; grading/document/Canvas logic stays in Python.
- **Light mode is the default.** Use semantic CSS variables from `app/src/index.css`; theme/density/
  motion prefs live in `app/src/theme.tsx`. Preserve color-independent status, visible focus,
  keyboard paths, reduced motion, and 200% text reflow.

## Gotchas & Constraints
Non-negotiable product rules:
- Student work stays on-device. **Never commit** real student work, golden sets, handwriting
  samples, databases, credentials, model weights, DMGs, notarization artifacts, or logs with secrets.
- **No grade posts to Canvas without an approved grade in the data layer** (not merely in the UI).
- Original student files are **immutable**; annotations always go to a new output file.
- **Never let a VLM grade directly** — transcribe first, then call the normal text grader.
- Canvas live writes are allowed **only** in courses whose names start with `ZZ-GRADER-TEST`.

Traps that will bite:
- **License bans:** never use PyMuPDF (AGPL), marker (GPL/OpenRAIL), or self-hosted Firecrawl/
  crawl4ai's Docker server (AGPL). Use Docling, pypdf, python-docx, openpyxl, and only crawl4ai's
  Apache library path. Never commit NC-licensed corpora (PERSUADE 2.0, ELLIPSE, AAC&U VALUE, IAM).
- **Never use Qwen3-VL for vision** (un-disableable reasoning → 140s+ latency; MiniCPM-V ≈ 5.8s).
- **Never use raw Ollama base tags for app grading** (default contexts eat tens of GB).
- **Never pipe `ollama list` to `grep -q` under `set -o pipefail`** — SIGPIPE can hide installed
  models. Snapshot first.
- **Canvas bulk posting isn't done until its Progress object reaches `completed`.**
- **canvas-pp-cli reads are `{meta, results}`; mutations are `{action, resource, success, data,
  partial_failure}`** — don't parse a read as `{data}`.
- Never point sandbox tooling at an unmarked course; `ZZ-GRADER-TEST` is the safety boundary.
- The modification under `.omo/run-continuation/` is unrelated operational state — **never commit,
  discard, or overwrite it** unless the user explicitly asks.
- Don't relitigate locked decisions without new evidence — read `RESEARCH.md` and `docs/research/`.

## Evidence & Status
Three evidence tiers: `public_proxy` (open human-scored data → supervised public-beta evidence),
`teacher_local` (instructor-owned history → "grades like this teacher"), `synthetic_fixture`
(mechanics/regression only, never accuracy). As of 2026-07-24: Python 240 passing; 18 frontend
(16 component + 2 API) + 4 Playwright E2E; individual gates PASS on current `main` (privacy,
perf ~38s/1,500-word essay, public-proxy QWK 0.845). Citations are `PASS_EXPERIMENTAL` (20/29,
0 false positives, 3/3 retractions) — keep visibly experimental until ≥ 90% accuracy. Not an
unattended or teacher-specific accuracy claim; teacher-local calibration awaits authorized data.

**The consolidated `supervised_public_beta` verdict is STALE — do not trust it as current.** It
was generated 2026-07-13/14 and has not been regenerated since ~20 commits (settings routing,
Canvas attachments, exemplar retrieval, thread safety, HTTPS, the E2E repair) landed. Regenerate
`scripts/release_readiness.py` against current `main` before making any readiness claim. See
`docs/HANDOFF.md` §1a for what this session changed and why the old verdict can't be inherited.
