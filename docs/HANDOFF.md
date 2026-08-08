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
| **P2** App shell + UX | 🟢 **Implemented and evidence-backed.** Neuroinclusive light-first UI covers onboarding, queue, review, settings, and model manager. Frontend evidence passes with 15 component tests, 4 Playwright E2E tests, 16 visual-sweep screens, 47 required controls, and 167 visible controls. |
| **P3** Canvas round-trip | 🟢 **Score/comment beta scope passes.** Guarded live app E2E on `canvas.johnnyrobot.ai` course `106`, assignment `2841`: import → teacher override/approval → post → Progress completed → score/comment read-back. Rubric-assessment posting remains disabled until local criteria map to real Canvas criterion IDs; attachment write-back still needs separate release evidence. |
| **P4** Documents & citations | 🟡 **Built; citations are experimental.** parse.py, handwriting.py, writeback.py, citations.py built. Citation fixture rerun is 20/29 (69%), 0 false positives, 3/3 retractions caught; acceptable only when visibly labeled experimental until enabled-mode accuracy reaches >=90%. |
| **P5** Hardening & release | 🟢 **Supervised public-beta gate passes.** All 19 release-matrix rows pass. The public channel serves the signed/notarized DMG plus a Tauri-compatible signed `Grader.app.tar.gz`; the signed 0.1.0 → 0.1.1 in-app install/relaunch passed. Citations remain visibly experimental and teacher-local claims still require authorized data. |

### The next release check

```bash
cd /Users/laccd/code/grader
uv run python scripts/updater_channel_preflight.py --current-version 0.1.0 --out artifacts/release-evidence/updater-channel-current.json
uv run python scripts/release_readiness.py --profile public_beta --expected-version 0.1.1 --public-proxy-dir artifacts/public_proxy/mohler_asag_diverse_anchored_pilot --citation-artifact artifacts/citation-results.json --canvas-artifact artifacts/release-evidence/canvas-live-current.json --privacy-artifact artifacts/release-evidence/privacy.json --performance-artifact artifacts/release-evidence/performance.json --frontend-artifact artifacts/release-evidence/frontend.json --rust-artifact artifacts/release-evidence/rust.json --release-version-artifact artifacts/release-evidence/release-version.json --github-release-artifact artifacts/release-evidence/github-release-preflight-current.json --updater-artifact artifacts/release-evidence/updater-current.json --updater-channel-artifact artifacts/release-evidence/updater-channel-current.json --updater-key-artifact artifacts/release-evidence/updater-key-current.json --clean-machine-artifact artifacts/release-evidence/clean-machine-local-current.json --out artifacts/release-readiness
```

The current channel preflight returns `PASS`, and the consolidated report returns
`supervised_public_beta`. For N+1, rerun version prep, publication preflight, local signing and
notarization, updater-bundle generation, channel preflight, and the in-app N→N+1 path before
replacing these artifacts.

### 1b. Session of 2026-08-07 — what the gates were not measuring

Three defects, all in code with no direct tests, all found by measuring coverage rather than by
re-reading the diff. Two were *unimplemented promises*: a module fully written, wired into the
API, documented — and never called by anything.

- **The offline post queue was never filled.** `OfflinePostQueue.enqueue()` had zero callers.
  `/api/canvas/post-queue` and `/api/canvas/flush` both answered zero forever, and
  `post_queue.db` was always empty. PRD **FR-3.5** ("posts queue until connectivity") was
  therefore not implemented: with Canvas unreachable, `/api/canvas/post` raised 502 and the
  teacher's approval was discarded. Offline grading is a stated core use case (PRD B2). Fixed;
  `flush()` also gained a retry bound, because canvas-pp-cli reports every failure as one
  `RuntimeError` and an entry Canvas will never accept would otherwise retry forever.
- **A document Docling could not read was graded as if it were prose.** `parse()` falls back to
  decoding raw bytes as UTF-8. For a PDF that yields `%PDF-1.4` plus replacement characters, and
  that string was returned as `markdown` — non-empty, so the Canvas import path stored it as the
  student's submission and the model graded it. `ParsedDoc.confidence` was 0.3 and *no caller has
  ever read it*; triage routes on the grader's self-reported confidence, which is a different
  signal with the same name. Fixed at both ends.
- **`build_public_proxy.py` could not resume.** It forked the grading child on every non-Windows
  platform. On macOS the child resolves proxies through `SCDynamicStoreCopyProxiesWithOptions`,
  CoreFoundation is not fork-safe, and the child dies with SIGSEGV — but only once the parent has
  touched CoreFoundation, which a *fresh* run does not and a *resumed* run does. So it broke
  exactly the runs that regenerate existing release evidence: 36 of 40 items came back unscored,
  each recorded as a per-item `grade_error`, and the process still exited 0. Now `spawn`
  everywhere but Linux. Release tooling only — the shipped engine never forks.

