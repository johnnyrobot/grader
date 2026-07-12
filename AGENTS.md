# PROJECT KNOWLEDGE BASE

**Generated:** 2026-07-11
**Mode:** init-deep (update)

## OVERVIEW

Local-first macOS desktop app for college instructors: AI drafts rubric-aligned grades, teacher approves. Python engine (Pydantic AI v2 + Ollama) is working; Tauri/React shell not started. Student data never leaves the device.

## STRUCTURE

```
grader/
├── engine/                  # grading engine (P0/P1)
│   ├── grader/              # the Python package: schema, agent, exemplars, metrics, triage
│   ├── bench.py             # P0: model/output-mode spike
│   └── calibrate.py         # P1: anchor-vs-baseline experiment (NEXT COMMAND)
├── docs/                    # PRD, SPEC, DATA-PLAN, HANDOFF, phase guides P0–P5, research/
│   ├── phases/P0..P5.md     # per-phase build guides with runnable commands
│   └── research/            # 8 verified research reports + 2 raw workflow dumps
├── fixtures/
│   ├── citations/           # 29 synthetic citation fixtures + generator (committed)
│   └── canvas_sandbox.json  # live sandbox ids (gitignored)
├── scripts/
│   ├── make_models.sh       # bake context-capped Ollama variants (CRITICAL — see below)
│   └── seed_canvas_sandbox.py  # Canvas round-trip seeder/verifier
├── humanizer-main/          # separate subproject (agent skill) — has own AGENTS.md
├── RESEARCH.md              # cited architecture report (WHY every choice was made)
├── pyproject.toml           # uv-managed, package=false
└── uv.lock
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Understand the product | `docs/HANDOFF.md` then `RESEARCH.md` §1 | 60-second orientation → stack table |
| Run the engine | `engine/bench.py`, `engine/calibrate.py` | `uv run python engine/calibrate.py --n-test 20` |
| Add a grading feature | `engine/grader/` | schema.py = contract, agent.py = LLM, triage.py = routing |
| Citation verification | `fixtures/citations/` | 29 fixtures with expected verdicts |
| Canvas integration | `scripts/seed_canvas_sandbox.py` | live sandbox course 106 |
| Phase status | `docs/phases/P0..P5.md` | each has goal, status, build sequence, gotchas |
| Data licensing | `docs/DATA-PLAN.md`, `.gitignore` | NC corpora gitignored; never commit student work |

## CODE MAP

| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `grade()` | function | `engine/grader/agent.py:122` | Main entry: rubric+submission → GradeDraft. Never raises. |
| `build_agent()` | function | `engine/grader/agent.py:72` | Constructs Pydantic AI Agent in tool/native/prompted mode |
| `Rubric` | model | `engine/grader/schema.py:59` | Grading contract; `.render()` produces prompt text |
| `GradeDraft` | model | `engine/grader/schema.py:98` | Validated LLM output; `.validate_against(rubric)` → ModelRetry |
| `CriterionType` | enum | `engine/grader/schema.py:16` | objective/analytic/open_ended — drives triage bias weighting |
| `Flag` | enum | `engine/grader/schema.py:80` | low_confidence, citation_issues, needs_human, etc. |
| `triage()` | function | `engine/grader/triage.py:64` | Confidence → review lane; escalates to consistency vote |
| `Lane` | enum | `engine/grader/triage.py:34` | review_first / standard / high_confidence |
| `ExemplarStore` | class | `engine/grader/exemplars.py:95` | SQLite-backed anchor store; embed + retrieve (similarity + band balance) |
| `render_anchors()` | function | `engine/grader/exemplars.py:200` | Formats retrieved exemplars for prompt injection |
| `quadratic_weighted_kappa()` | function | `engine/grader/metrics.py:19` | QWK — the standard agreement metric |
| `agreement()` | function | `engine/grader/metrics.py:68` | AI-vs-human comparison; signed mean_error = bias direction |
| `human_ceiling()` | function | `engine/grader/metrics.py:93` | Human-vs-human agreement — the bar AI chases |
| `GradeRun` | dataclass | `engine/grader/agent.py:48` | One grading attempt with telemetry (draft, mode, seconds, retries) |

## CONVENTIONS

- **`uv run python`** for all execution — deps managed by uv, `package = false`
- **Temperature 0** for grading; uncertainty measured by separate sampling, never noisy grades
- **Schema is the reliability mechanism** — push constraints into Pydantic models, not prompt prose
- **`grade()` never raises** — a failed grade is a `GradeRun` with `error` set; the queue surfaces it
- **OpenAI-compatible client** — `agent.py:build_model` is the only place that knows about Ollama; MLX swap touches nothing else
- **Always use capped model variants** (`grader-text:8b`, `grader-vision:1.3b`), never raw base tags
- **Vision transcribes, text grades** — never let the VLM judge; short output avoids reasoning spiral
- Python ≥3.12, `from __future__ import annotations` in all modules

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER run crawl4ai's Docker server** — 2026 RCE/SSRF/auth-bypass CVEs are all in that component (library-only)
- **NEVER commit student work** — FERPA violation even de-identified. `.gitignore` blocks `fixtures/teacher-goldset/`, `*.student.*`, `private/`
- **NEVER commit NC-licensed corpora** (PERSUADE 2.0, ELLIPSE, AAC&U VALUE, IAM) — gitignored under `fixtures/corpora/`
- **NEVER use PyMuPDF (AGPL), marker (GPL/OpenRAIL), or self-hosted Firecrawl (AGPL)** — use Docling (MIT), pypdf (BSD) instead
- **NEVER pipe `ollama list` into `grep -q` under `set -o pipefail`** — SIGPIPE makes installed models look missing (see `make_models.sh:25`)
- **NEVER let the VLM grade directly** — transcribe to text first, then grade with text model
- **NEVER use Qwen3-VL** — emits 20k+ chars of un-disableable ` IMD` reasoning (140s/item vs MiniCPM-V 5.8s)
- **Do not re-research** — `docs/research/` was adversarially verified by a 107-agent run; claims carry vote counts + source URLs
- **Do not relitigate locked decisions** — higher-ed audience, Apache 2.0, Tauri shell, Ollama v1→MLX v2, retrieval-first calibration, 16 GB floor

## UNIQUE STYLES

- Every code decision cites research: arXiv IDs in docstrings justify rubric scale (3-5 levels), temp 0, QWK metric, anchor retrieval, criterion-type bias weighting
- `CriterionType` enum drives triage: `open_ended` criteria never auto-clear (style bias is prompt-resistant, arXiv 2603.18765)
- `mean_error` is signed — positive = AI lenient, negative = AI harsh. LLM judges drift lenient; symmetric metrics hide that
- Exemplar retrieval is band-balanced: pure nearest-neighbour returns all A-papers; round-robin across score bands keeps full range visible
- Canvas sandbox uses `ZZ-GRADER-TEST` marker in course name as a safety guard — script refuses to operate on unmarked courses

## COMMANDS

```bash
# Setup
uv sync
ollama pull qwen3:8b && ollama pull minicpm-v4.6:latest
./scripts/make_models.sh                    # bake capped variants (CRITICAL for 16 GB tier)

