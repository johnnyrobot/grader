# AGENTS.md

Grader is a **fully-local macOS desktop app** that helps college instructors grade essays, problem
sets, lab reports, code, and scanned handwriting with an AI that calibrates to how the instructor
grades. Stack: a Python 3.12 / FastAPI engine (Pydantic AI + capped Ollama models, SQLite/WAL) and a
Tauri 2 + React/TypeScript desktop app (Rust host). Canvas integration, document/handwriting
parsing, citation verification, annotated write-back, release tooling, and CI gates are implemented
but still require real-world validation. **Student work must remain on-device, and no grade posts
without explicit teacher approval.**

This is the root handbook. Nested `AGENTS.md` files add local guidance under `engine/`,
`engine/grader/`, `scripts/`, `fixtures/citations/`, and `humanizer-main/` — read the nearest one.

## Setup
```bash
# Python engine (uv-managed; this is a `package = false` project)
uv sync

# Capped Ollama models — the ONLY tags allowed in application grading paths
ollama pull qwen3:8b && ollama pull minicpm-v4.6:latest
./scripts/make_models.sh          # produces grader-text:8b and grader-vision:1.3b

# Desktop app deps
cd app && npm install
```
- Python ≥ 3.12; new modules use `from __future__ import annotations`.
- Ollama must be running for any grading path. Never substitute raw base tags (default contexts can
  consume tens of GB); use only the capped `grader-text:8b` / `grader-vision:1.3b`.
- Canvas credentials resolve from env vars or the authenticated `canvas-pp-cli` config/credentials
  files. Live writes are restricted to `ZZ-GRADER-TEST*` courses.

## Build & Run
```bash
# Engine standalone (loopback + bearer token required on protected routes)
PYTHONPATH=engine GRADER_ENGINE_TOKEN=dev uv run uvicorn engine.api:app \
  --host 127.0.0.1 --port 8756 --reload

# Full desktop app (starts/stops the engine sidecar via Tauri)
cd app && npm run tauri dev

# Production builds
cd app && npm run build           # tsc && vite build (frontend)
cd app/src-tauri && cargo check   # Rust host
```
- The desktop app talks to the engine over a **dynamic loopback HTTP port + per-launch bearer
  token**; dev fallback is `127.0.0.1:8756`. All frontend↔engine calls go through `app/src/api.ts`.
- Packaging/signing/notarization and in-app updates are driven by `scripts/` and
  `app/src-tauri/tauri.conf.json`; the full release pipeline lives in `docs/phases/P5.md`.

## Testing
```bash
uv run pytest engine/tests/ -q            # Python engine (209 passing as of 2026-07-13)
cd app && npm test -- --run               # vitest component tests (15)
cd app && npm run dev -- --host 127.0.0.1 # then in another terminal:
cd app && npm run test:e2e                # Playwright E2E (mocked workflow, 4 tests)
cd app/src-tauri && cargo check           # Rust must compile

# Release gates (must pass before shipping)
uv run python engine/gates/quality.py
uv run python engine/gates/bias.py
uv run python engine/gates/egress.py --capture-duration 15
uv run python engine/gates/perf.py --memory-floor 16

# Citation harness (network-dependent; rerun before quoting accuracy)
uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json
```
- Tests live in `engine/tests/` (pytest, configured via `pyproject.toml` with `pythonpath=["engine"]`
  and `testpaths=["engine/tests"]`) and `app/src/**/*.test.tsx` + `app/e2e/`.
- CI: `.github/workflows/gates.yml` runs ship gates; `release.yml` is tag-triggered.
- **Definition of done:** the Python suite, the frontend component + E2E suites, and `cargo check`
  all pass, plus any gate touched by the change. The repo is not public-beta ready until
  `scripts/release_readiness.py` reports `supervised_public_beta`. Evidence artifacts land in
  `artifacts/`; citation and Canvas results are freshness-checked against the current git commit, so
  rerun them rather than quoting stale numbers.

## Code Style
- Run all Python through `uv run` (`uv run python`, `uv run pytest`).
- **Grade at temperature 0**; measure uncertainty with separate consistency samples.
- Put enforceable output constraints in **Pydantic schemas, not prompt prose**.
- `grade()` **never raises** — capture failures into `GradeRun.error` so they stay visible in the
  queue. `build_model()` is the single seam that knows the model base URL.
- **Vision transcribes, the text model grades** — never let a VLM grade directly.
- Frontend logic stays in TypeScript under `app/src/` (calls in `api.ts`); grading, document, and
  Canvas logic stays in Python. Don't move business logic across that boundary.
- UI is light-mode-first: use semantic CSS variables from `app/src/index.css`; don't add hard-coded
  gray/dark utility palettes. Preserve color-independent status, visible focus, keyboard paths,
  reduced motion, and 200% text reflow. Theme/density/motion prefs persist via `app/src/theme.tsx`.
- Keep `humanizer-main/` fully separate — it is not part of the Grader product.

## Commit & PR Conventions
- Git repo. See `CONTRIBUTING.md` for the contributor flow.
- Never commit forbidden data (see Security & Data). `.gitignore` already excludes golden sets,
  handwriting samples, databases, credentials, model weights, DMGs, and notarization artifacts.
- End every commit message with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Only commit or push when the user asks. Never commit, discard, or overwrite the working-tree state
  under `.omo/run-continuation/` unless explicitly requested.

## Security & Data
- **FERPA / on-device is the core constraint.** Student work never leaves the machine. Never commit
  real student work (even de-identified), golden sets, handwriting samples, databases, credentials,
  private files, model weights, or logs containing secrets.
- **Never commit NC-licensed corpora** (PERSUADE 2.0, ELLIPSE, AAC&U VALUE, IAM).
- **License hygiene:** never use PyMuPDF (AGPL), marker (GPL/OpenRAIL), or self-hosted Firecrawl /
  crawl4ai's Docker server (AGPL). Use Docling, pypdf, python-docx, openpyxl, and only crawl4ai's
  Apache-licensed library path.
- **No grade posts to Canvas without a teacher-approved grade in the data layer.** Original student
  files are immutable; annotated output is always written to a new destination. Canvas live writes
  are confined to `ZZ-GRADER-TEST*` courses, and bulk posting isn't complete until the Canvas
  Progress object reaches `completed`.
- The engine binds to loopback and requires `GRADER_ENGINE_TOKEN` on protected routes; SQLite uses
  WAL and the audit log is append-only.