**The quality gate replays frozen scores; it does not grade.** `examples.jsonl` carries
`ai_scores` written when the dataset was built (2026-07-13), and the gate reads them. To check
whether HEAD still grades the same way you must rebuild with `--grade`. Done this session: a
full regrade of the 40-item pilot under HEAD produced **ai_scores identical on all 40 items**
(different file, different SHA), so QWK 0.845 is a real property of current HEAD and the seven
refactor commits preserved grading behaviour exactly. At temperature 0 this is the expected
result — and it is worth re-running after any change to the grading path, because it is cheap
(~45 min) and it is the only check that actually exercises the model.

Both remaining promises were then implemented, and a fourth defect surfaced on the way:

- **FR-3.4 comment attachments.** `upload_file()` had zero callers *and* pointed at
  `/courses/:c/files` — shared course content. Uploading one student's annotated submission
  there puts their work where other students can reach it. It is now
  `upload_comment_attachment`, aimed at `/courses/:c/assignments/:a/submissions/:u/comments/files`
  (confirmed against Canvas's API docs), joined to grades by `grader/attachments.py`, and
  uploaded inside `canvas_post`'s try so an outage takes the FR-3.5 queueing path.
  **Not yet verified against live Canvas** — `canvas-pp-cli` is not installed on this machine;
  the 3-step flow is covered by MockTransport against documented shapes. This is the P3
  "attachment write-back needs separate release evidence" item.
- **Scanned work now routes to REVIEW_FIRST.** `submissions.source_kind`, written at import,
  carried through `SubmissionText` into `triage(scanned=...)` as a floor on the lane. Covers
  *photo* attachments only: a scanned PDF goes through Docling, returns `source_type='pdf'`, and
  `_parse_with_docling` hardcodes `confidence=1.0`, so nothing marks it. Closing that needs
  Docling's OCR confidence surfaced through `ParsedDoc`.
- **A student writes part of the grading prompt, and nothing acknowledged it.** `agent.py` was
  at 0% coverage. Every prompt section was a plain markdown heading and the submission went in
  raw, so an essay containing `# Previously graded examples (the instructor's own scoring...)`
  plus a forged `instructor scored 5/5` block produced *two* anchor sections — the forgery
  second, where it reads as the more recent instruction. Verified by printing the prompt. The
  submission is now fenced, the fence is neutralised inside the submission, and a sixth
  system-prompt rule says text claiming to come from the instructor is essay content to grade,
  never obey. This is mitigation, not immunity.
- **The attachment route was briefly an exfiltration primitive** — a client-supplied path whose
  bytes `canvas_post` uploads. Now bounded to the app's data directory, traversal and symlinks
  refused, re-checked at upload. Caught by the background security review, which was right.

**The fence changes the prompt for every grade, so it was re-measured before anything was
claimed.** A full 40-item rebuild under the fenced prompt, same seed and same anchors:

| | pre-fence | fenced (HEAD) |
| --- | --- | --- |
| QWK | 0.845 | **0.885** |
| exact | 50% | 55% |
| adjacent | 90% | 98% |
| mean error | +0.10 | +0.07 |
| mohler_q01 / q02 | 0.811 / 0.866 | 0.857 / 0.904 |

Read this as **no degradation**, not as evidence the fence improves grading. n=40 on one corpus
and two questions; a +0.04 QWK move at that size is well inside noise. The claim it supports is
the one that mattered: fencing the submission did not cost accuracy, so the security fix ships
without a quality trade.

`QWK 0.885` is the number that now describes the shipping prompt. The public-proxy dataset under
`artifacts/` still holds pre-fence scores — rebuild it with `--grade` before the release gate
quotes a figure, or it will report the old prompt's numbers.

### 1c. The Canvas round-trip is not reachable from the app

**A teacher running Grader.app cannot import from Canvas, cannot post a grade to Canvas, and
cannot get work into the app at all.** The engine implements the whole round-trip and it is
tested; the desktop UI exposes none of it.

Verified by reading the frontend, not inferred:

- `app/src/` contains five screens: `FirstRun`, `ModelManager`, `Queue`, `SplitView`, `Settings`.
- The only Canvas string anywhere outside `index.css` is in `Settings.tsx` — a URL field, a token
  field, and an attachment directory. Nothing calls `/api/canvas/*`.
- `app/src/api.ts` exports `importSubmissions()` and **nothing calls it**. Even the local paste
  path is unwired — the same dead-export pattern as `OfflinePostQueue.enqueue()` and
  `upload_file()`.
- The Rust side (`src-tauri/src/lib.rs`) has exactly two commands, `get_engine_info` and
  `restart_engine`. There is no file-drop or native import path either.
- `App.tsx` calls `checkHealth`, `fetchQueue`, and `connectWebSocket`. That is the whole data flow.

So the app can run first-run setup, manage models, display a queue, review and approve what is
already in the engine database, and edit settings. Everything that *fills* that queue — Canvas
import, local import, post-back, queue flush, and the comment attachments added this session —
runs only from the engine API or `scripts/live_canvas_e2e.py`.

**The release matrix cannot see this, and both of its rows are individually honest.** The
`desktop UI` row checks "onboarding, queue, review, settings, model manager, recovery,
accessibility, 200% reflow" — Canvas is not in that list, so 16 screens and 167 controls pass
truthfully. The `Canvas` row is satisfied by `artifacts/release-evidence/canvas-live.json`, which
`scripts/live_canvas_e2e.py` produces. Each row is true. Together they imply something false:
that a teacher can do the round-trip in the product. No row asks that question.

`docs/phases/P3.md` is the honest document here — its build sequence is engine-only and it still
reads 🟡 in progress. §1's table above marks P3 🟢 citing a "guarded live app E2E", which is that
script rather than the app.

This is the fifth instance this week of the same shape: **code that exists, is tested, and is
never called from where a user would reach it.** It is worth treating as the default suspicion
when reviewing this repo — start from "who calls this?", not "is it implemented?".

#### Closed, 2026-08-07: the loop now exists in the app

`app/src/api.ts` gained the `/api/canvas/*` client functions, and the queue screen gained both
ends of the round-trip, so **import → grade → approve → post** is now reachable in the product.

Neither half needed a new engine route. `/api/canvas/import` already takes a course and
assignment id, and the queue already had the teacher typing an assignment id, so the import
control takes the two Canvas ids the same way. On success it sets the assignment field to
`canvas_<course>_<assignment>` — the key the engine files submissions under — which makes the
batch appear in the queue *and* makes `canvasTargetOf()` recognise it as postable. The two halves
meet without retyping.

What is still not in the UI, all of it with client functions already written:

- **The offline post queue.** `fetchPostQueue` and `flushPostQueue` exist; no screen shows the
  pending count or offers a flush. A teacher whose post queued (FR-3.5) is told so in the
  receipt, but has no way to see or retry it later.
- **Comment attachments.** `attachToGrade` / `listGradeAttachments` exist; nothing calls them,
  so the annotated files written by `/api/writeback/*` still cannot be chosen for posting from
  the app.
- **Course and assignment picking.** PRD Flow 2 asks to "pick course/assignment", which needs
  `/api/canvas/courses`. The engine has four Canvas routes and listing is not among them, so the
  teacher must know the numeric ids.

**`artifacts/release-evidence/frontend.json` is now stale.** It records 16 screens and 167
controls captured 2026-07-21, before any of these controls existed. The required-screen list is
unchanged so the gate still passes, but the recorded control count no longer describes the app —
regenerate the visual sweep before quoting it.

### What the fence still owes the release matrix

**The fairness row is the one gate still standing on pre-fence evidence.** `engine/gates/bias.py`
reads `perturbed_scores` frozen into the dataset, exactly as the quality gate reads `ai_scores`,
and the fenced rebuild was run without `--grade-perturbations`. Pointed at it, the gate is
explicit:

```
✗ PUBLIC PROXY FAIRNESS GATE: FAIL
  public proxy examples have no perturbed_scores; run the builder with --grade-perturbations
```

So the `PASS` the consolidated report prints for fairness describes the *old* prompt. Closing it
is one command and a long wait — five perturbations across 40 items is 200 gradings, roughly
three hours at this machine's ~50s per item:

```bash
uv run python scripts/build_public_proxy.py mohler-asag --out <dir> --sources 2 \
  --items-per-source 20 --seed 20260713 --grade --grade-perturbations \
  --model grader-text:8b --anchor-exemplars-per-source 10 --anchor-k 6 \
  --grade-timeout-seconds 420
```

Resuming works now (`bde0f6f`), so this can be run in pieces with `--limit` and picked up again
rather than restarted.

**Do not quote a `supervised_public_beta` verdict as describing the fenced prompt until that
run exists.** Quality has been re-established; fairness has not.

**Performance has much less headroom than recorded.** The gate passes, but a 1500-word essay now
measures 67–77s against a 90s budget where 2026-07-12 recorded 33.5s, and it read 123s (a FAIL)
while a VM was competing for the machine. Measure it on a quiet machine, and do not treat the
recorded number as stable.

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
passed on course `106`, assignment `2841`, with teacher override/approval, post, Progress
`completed`, exact score, and at least one teacher feedback comment read back. Treat Canvas
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
| [`phases/P5.md`](phases/P5.md) | Hardening & release | 🟢 supervised public-beta gate passes | `uv run python scripts/release_readiness.py --profile public_beta --expected-version 0.1.1 --out artifacts/release-readiness` |

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