# Run
uv run python engine/bench.py               # P0: model/mode spike
uv run python engine/calibrate.py --n-test 20 --n-exemplars 60  # P1: NEXT COMMAND

# Canvas sandbox
python scripts/seed_canvas_sandbox.py --apply --students 6
python scripts/seed_canvas_sandbox.py --teardown --course-id 106 --apply

# Fixtures
python fixtures/citations/generate_fixtures.py
```

## NOTES

- **Status:** P0 complete (engine works, model choice reversed to MiniCPM-V). P1 built but not run. P2–P5 not started.
- **Highest-value next action:** `uv run python engine/calibrate.py --n-test 20 --n-exemplars 60` — proves the core product bet (do teacher anchors help?)
- **16 GB context trap:** Ollama loads models at full default context. Qwen3-VL's 262k default made a 4B model consume 41 GB. Always use capped variants from `make_models.sh`.
- **Canvas gotchas:** bulk-update is async (poll Progress, not grades); returns HTML without `Accept: application/json`; submission files need `as_user_id` masquerade; Canvas Free-for-Teacher is dead.
- **`humanizer-main/`** is a separate agent-skill subproject with its own AGENTS.md — don't conflate with grader.
- No tests exist yet. P5 will add CI gates: quality (QWK≥0.70), bias (style perturbation delta), egress (network allowlist audit).
- LSP: ruff installed for Python; basedpyright not installed (no document symbol support this session).