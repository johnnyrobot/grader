# Grader — Master Handoff

**Written:** 2026-07-11 · **For:** resuming this project in a fresh session (any model, any week)
**Read this first.** Then the phase doc you're working on (`docs/phases/P0.md` … `P5.md`).

---

## 0. Sixty-second orientation

**What we're building.** A free, open-source **macOS desktop app** that lets a college
instructor grade anything — essays, problem sets, labs, code, scanned handwriting — with a
**local** AI that learns to grade *the way that specific teacher grades*. Student work never
leaves the machine. It pulls submissions from Canvas, drafts rubric-aligned scores and
feedback, verifies cited references, writes annotated files back, and posts grades — with the
teacher approving every score.

**The two invariants that define the product** (violate either and it isn't Grader):
1. **No student PII leaves the device.** Outbound traffic is only: the user's own Canvas, model
   downloads, and cited-*source* metadata lookups (a DOI, a title, a URL). Enforced by an
   allowlist + a CI egress test.
2. **No grade posts without explicit teacher approval.** The AI drafts; the teacher decides.

**The core bet — and it's evidence-backed, not a hunch.** Calibration is **retrieval, not
fine-tuning**. The teacher's own previously-graded work is embedded and the most similar
examples are injected into each grading prompt as "anchors." This is literally how human
raters calibrate, it measurably closes the LLM–human gap, it works from the first graded
paper, it updates instantly, it's explainable ("I scored this a 4 because it resembles these
three you gave 4s"), and it ships zero per-teacher model artifacts. LoRA fine-tuning is a
*later* optimization (revisit at 1000+ exemplars), not the foundation. See
`docs/research/04-lora-and-mac-packaging.md`.

---

## 1. Where things actually stand (2026-07-13)

| Phase | Status |
|---|---|
| **Pre-work** | ✅ Complete — research, PRD, spec, data plan, citation fixtures, Canvas sandbox |
| **P0** Engine spike | 🟢 **Core risk retired, and it reversed a model choice.** `qwen3:8b` in Pydantic AI **Tool-Output mode: 100% schema success, 0 retries**, correct rank-ordering, 27–66s, **zero variance at temp 0**. **But Qwen3-VL is dead for vision** — see the ⚠️ below. |
| **P1** Calibration core | 🟢 **Complete.** 5 experiments run. Anchors lift QWK +0.127/+0.135 at k=6 (the sweet spot). Context capping has zero quality impact. k≥8 HURTS (too many anchors from a small pool). Override loop, rubric ingestion, refinement, health panel, cold-start rule all built. P5 ship gate blocked on teacher's own graded work. |
| **P2** App shell + UX | 🟢 **Implemented and evidence-backed.** Neuroinclusive light-first UI covers onboarding, queue, review, settings, and model manager. Frontend evidence passes with 14 component tests, 4 Playwright E2E tests, 16 visual-sweep screens, 47 required controls, and 167 visible controls. |
| **P3** Canvas round-trip | 🟢 **Score/comment beta scope passes.** Guarded live app E2E on `canvas.johnnyrobot.ai` course `106`, assignment `2841`, Canvas user `9`: import → teacher override/approval → post → Progress completed → score/comment read-back. Rubric-assessment posting remains disabled until local criteria map to real Canvas criterion IDs; attachment write-back still needs separate release evidence. |
| **P4** Documents & citations | 🟡 **Built; citations are experimental.** parse.py, handwriting.py, writeback.py, citations.py built. Citation fixture rerun is 20/29 (69%), 0 false positives, 3/3 retractions caught; acceptable only when visibly labeled experimental until enabled-mode accuracy reaches >=90%. |
| **P5** Hardening & release | 🟡 **Blocked by GitHub release preflight + updater release-channel + clean-machine updater path.** Public-proxy quality/fairness pass on the anchored Mohler ASAG artifact; privacy, performance, frontend, Rust, release-version, updater-manifest, signing/notarization all pass locally. Current `scripts/release_readiness.py` verdict is `blocked` with three blockers: `github_release=BLOCKED_GITHUB_RELEASE_PREFLIGHT` because required GitHub Actions release secrets are missing and the N+1 candidate version metadata has not been applied yet, `updater_channel=BLOCKED_UPDATER_CHANNEL` because GitHub `latest.json` returns HTTP 404, and `clean_machine=BLOCKED_CLEAN_MACHINE_EVIDENCE` missing `updater_path`. |

### The very next command

```bash
cd /Users/laccd/code/grader
uv run python scripts/updater_channel_preflight.py --current-version 0.1.0 --out artifacts/release-evidence/updater-channel-current.json
uv run python scripts/github_release_secrets.py --print-runbook > github-release-secrets-runbook.md
uv run python scripts/bump_release_version.py --target-version 0.1.1 --out artifacts/release-evidence/version-bump-plan.json
uv run python scripts/github_release_preflight.py --candidate-tag v0.1.1 --out artifacts/release-evidence/github-release-preflight-current.json --no-fail
```

Today `scripts/github_release_secrets.py` should print a private-shell runbook for configuring the
Developer ID `.p12`, Apple notarization credentials, and Tauri updater key.
`scripts/bump_release_version.py` should be run dry first, then with `--apply` only in the deliberate
N+1 release-prep commit. The GitHub release preflight should return
`BLOCKED_GITHUB_RELEASE_PREFLIGHT` until those required Actions secrets are configured and the N+1
version metadata has been applied, and the updater-channel preflight should return
`BLOCKED_UPDATER_CHANNEL` until a signed N+1 GitHub release publishes `latest.json` and the
reachable signed `Grader.dmg` asset. After both pass, run the clean-machine in-app `Install update
and restart` path and record `updater_path` evidence. Do **not** claim public beta readiness until
`scripts/release_readiness.py` reports `supervised_public_beta`.

---

## 2. The repo

```
grader/
├── RESEARCH.md              # the cited architecture report — WHY every choice was made
├── docs/
│   ├── HANDOFF.md           # you are here
│   ├── PRD.md               # full product requirements (16 user stories, FRs, NFRs, data models)
│   ├── SPEC.md              # one-page technical spec sheet
│   ├── DATA-PLAN.md         # every dataset + license + access path, by phase
│   ├── phases/P0..P5.md     # per-phase handoff: goal, done-criteria, how to run, gotchas
│   └── research/            # all raw research, preserved (8 reports + 2 raw workflow dumps)
├── engine/                  # the Python engine (P0/P1)
│   ├── grader/
│   │   ├── schema.py        # Rubric, GradeDraft — the grading contract
│   │   ├── agent.py         # Pydantic AI agent; temp 0; tool/prompted/native modes
│   │   ├── exemplars.py     # anchor store: embed, retrieve (similarity + score-band balance)
│   │   ├── metrics.py       # QWK, agreement, human ceiling
│   │   └── triage.py        # confidence -> review lanes (the time-saving mechanism)
│   ├── bench.py             # P0: which model/output-mode actually works
│   └── calibrate.py         # P1: do anchors help? (vs human ceiling)  <-- RUN THIS
├── fixtures/
│   ├── citations/           # 29 synthetic citation fixtures + generator (committed)
│   ├── canvas_sandbox.json  # live sandbox ids (gitignored)
│   └── corpora/             # downloaded datasets (gitignored — NC licenses)
├── scripts/
│   └── seed_canvas_sandbox.py   # seeds + verifies the Canvas round-trip
└── pyproject.toml           # uv-managed
```

**Run anything with `uv run python …`** — deps are already resolved (`pydantic-ai 2.9.0`,
`docling`, `pandas`, `httpx`).

---

## 3. Hard-won facts (do not re-derive these)

**Canvas** — all verified against a live sandbox:
- `bulk-update` is **asynchronous**. It returns a **Progress** object, *not* grades. Poll
  `/progress/:id` to `completed` (~4s) or you'll tell a teacher grades posted when they haven't.
- canvas-pp-cli's **read** envelope is `{meta, results}` — **not** `{data}`. Mutations use
  `{action, resource, success, data, partial_failure}`. Parsing the wrong key silently returns
  zero rows. (It fooled me once.)
- Submission attachments must be uploaded **as the student** (`?as_user_id=`); a teacher POSTing
  to `/users/:id/files` gets 403 even for the built-in Test Student. Needs `become_user`.
- Canvas returns **HTML unless you send `Accept: application/json`**.
- **Canvas Free-for-Teacher is permanently discontinued** (killed after the 2026 breach). Any
  plan assuming a free Canvas signup is out of date. We use the user's own instance.

**Models / runtime:**

⚠️ **P0 REVERSED THE RESEARCH'S VISION-MODEL PICK. Use MiniCPM-V, not Qwen3-VL.**
The research chose Qwen3-VL on licence + benchmarks + mlx-vlm LoRA support. Running it killed that:
`qwen3-vl:4b` emits **20,000+ characters of `<think>` reasoning** to answer *"score this 0–5"*, and
**the reasoning cannot be disabled** — Ollama's `think: false` is ignored, and Qwen's documented
`/no_think` switch is ignored (both tested). Measured on the same prompt, warm:

| Model | Latency | Eval tokens |
|---|---|---|
| **`minicpm-v4.6` (1.3B)** | **5.8s** | 610 |
| `gemma4:e2b` | 10.3s | 407 |
| `qwen3-vl:4b` | **140s** | 4,887 |

A 30-item handwritten batch: **2 hours on Qwen3-VL, ~3 minutes on MiniCPM-V.** Qwen3-VL's edge was
mlx-vlm **LoRA** support — but calibration is retrieval-first, so LoRA is a *v2* concern. Don't pay
24× latency now for a deferred capability. *(TODO: confirm MiniCPM-V's redistribution licence.)*

**Architectural consequence — transcribe, then grade.** Never ask the vision model to *judge*; ask
it to **transcribe**, then grade the text with the text model. Short output = no reasoning spiral,
and handwritten and typed work then share **one** grading path and one calibration mechanism:
`image --[grader-vision: transcribe]--> text --[grader-text: grade]--> GradeDraft`

- **Ollama works for v1**, but **MLX is required for v2**: Ollama's `ADAPTER` supports only
  Llama/Mistral/Gemma *text* models and is **broken for vision adapters** — it can never serve a
  fine-tuned VLM. MLX in-process is also the only Mac-App-Store-compatible shape.
- Keep the engine behind an **OpenAI-compatible client** so the Ollama→MLX swap doesn't touch
  agent code. (`grader/agent.py:build_model` is the only place that knows.)
- **Grade at temperature 0.** Reproducibility is >95% at temp 0, ~70% at temp 1. Temperature
  buys variance, not accuracy. Measure uncertainty by *sampling separately*, never by making the
  grade itself noisy.

**Licensing landmines (Grader is Apache-2.0, so these matter):**
- **Banned:** PyMuPDF (AGPL), marker (GPL + OpenRAIL revenue ceiling), self-hosted Firecrawl (AGPL).
- **Use instead:** Docling (MIT), pypdf (BSD), python-docx, openpyxl, crawl4ai (Apache — **library
  only**, never its Docker server: 2026 CVEs are all in that component).
- **Dev-only, never commit or ship:** PERSUADE 2.0, ELLIPSE, AAC&U VALUE, IAM (all NonCommercial).
  `.gitignore` already blocks `fixtures/corpora/`.

**🚨 Context window, not weights, decides whether the 16 GB tier works.** Ollama loads a model at
its **full default context window**. Qwen3-VL's default is **262,144 tokens**, so `qwen3-vl:4b` — a
4B model with ~3.3 GB of Q4 weights — was observed using **41 GB** (the KV cache is ~38 GB of that).
On a 16 GB machine it would simply fail to load.
Grading needs ~5,500 tokens (essay + rubric + 6 anchors + output). **Run `scripts/make_models.sh`**
to bake capped variants (`num_ctx=16384`, `temp=0`) — currently `grader-text:8b` and
`grader-vision:1.3b` — and **always grade against those**, never the raw base tags. Capped, the
vision model stays in the single-digit GB range.
Verify with `ollama ps` after a grade: single-digit GB = correct; tens of GB = wrong model.

**The dev machine is not the target.** This M2 Max has 64 GB. The PRD floor is **16 GB**. The
finding above is exactly why that matters: it looked perfectly fine on the dev box while being
impossible on the machine we promised to support.

---

## 4. Data you still need from the human

Public data validates the **engine**; only the teacher's own graded work validates
**"grades like me."** Nothing is blocked on this — P0/P1 run on public data today.

| When | What | Why irreplaceable |
|---|---|---|
| P1 | **20–30 of your graded items** (work + your scores) | Seeds the exemplar store; demos real calibration |
| P5 gate | **~100 graded items, ≥4 assignment types**, with rubrics | The QWK ship gate; proves higher-ed performance the K-12 corpora can't |
| P4 | **20–50 pages of your students' handwriting** | Real phone-photo conditions; public sets are cleaner than reality |

**Never commit any of it.** Real student work in a public repo is a FERPA problem even
de-identified. `fixtures/teacher-goldset/` and `fixtures/handwriting-samples/` are gitignored.

**One slow-path action worth starting early:** email `value@aacu.org` about using the 16 AAC&U
VALUE rubrics (the only *higher-ed-native* rubric set found). They're free but explicitly name
"assessment software" as needing prior written permission.

---

## 5. Live Canvas sandbox (already working)

Instance `canvas.johnnyrobot.ai`, credentials already on disk (canvas-pp-cli's
`credentials.toml` + `config.toml`).

| Thing | ID |
|---|---|
| Course | **106** `ZZ-GRADER-TEST-SANDBOX (safe to delete)` |
| Seeder assignment | **2836** (20 pts, `online_upload` + `online_text_entry`) |
| App E2E assignment | **2841** (used by `artifacts/release-evidence/canvas-live.json`) |
| Rubric | **189** (4 analytic criteria × 0–5) |
| Test Student | **8** |
| Fake cohort | **9–14** (`ZZGraderTest Student 01-06`, `.invalid` emails) |

Re-seed (idempotent): `python scripts/seed_canvas_sandbox.py --apply --students 6`
Tear down: `python scripts/seed_canvas_sandbox.py --teardown --course-id 106 --apply --delete-users`

Seeder verified end-to-end: submit w/ attachment → grade + rubric assessment + comment +
comment-file → read back → authenticated attachment download. Bulk-graded 6 students; gradebook
spread 100/60/20/0/100/60.

Current app-level public-beta Canvas evidence is narrower and cleaner: `scripts/live_canvas_e2e.py`
passed on course `106`, assignment `2841`, Canvas user `9`, with teacher override/approval,
post, Progress `completed`, score `2`, and one teacher feedback comment read back. Treat Canvas
rubric-assessment posting and attachment write-back as separate remaining evidence, not as covered
by that score/comment beta scope.

---

## 5b. The phase plans (each is a standalone build guide)

Every phase doc has the same shape: **goal · status · what's built · ▶ build sequence with runnable
commands · gotchas · done-when checklist.**

| Doc | Phase | Status | Its ▶ first command |
|---|---|---|---|
| [`phases/P0.md`](phases/P0.md) | Engine spike | ✅ complete | `uv run python engine/bench.py` |
| [`phases/P1.md`](phases/P1.md) | **Calibration core** | 🟢 complete | `uv run python engine/calibrate.py --n-test 20 --n-exemplars 60` (5 experiments run; k=6 is the sweet spot) |
| [`phases/P2.md`](phases/P2.md) | App shell + UX | 🟢 implemented; release evidence passes | `uv run python scripts/frontend_evidence.py --out artifacts/release-evidence/frontend.json` |
| [`phases/P3.md`](phases/P3.md) | Canvas round-trip | 🟢 score/comment beta scope passes | `uv run python scripts/live_canvas_e2e.py --dry-run` |
| [`phases/P4.md`](phases/P4.md) | Documents & citations | 🟡 in progress | `POST /api/parse` · `POST /api/citations/verify` · `POST /api/writeback/annotate` |
| [`phases/P5.md`](phases/P5.md) | Hardening & release | 🟡 blocked on GitHub release preflight, updater channel, and clean-machine updater path | `uv run python scripts/release_readiness.py --profile public_beta --expected-version 0.1.0 --out artifacts/release-readiness` |

## 6. How to resume in a new session

1. Read this file, then `RESEARCH.md` §1 (the stack table) — that's the whole architecture in
   two tables.
2. Read the phase doc for wherever you are (`docs/phases/P5.md` right now).
3. Run the next command in §1 above, then regenerate `scripts/release_readiness.py` evidence.
4. **Don't re-research.** Everything in `docs/research/` was adversarially verified by a
   107-agent run; the claims carry vote counts and source URLs. If you find yourself about to
   search the web for "best local VLM," read `docs/research/01-verified-findings.md` instead.

**Decisions already made — do not relitigate without new evidence:**
higher-ed audience · free & open-source (Apache 2.0) · Tauri shell · Ollama v1 → MLX v2 ·
MiniCPM-V transcription + Qwen3 text grading · Docling · Pydantic AI v2 · retrieval-first calibration · queue → split-view UX ·
16 GB floor · all four v1 must-haves (Canvas, citations, handwriting, annotated write-back).
