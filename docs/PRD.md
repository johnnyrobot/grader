# Grader — Product Requirements Document

**Document Version:** 1.0
**Last Updated:** 2026-07-10
**Status:** Draft
**Owner:** Johnny (johnny@johnnyrobot.ai)
**Companion docs:** `docs/SPEC.md` (technical spec sheet) · `RESEARCH.md` (cited research basis)

---

## Executive Summary

**TL;DR:** Grader is a free, open-source macOS desktop app that lets college instructors grade anything — essays, problem sets, lab reports, code, scanned handwritten work — with a local AI assistant that learns to grade *the way that specific teacher grades*. All inference runs on-device; student work never leaves the machine. It pulls submissions from Canvas, drafts rubric-aligned scores and narrative feedback, verifies cited references, and writes annotated files and grades back — with the teacher approving every score.

**Problem Statement:** Grading is the largest unautomated time sink in teaching (routinely 10–20 hrs/week for writing-heavy courses). Cloud AI graders exist but (a) require sending student PII to third parties — a FERPA minefield most institutions prohibit; (b) grade like a generic model, not like the instructor; and (c) position the AI as the authority rather than the teacher. Instructors need the time savings without surrendering privacy, judgment, or accountability.

**Proposed Solution:** A fully-local grading copilot: a Tauri desktop shell over a Python agent engine (Pydantic AI v2) driving local models (Qwen3-VL vision + Qwen3 text via Ollama, migrating to MLX in-process). The teacher is the source of truth — their rubric, their source materials, and their own previously-graded work (retrieved as "anchor exemplars") calibrate every AI draft. Confidence-routed review queues concentrate teacher attention on genuinely uncertain items. Canvas integration (via the existing canvas-pp-cli) closes the loop from submission pull to grade post.

**Success Metrics (top-line):**
1. AI-draft vs teacher-final agreement: Quadratic Weighted Kappa (QWK) ≥ 0.70 after 25 graded exemplars per assignment type.
2. ≥ 40% of items auto-cleared into the "high-confidence" lane (teacher spot-checks rather than fully re-grades) without agreement loss.
3. ≥ 50% reduction in self-reported grading time per assignment by week 4 of use.
4. Zero student-PII bytes in outbound network traffic (verifiable; automated egress test in CI).
5. Teacher override rate trending downward across the first 5 assignments (calibration is working).

---

## 1. Product Overview

### 1.1 Vision & Objectives

**Vision:** Every instructor has a private teaching assistant that grades exactly like they do, on their own machine, for free — and gets better every time they grade one more paper.

**Primary Objectives:**
- **O1 — Trustworthy drafts:** produce rubric-aligned draft scores + feedback that a teacher accepts unmodified ≥ 60% of the time by the 5th assignment.
- **O2 — Absolute privacy:** all student content processed on-device; the only outbound traffic is cited-source metadata lookups (a DOI, a title, a URL) and model-weight downloads.
- **O3 — Teacher-as-source-of-truth:** every mechanism (rubrics, exemplars, overrides, rubric refinement) flows *from* the teacher; the AI never silently overrides and never posts a score without approval.
- **O4 — Whole-workflow coverage:** Canvas in → parse any document → grade → annotate files → Canvas out, in one app.

### 1.2 Target Audience

**Primary Users:**
- **Community-college / higher-ed instructor ("Prof. Rivera"):** teaches 4–5 sections, 120+ students, Canvas LMS, mixed submissions (DOCX essays, PDF lab reports, photographed handwritten quizzes). Pain: grading load, feedback quality guilt, institutional AI-privacy policies. Comfort: moderate tech skill; owns a 16 GB M-series MacBook.
- **Adjunct instructor ("Sam"):** teaches across 2 institutions, unpaid grading hours, needs speed above all; must not violate either institution's data policies.

**Secondary Users/Stakeholders:**
- Department chairs / assessment coordinators (rubric consistency, audit trails).
- Campus IT & compliance (FERPA posture; local-only architecture is the selling point).
- Students (recipients of faster, richer feedback — never direct users of the app).
- OSS contributors (secondary audience for docs, architecture cleanliness, plugin points).

### 1.3 Scope

**In Scope (v1 / MVP):**
- Local grading engine: rubric-conditioned scoring + narrative feedback drafting with structured outputs.
- Teacher calibration: rubric ingestion, source-material grounding, anchor-exemplar retrieval, override-driven rubric refinement.
- Canvas: pull courses/assignments/submissions (incl. attachment download), post grades/rubric assessments/comments (single + bulk).
- Document read: PDF, DOCX, PPTX, XLSX, HTML, images, plain text/Markdown/LaTeX; scanned/handwritten work via vision pipeline.
- Document write: margin comments into .docx, annotations into .pdf, cell comments into .xlsx, standalone feedback reports.
- Citation verification: DOI/bibliographic resolution + web fetch + local claim-check.
- Review UX: confidence-routed queue → SpeedGrader-style split view; audit trail.
- Pedagogy assistant: feedback-quality checks (Hattie criteria) and instructor-facing suggestions.
- Model management: first-run model download, hardware-tiered defaults, Ollama sidecar runtime.

**Out of Scope (Future Phases):**
- Per-teacher LoRA fine-tuning (v2; requires MLX runtime + 1000+ exemplars).
- MLX in-process runtime (v2).
- Other LMSes (Moodle, Blackboard, Google Classroom), Windows/Linux builds.
- K-12 mode (COPPA workflows, parent reporting).
- Plagiarism/AI-authorship detection (adjacent product; explicitly deferred).
- Multi-teacher collaboration / shared rubric libraries (community repo later).

**Non-Goals:**
- Grader will **never auto-post a score without explicit teacher approval** (per-item or reviewed-batch).
- Grader is **not a proctoring or surveillance tool.**
- Grader does **not send student work to any cloud AI**, ever, including as an optional mode in v1.
- Grader does not attempt summative institutional assessment (accreditation scoring, placement).

---

## 2. User Stories & Requirements

### 2.1 Core User Stories

**Epic A: Setup & Calibration ("Teach it how I grade")**

- **A1 — Rubric import.** As an instructor, I want to import my rubric (from Canvas or by pasting/building one), so the AI grades against *my* criteria.
  - *AC:* Given a Canvas assignment with a rubric, when I link the assignment, then criteria/levels/points appear editable in the app; given a pasted rubric (text/table), when I confirm the parsed structure, then it is stored as an analytic rubric with criterion-level anchors.
  - *AC:* The app warns when a rubric uses scales wider than 0–5 per criterion and offers a restructure (research: wide scales measurably reduce agreement).
  - *Priority:* Must-Have. *Effort:* M.
- **A2 — Source-of-truth materials.** As an instructor, I want to attach source materials (readings, answer keys, prompts, lecture notes) to an assignment, so grading is grounded in what I actually taught.
  - *AC:* Attached files are parsed and retrievable as grading context; answer-key conditioning is used as a "weak hint" with over-crediting monitoring (FP guard).
  - *Priority:* Must-Have. *Effort:* M.
- **A3 — Grade-like-me exemplars.** As an instructor, I want to grade a few submissions myself (or import past graded work), so the AI calibrates to my standards.
  - *AC:* Given ≥ 3 teacher-graded exemplars, when the AI drafts a grade, then the k most similar exemplars (verdict-balanced) are injected as anchors and the draft cites which exemplars informed it ("resembles these two you scored 4").
  - *Priority:* Must-Have. *Effort:* L.
- **A4 — Learn from my overrides.** As an instructor, when I change an AI score, I want the system to learn, so the same mistake doesn't repeat.
  - *AC:* Every override becomes an exemplar immediately; after ≥ 3 overrides sharing a pattern, the app proposes a rubric-text amendment (Reflect-and-Revise) that I can accept/edit/reject; accepted amendments are versioned.
  - *Priority:* Must-Have. *Effort:* M.

**Epic B: Canvas Round-Trip**

- **B1 — Connect Canvas.** As an instructor, I want to connect with my Canvas URL + API token, so my courses appear.
  - *AC:* Token stored in macOS Keychain; `doctor`-style connectivity check with actionable errors; courses/assignments/rosters listed and locally cached (SQLite mirror).
  - *Priority:* Must-Have. *Effort:* S.
- **B2 — Pull a grading batch.** As an instructor, I want to pull all ungraded submissions for an assignment (files included), so I can grade offline.
  - *AC:* Given an assignment, when I click Import, then submission records + all attachments download (authenticated fetch of attachment URLs), parse, and enqueue; partial failures reported per-student, retryable.
  - *Priority:* Must-Have. *Effort:* M.
- **B3 — Post back.** As an instructor, I want approved grades, rubric assessments, comments, and annotated files posted to Canvas, so students see results without me touching SpeedGrader.
  - *AC:* Approved items post via `submissions update`/`bulk-update` with rubric assessment + text comment; annotated files upload as comment attachments (3-step upload); post results shown per-student with partial-failure handling; nothing posts without approval.
  - *Priority:* Must-Have. *Effort:* M.

**Epic C: Grading & Review**

- **C1 — AI drafts.** As an instructor, I want each submission scored per-criterion with drafted narrative feedback, so I start from a draft, not a blank.
  - *AC:* Output is schema-validated (criterion scores + rationale + feedback + confidence + flags); grading runs at temperature 0; invalid JSON auto-retries; hard failures land in the review queue marked "needs human," never a silent zero.
  - *Priority:* Must-Have. *Effort:* L.
- **C2 — Confidence-routed queue.** As an instructor, I want submissions triaged by AI certainty, so I spend time where the AI is unsure.
  - *AC:* Items sort into lanes — Review First (low confidence / low consistency / bias-sensitive), Standard, High Confidence (spot-check); low self-reported confidence triggers a 3-sample consistency vote before lane assignment; essay-type criteria get a review-weight boost (documented style-bias risk).
  - *Priority:* Must-Have. *Effort:* M.
- **C3 — Split-view grading.** As an instructor, I want submission left / rubric + AI draft right, so I can verify and adjust fast.
  - *AC:* Keyboard-first (next/prev, accept, adjust score, edit feedback); every AI rationale links to the exact rubric criterion and anchor exemplars; original file viewable (rendered PDF/DOCX/image) alongside parsed text.
  - *Priority:* Must-Have. *Effort:* L.
- **C4 — Handwritten work.** As an instructor, I want to grade photographed/scanned handwritten work, so paper quizzes get the same treatment.
  - *AC:* Images route through Apple Vision OCR first-pass + the grading VLM reading the page directly; low OCR-confidence regions are flagged visually; transcription is editable before grading.
  - *Priority:* Must-Have. *Effort:* L.
- **C5 — Citation verification.** As an instructor, I want each reference in a paper checked (exists? matches? says what's claimed?), so I catch fabricated or misused sources.
  - *AC:* Per-reference verdict: Verified / Metadata mismatch / Not found / Claim unsupported / Unreachable, with evidence links; academic refs resolve via DOI/Crossref/Semantic Scholar before any web search; outbound queries contain zero student-identifying data.
  - *Priority:* Must-Have. *Effort:* L.
- **C6 — Annotated file return.** As an instructor, I want feedback written into the student's file (docx margin comments, PDF annotations), so students get feedback in context.
  - *AC:* .docx comments anchored to relevant passages (python-docx); .pdf sticky-note/FreeText annotations (pypdf); .xlsx cell comments (openpyxl); fallback = standalone feedback report (docxtpl/Markdown→PDF); teacher previews before anything is saved/posted.
  - *Priority:* Must-Have. *Effort:* M.

**Epic D: Trust, Audit & Pedagogy**

- **D1 — Audit trail.** As an instructor (and for my chair), I want every grade's provenance recorded, so I can defend any grade.
  - *AC:* Per-item immutable log: model + version, prompt/rubric version, retrieved exemplars, AI draft, confidence, teacher edits, timestamps; exportable per-assignment (CSV/JSON).
  - *Priority:* Must-Have. *Effort:* S.
- **D2 — Feedback quality gate.** As an instructor, I want drafted comments checked for pedagogical quality, so students get feedback that helps them improve.
  - *AC:* Drafts evaluated against Hattie criteria (goal-linked, thinking-causing, understandable, actionable); failing drafts regenerate once, then flag.
  - *Priority:* Should-Have. *Effort:* M.
- **D3 — Pedagogy suggestions.** As an instructor, I want the app to surface patterns ("18/30 students missed criterion 3 — consider re-teaching X") so grading informs teaching.
  - *AC:* Post-batch summary: per-criterion distributions, common misconception clusters, suggested rubric/instruction adjustments — always framed as suggestions.
  - *Priority:* Should-Have. *Effort:* M.
- **D4 — Model management.** As an instructor, I want the app to pick models that fit my Mac and download them on first run, so I never touch a terminal.
  - *AC:* Hardware detection → tier recommendation (16 GB default: Qwen3-VL 4B + Qwen3 8B Q4; 24 GB+: Qwen3-VL 8B); resumable, checksummed downloads with progress; storage location + delete/redownload controls in settings.
  - *Priority:* Must-Have. *Effort:* M.

### 2.2 Functional Requirements (system-level)

**FR-1 Grading engine**
- FR-1.1 The engine must produce per-criterion scores conforming to the assignment's rubric schema (validated; auto-retry on schema failure; max 2 retries then human-flag).
- FR-1.2 Grading calls must run at temperature 0; uncertainty estimation may use separate sampled passes.
- FR-1.3 Every draft must include: per-criterion score + rationale, overall feedback draft, self-reported confidence (0–1), anchor-exemplar citations, and zero-or-more flags (`low_confidence`, `possible_plagiarism_style_shift`, `citation_issues`, `off_topic`, `needs_human`).
- FR-1.4 The engine must retrieve: rubric (versioned), top-k anchor exemplars (embedding similarity, verdict-balanced, k ≤ 8), and relevant source-material chunks, within the model's context budget.
- FR-1.5 Answer-key conditioning ("weak hint") must be paired with an over-crediting check (draft compared against key-free pass on a sampled subset; drift alerts).

**FR-2 Document pipeline**
- FR-2.1 Must parse: PDF, DOCX, PPTX, XLSX, HTML, PNG/JPG/HEIC, TXT/MD/LaTeX — via Docling (+ Granite-Docling VLM) to structured Markdown/JSON with reading order and tables.
- FR-2.2 Image/scan inputs must run OCR (Apple Vision first-pass) and preserve page images for direct VLM reading; per-region OCR confidence retained.
- FR-2.3 Must write: .docx margin comments, .pdf annotations, .xlsx cell comments, and templated feedback reports; all writes are copies — the original submission file is never mutated.
- FR-2.4 Unparseable files must degrade gracefully: raw-image VLM path, or human-flag with the original viewable.

**FR-3 Canvas integration** *(all verified against a live sandbox — see `docs/DATA-PLAN.md` §P3)*
- FR-3.1 All Canvas operations go through canvas-pp-cli (`--agent` JSON envelopes) or its MCP server. **Envelope shapes differ by verb:** reads return `{meta, results}` (payload under `results`; `meta.source` = `live` | local mirror); mutations return `{action, resource, status, success, data, partial_failure?}`. Parsing the wrong key yields a silent empty set — the importer must assert on `results`.
- FR-3.2 The app must implement authenticated attachment download (bearer token) since the CLI returns URLs, not bytes.
- FR-3.3 **Batch post-back is asynchronous.** `submissions bulk-update` returns a **Progress** object, not grades; the app must poll `/progress/:id` to `completed` before reporting a batch as posted (observed ~4 s). Reporting success on the POST alone will tell teachers grades landed when they haven't.
- FR-3.4 Write scope is limited to: submission grades, rubric assessments, submission comments, comment file attachments. No course-structure mutations in v1.
- FR-3.5 Offline mode: imported batches remain fully gradable with no network; posts queue until connectivity.
- FR-3.6 Requests must send `Accept: application/json` (Canvas returns HTML otherwise).

**FR-4 Citation verification**
- FR-4.1 Reference extraction from parsed submissions (bibliography + in-text).
- FR-4.2 Resolution ladder: DOI content negotiation → Crossref `query.bibliographic` (polite pool, app-owned `mailto`) → Semantic Scholar → OpenAlex (enrichment) → ddgs web search (non-academic only) → fetch (httpx+trafilatura; crawl4ai/Playwright headless-shell for JS pages).
- FR-4.3 Claim-checking (does the source support the student's claim?) runs on the local LLM only.
- FR-4.4 Outbound requests must never contain student names, IDs, essay text beyond the quoted claim fragment necessary for verification, or grades. An egress audit test enforces this.

**FR-5 Calibration & learning**
- FR-5.1 Exemplar store: every teacher-graded item (imported or created in-app) is embedded and indexed per assignment-type.
- FR-5.2 Overrides append to the exemplar store immediately and are weighted above pre-import exemplars.
- FR-5.3 Rubric refinement proposals require teacher approval; rubric versions are immutable and linked from every grade's audit record.
- FR-5.4 Calibration health panel: rolling QWK (AI-draft vs teacher-final), override rate, per-criterion disagreement — visible to the teacher.

**FR-6 Models & runtime**
- FR-6.1 v1 runtime: bundled/managed Ollama sidecar (app-scoped instance, its own models dir, localhost-only, app-lifecycle-managed). Engine speaks OpenAI-compatible API so the runtime is swappable (MLX v2).
- FR-6.2 Model tiers: 16 GB → Qwen3-VL 4B (vision/grading) + Qwen3 8B Q4 (agent/feedback) + Granite-Docling-258M (parsing) + an embedding model; 24 GB+ → Qwen3-VL 8B upgrade. User-overridable.
- FR-6.3 First-run wizard: hardware detect → recommended tier → resumable checksummed downloads → smoke-test inference before "ready."

### 2.3 Non-Functional Requirements

**Performance (16 GB M1/M2 baseline):**
- Parse + grade a 1,500-word essay against a 4-criterion rubric: ≤ 90 s end-to-end (target 45 s).
- Batch of 30 submissions: fully processed ≤ 45 min unattended; UI remains responsive (grading is background-queued).
- Queue/split-view interactions: < 100 ms; app cold start < 5 s (excluding model load; model warm-load ≤ 30 s).

**Privacy & Security:**
- All student content, grades, exemplars, embeddings: local only, under `~/Library/Application Support/Grader/`; SQLite encrypted at rest (SQLCipher); Canvas token in macOS Keychain.
- Network allowlist enforced in the engine: Canvas host (user-configured), model CDN (Ollama registry/Hugging Face), doi.org/api.crossref.org/api.semanticscholar.org/api.openalex.org, ddgs endpoints, and fetch targets for citation URLs. Everything else refused. Automated egress test in CI.
- FERPA posture documented (school-official exception; local processing = direct control); no telemetry by default — opt-in, anonymous, content-free.
- Code-signed + notarized distribution; Sparkle updates signed (EdDSA).

**Reliability:**
- Crash-safe job queue: any interrupted batch resumes; no grade data loss on force-quit (WAL journaling).
- Every AI failure mode terminates in a human-visible state — never a silent default score.
- Local time-machine-friendly data layout; one-click export of all data (SQLite + files) for backup/migration.

**Accessibility & UX quality:**
- WCAG 2.1 AA for the app UI; full keyboard operability of the grading loop; respects system dark/light and reduced motion.
- Generated student-facing documents use accessible defaults (real headings, sufficient contrast).

**Scalability (single-user desktop):**
- Tested to 8 courses × 150 students × 15 assignments/semester (≈ 18k submissions/year) with sub-second queue queries.
- Disk budget: models ≤ 12 GB (16 GB tier); data growth ~1–5 GB/semester typical.

---

## 3. Technical Specifications

### 3.1 Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Desktop shell | **Tauri 2 (Rust) + WKWebView** | Mature signing/notarization/updater; first-class sidecars; ~5 MB overhead. (Deno Desktop re-evaluated at v2 — experimental as of research date.) |
| UI | **React + TypeScript + Tailwind** | Fast iteration on queue/split-view; OSS contributor familiarity. |
| Engine | **Python 3.12 (uv-managed) + FastAPI on localhost** | Docling, Pydantic AI, mlx-* are Python; single engine process owns all AI/document/Canvas work. |
| Agent framework | **Pydantic AI v2** | Schema-validated structured outputs + retry; Ollama/OpenAI-compatible/MLX providers. Benchmark Tool-Output vs Prompted-Output per model at build time. |
| LLM runtime v1 | **Ollama sidecar** (app-scoped, localhost-only) | Official Qwen3-VL/Qwen3/Granite images; JSON-schema `format` outputs. |
| LLM runtime v2 | **MLX in-process (mlx-vlm/mlx-lm)** | Required for per-teacher LoRA on VLMs; MAS-compatible shape. |
| Models (16 GB tier) | Qwen3-VL 4B (vision grading) · Qwen3 8B Q4 (agent/feedback) · Granite-Docling-258M (parsing) · bge-m3 or nomic-embed (embeddings) | Apache 2.0 across the board; see RESEARCH.md §2. |
| Doc read | **Docling (MIT)** + Apple Vision OCR (`ocrmac`) + TrOCR fallback | License-clean; MLX-accelerated; handwriting path. |
| Doc write | **python-docx · pypdf · openpyxl · docxtpl** | Permissive licenses only (no PyMuPDF/marker — AGPL/GPL+OpenRAIL). |
| Citations | doi.org · Crossref · Semantic Scholar · OpenAlex · **ddgs** · httpx+trafilatura · **crawl4ai (library-only)** + Playwright chromium `--only-shell` | Keyless-first ladder; see RESEARCH.md §5. |
| Canvas | **canvas-pp-cli** (bundled binary, `--agent` mode) | Full read/write Canvas REST surface already built and audited. |
| Storage | **SQLite (SQLCipher) + sqlite-vec** for embeddings; files on disk | Single-user local; encrypted; no server. |
| Updates | **Sparkle** (delta updates; weights outside bundle) | Direct distribution. |
| License (app) | **Apache 2.0** | Matches dependency posture; permissive for institutional adoption. |

### 3.2 Data Models (core entities)

```
Teacher       { id, display_name, created_at }                        # single-profile v1, multi-profile ready
CanvasAccount { id, base_url, token_ref(Keychain), status, last_sync }
Course        { id, canvas_id?, name, term, account_id? }
Assignment    { id, course_id, canvas_id?, title, prompt_text, due_at,
                rubric_id, assignment_type: enum[essay, problem_set, lab, code,
                presentation, quiz_scan, other], settings: json }
Rubric        { id, name, version, canvas_id?, criteria: [RubricCriterion], status }
RubricCriterion { id, rubric_id, title, description, max_points,
                levels: [{score, label, anchor_description}],   # 3–5 levels enforced-by-default
                criterion_type: enum[objective, analytic, open_ended] }  # drives review weighting
SourceMaterial{ id, assignment_id, kind: enum[reading, answer_key, prompt, notes],
                file_ref, parsed_ref, embedded: bool }
Submission    { id, assignment_id, canvas_submission_id?, student_ref,   # student_ref = local pseudonymous key
                submitted_at, state: enum[imported, parsed, drafted, in_review,
                approved, posted, error], attempt }
Artifact      { id, submission_id, kind: enum[original, parsed_md, page_image,
                annotated_docx, annotated_pdf, feedback_report],
                path, mime, sha256, ocr_confidence? }
GradeRecord   { id, submission_id, rubric_version,
                ai_draft: { criterion_scores: [{criterion_id, score, rationale}],
                            feedback_md, confidence, consistency_votes?, flags[] },
                teacher_final: { criterion_scores[], feedback_md, approved_at }?,
                lane: enum[review_first, standard, high_confidence],
                posted: { canvas_status, posted_at, envelope_ref }? }
AnchorExemplar{ id, assignment_type, rubric_id, submission_ref?, text_excerpt_ref,
                embedding, teacher_scores: json, teacher_comment,
                source: enum[imported, graded_in_app, override], weight }
CalibrationStat{ id, assignment_id, window, qwk, override_rate,
                per_criterion_disagreement: json }
CitationCheck { id, submission_id, raw_reference, doi?, resolved_meta: json,
                verdict: enum[verified, metadata_mismatch, not_found,
                claim_unsupported, unreachable, skipped],
                evidence: [{url, quote}], checked_at }
AuditEvent    { id, entity_ref, actor: enum[engine, teacher], event_type,
                payload: json, model_id?, prompt_version?, created_at }  # append-only
ModelAsset    { id, name, tier, runtime: enum[ollama, mlx], version, size_bytes,
                sha256, status: enum[available, downloading, ready, error] }
```

**Key relationships:** Assignment→Rubric (versioned); Submission→GradeRecord (1:current, history via audit); AnchorExemplar scoped by (assignment_type, rubric) for retrieval; AuditEvent references any entity (polymorphic, append-only).

**Validation highlights:** criterion scores must lie within the criterion's level range; a GradeRecord cannot enter `posted` without `teacher_final.approved_at`; `student_ref` is a local pseudonym — Canvas IDs/names live only in the encrypted store and never enter prompts sent to *any* network service (they do enter local model prompts).

### 3.3 Engine API (localhost, Tauri→FastAPI)

Loopback-only, per-launch bearer token; JSON; long jobs return `job_id` + progress via WebSocket `/ws/jobs`.

| Endpoint | Purpose |
|---|---|
| `POST /canvas/connect` · `GET /canvas/courses` · `POST /canvas/import` | account setup; course list; batch import (submissions + attachments) → job |
| `POST /canvas/post` | post approved GradeRecords (single/bulk) → per-item results incl. partial failures |
| `POST /rubrics` · `POST /rubrics/import-canvas` · `POST /rubrics/{id}/refine` | create/parse rubric; import; accept/reject refinement proposals |
| `POST /assignments/{id}/materials` | attach + parse source materials |
| `POST /grade/batch` · `POST /grade/{submission_id}` | run grading → drafts, lanes, flags (job) |
| `GET /queue?assignment=…&lane=…` | review queue, sorted/faceted |
| `POST /grades/{id}/approve` · `POST /grades/{id}/override` | teacher decisions (override auto-creates exemplar) |
| `POST /exemplars/import` | bulk import past graded work |
| `POST /citations/verify/{submission_id}` | run verification ladder (job) |
| `POST /feedback/render/{submission_id}` | produce annotated docx/pdf/xlsx or report; returns Artifact refs |
| `GET /calibration/{assignment_id}` | QWK, override rate, disagreement panel |
| `GET /models` · `POST /models/install` · `DELETE /models/{id}` | model management (job) |
| `GET /audit?entity=…` · `GET /export/{assignment_id}` | audit trail; CSV/JSON export |

**Error model:** `{error: {code, message, recoverable, human_action?}}`; engine failures map to submission `state=error` with retry affordances — never dropped items.

### 3.4 Third-Party Integrations

| Service | Purpose | Auth | Limits / Cost | PII exposure |
|---|---|---|---|---|
| Canvas LMS (user's instance) | submissions in, grades out | user API token (Keychain) | institution-set | Yes — the user's own LMS; inherent to the task |
| Ollama registry / Hugging Face | model downloads | none | free | None |
| doi.org / Crossref | citation resolution | none (polite pool `mailto` = app address) | Crossref polite: 10 req/interval | None (reference strings only) |
| Semantic Scholar / OpenAlex | citation enrichment | none / free key | 100 req/5 min; credit-based | None |
| ddgs (DuckDuckGo) | non-academic source search | none | soft; backoff + cache | None (cited-source queries only) |
| Optional: Serper/Brave/Tavily | search reliability upgrade | user-supplied key | per-vendor | None |
| Sparkle appcast (project host) | app updates | signed feed | free (GitHub Releases) | None |

### 3.5 System Architecture

```
┌────────────────────────── Grader.app (Tauri) ──────────────────────────┐
│  React UI  ── queue · split-view · calibration · settings              │
│      │  localhost HTTP/WS (loopback, per-launch token)                 │
│  ┌───▼────────────── Python Engine (FastAPI) ─────────────────┐        │
│  │ Pydantic AI grading agent (temp 0, structured outputs)     │        │
│  │  tools: parse_document · retrieve_anchors · lookup_rubric  │        │
│  │         verify_citation · canvas_*                         │        │
│  │ Job queue (SQLite-backed, crash-safe)                      │        │
│  │ Docling + ocrmac/TrOCR │ python-docx/pypdf/openpyxl        │        │
│  │ Citation ladder (doi/Crossref/S2 → ddgs → crawl4ai)        │        │
│  │ SQLCipher SQLite + sqlite-vec │ AuditEvent log             │        │
│  └───┬──────────────────────┬──────────────────────┬──────────┘        │
│      │ OpenAI-compat HTTP   │ subprocess (--agent) │ HTTPS             │
│  ┌───▼───────────┐  ┌───────▼────────┐   ┌─────────▼─────────┐         │
│  │ Ollama sidecar│  │ canvas-pp-cli  │   │ allowlisted hosts │         │
│  │ (app-scoped)  │  │ (bundled Go)   │   │ (citations, models│         │
│  │ Qwen3-VL/Qwen3│  └────────────────┘   │  Canvas)          │         │
│  └───────────────┘                        └───────────────────┘        │
└─────────────────────────────────────────────────────────────────────────┘
   v2: Ollama sidecar → MLX in-process (mlx-vlm/mlx-lm) + LoRA training
```

**Grading data flow:** import (Canvas or drag-drop) → parse (Docling/OCR) → embed → retrieve (rubric + anchors + materials) → draft (temp-0 structured output) → confidence gate (self-report; consistency vote if low) → lane assignment → teacher review/override → exemplar + audit append → render annotated files → post to Canvas.

---

## 4. User Experience & Design

### 4.1 Design Principles

1. **The teacher's hand is always on the wheel.** AI output is visibly a *draft*; approval is an explicit act; overrides are frictionless and teach the system.
2. **Show your work.** Every score links to criterion text, anchor exemplars, and rationale. No unexplained numbers.
3. **Attention where it matters.** The queue's job is to make 30 papers feel like 8: high-confidence items collapse to spot-checks; uncertainty rises to the top.
4. **Calm, keyboard-first, batch-friendly.** Grading is a flow state; the UI must never interrupt it with modal churn or network stalls.
5. **Privacy is visible.** A persistent local-only indicator; the network panel shows every outbound request category.

### 4.2 Key User Flows

**Flow 1 — First run:** Welcome → hardware detect + model tier recommendation → model download (progress, resumable) → smoke test → optional Canvas connect (URL + token, Keychain) → course list appears.

**Flow 2 — Set up an assignment:** Pick course/assignment (or create local-only) → import/paste rubric → structure check (scale warning) → attach source materials → optionally import past graded exemplars → "Calibration: 0 exemplars — the AI will improve as you grade" status.

**Flow 3 — Grade a batch (core loop):** Import submissions → background parse+draft (progress in queue) → queue shows lanes (Review First n, Standard n, High Confidence n) → teacher enters split view on Review First → for each: read, adjust/approve (keyboard), AI learns from overrides → High Confidence lane: spot-check sampling UI (e.g., "review 3 of 14") → batch summary (distributions, misconceptions, pedagogy suggestions) → approve remaining → render annotated files (preview) → post to Canvas → per-student post receipt.

**Flow 4 — Citation review:** In split view, References panel lists per-reference verdicts with evidence quotes/links → teacher clicks through to source snapshot → verdict chips feed the relevant rubric criterion and feedback draft.

**Flow 5 — Calibration health:** Dashboard per assignment-type: QWK trend, override rate, per-criterion disagreement heat, pending rubric-refinement proposals (diff view, accept/edit/reject).

### 4.3 UI/UX Requirements

- **Layout:** Queue (home) → split view (grading). Left: original document render (PDF.js/native docx render/page images with OCR overlay). Right: rubric scorer (per-criterion steppers + level anchors), AI rationale (collapsible), feedback editor (rich text), citations panel, flags.
- **Keyboard map:** `J/K` next/prev · `A` approve · `1–5` set focused criterion score · `E` edit feedback · `F` flag for later · `⌘⏎` approve+next.
- **States:** skeletons for parsing; per-item progress chips (parsed → drafted → verified); friendly error states with retry; empty states that teach the calibration model ("Grade 3 yourself to unlock AI drafts" — cold-start rule).
- **Trust affordances:** confidence badge with plain-language explanation; "why this score" popover (criterion + anchors); style-bias notice on essay criteria ("AI review is stricter here — informal tone must not affect content scores").
- **Accessibility:** WCAG 2.1 AA; full keyboard loop; screen-reader labels on all grading controls; no color-only lane encoding.

### 4.4 Design Assets

- Wireframes/mockups: none yet — to be produced from this PRD (queue, split view, first-run wizard, calibration dashboard are the four key screens).
- Design system: start from a neutral Tailwind-based kit; defer custom identity until post-alpha.

---

## 5. Implementation Plan

### 5.1 Development Phases (solo-dev + AI-agent pacing; sequenced by risk)

**Phase 0 — Engine spike (Week 1):** Python engine skeleton; Pydantic AI + Ollama (Qwen3-VL 4B) + Docling; grade one rubric'd PDF end-to-end with schema-validated output. Benchmark Tool-Output vs Prompted-Output per model; measure tokens/s + latency on 16 GB. **Deliverable:** decision memo + working `POST /grade` on one file. *Kills the riskiest unknowns first.*

**Phase 1 — Calibration core (Weeks 2–4):** Rubric model + ingestion (paste/build; Canvas import); exemplar store + sqlite-vec retrieval; anchor-conditioned grading; override→exemplar loop; confidence + selective consistency voting; lanes; QWK eval harness with a golden set. **Deliverable:** CLI-driven grading of a real assignment folder with calibration stats.

**Phase 2 — App shell + core UX (Weeks 5–8):** Tauri app; first-run wizard + model manager; queue + split view + keyboard loop; job queue with progress; audit log; SQLCipher storage. **Deliverable:** installable .app grading local folders end-to-end.

**Phase 3 — Canvas round-trip (Weeks 9–10):** canvas-pp-cli bundling + envelope client; import (incl. attachment downloader); post-back (single/bulk + comment attachments); offline queue. **Deliverable:** full Canvas→grade→Canvas loop on a sandbox course.

**Phase 4 — Documents & citations (Weeks 11–13):** Handwriting pipeline (Vision OCR + VLM page reading + editable transcription); annotated write-back (docx/pdf/xlsx + report templates, preview); citation ladder + claims + citations panel. **Deliverable:** all four v1 must-have capabilities functional.

**Phase 5 — Hardening & release (Weeks 14–16):** egress audit test; bias/regression suite; performance passes; accessibility pass; signing/notarization/Sparkle; docs + OSS repo hygiene (CONTRIBUTING, issue templates); private beta with 3–5 instructors → v1.0 tag. **Deliverable:** public v1.0 release (GitHub).

### 5.2 Dependencies

```
P0 spike ─► P1 calibration ─► P2 shell/UX ─► P3 canvas ─► P5 release
                    │                            ▲
                    └──────────► P4 docs/citations┘   (P4 parallelizable with P3)
```

### 5.3 Resources & Costs

- **Team:** 1 developer (+ AI coding agents); 3–5 beta instructors.
- **Recurring costs:** Apple Developer Program $99/yr (signing/notarization); GitHub (free, incl. Releases for appcast + weights pointers — weights themselves pull from Ollama/HF, $0); optional search API keys are **user-supplied**, $0 to the project. **Total project cost ≈ $99/yr.**
- **User cost:** $0 (open source); disk ~12 GB models; one-time model download bandwidth.

---

## 6. Testing Strategy

- **Unit (target 80% on engine core):** rubric parsing/validation, exemplar retrieval (balance + k), schema validation/retry paths, envelope parsing, lane assignment logic, egress allowlist.
- **Golden-set grading harness (the product's real test):** curated set of ~100 human-graded submissions across ≥ 4 assignment types; CI reports QWK, override-simulation, and per-criterion disagreement per model/prompt version. **Regression gate: no prompt/model change ships if QWK drops > 0.03.**
- **Bias audit suite:** style-perturbation tests (informal tone, non-native phrasing, verbosity) auto-applied to golden-set items; score deltas must stay within threshold on content-identical variants; essay criteria monitored hardest. (Directly implements the arXiv 2603.18765 finding.)
- **Integration:** Canvas sandbox course round-trip (import → grade → post, incl. partial-failure injection); citation ladder against a fixture set (valid DOI, mangled ref, fabricated source, paywalled page, JS-rendered page); document write-back round-trips opened in Word/Preview/Excel.
- **E2E (WebDriver on Tauri):** first-run wizard, core grading loop, offline import→grade→queued post.
- **Performance:** batch-of-30 wall-clock on 16 GB reference hardware per release; model warm-load; memory ceiling watch.
- **Privacy:** automated egress capture during a full grading batch — any packet to a non-allowlisted host, or any allowlisted request containing student tokens, fails the build.

**Scenario example:** *Given* an essay with a fabricated reference and informal tone, *when* the batch runs, *then* the citation panel marks the reference `not_found` with evidence, the style-bias guard leaves content scores unchanged relative to the formal-tone control, and the item lands in Review First with `citation_issues`.

---

## 7. Success Metrics & Analytics

**Product metrics (computed locally, shown to the user; telemetry opt-in only):**
- Agreement: QWK (AI draft vs teacher final) per assignment type; target ≥ 0.70 by 25 exemplars.
- Efficiency: median teacher seconds/submission; % items in High Confidence lane (target ≥ 40% by assignment 5); % drafts accepted unmodified (target ≥ 60%).
- Calibration: override rate trend; rubric refinement acceptance rate.
- Trust/safety: 0 unapproved posts (hard invariant); egress test pass rate 100%.

**OSS project metrics:** GitHub stars/forks/issues velocity; beta→v1 retention of pilot instructors; # of institutions represented in issues.

**Opt-in telemetry (if enabled):** anonymous counts only — app version, model tier, feature usage counts, error codes. Never content, never scores, never Canvas metadata.

---

## 8. Risk Assessment

| # | Risk | P | I | Mitigation |
|---|---|---|---|---|
| T1 | 4B-class VLM grading quality below teacher tolerance on 16 GB tier | M | H | Golden-set gate before release; clear tier messaging; 8B on 24 GB+; lane thresholds tightened at low QWK (more human review, not worse grades) |
| T2 | Small local models fail Pydantic AI Tool-Output mode | M | M | P0 benchmark; Prompted-Output fallback is a config flag; Ollama `format` JSON-schema as belt-and-suspenders |
| T3 | Handwriting accuracy on real student work (no public benchmark) | M | H | Editable transcription UI; OCR-confidence flags; internal 50-page test set in P4; scanned work defaults to Review First lane |
| T4 | Ollama sidecar conflicts with a user's existing Ollama install | M | M | App-scoped instance (own port, own models dir, own lifecycle); detect-and-offer-reuse option |
| T5 | Style/verbosity bias penalizes students despite guards | M | H | Bias audit suite in CI; essay-criteria review weighting; bias notice in UI; never auto-post |
| T6 | canvas-pp-cli generated-code gaps or Canvas API drift | L | M | Envelope-level contract tests against sandbox; CLI is versioned/pinned; MCP fallback path |
| T7 | ddgs rate-limiting degrades citation checks | H | L | Metadata-API-first ladder makes search the fallback; cache; user-key upgrade path; `unreachable` is an honest verdict, not a failure |
| T8 | mlx-vlm LoRA path (v2) doesn't support chosen model | M | M | v2 concern; Qwen3-VL chosen partly for explicit mlx-vlm LoRA support; retrieval calibration is the primary mechanism regardless |
| B1 | Institutional pushback on any AI grading | M | H | Teacher-approval invariant, audit export, FERPA one-pager in docs; position as *drafting assistant*, formative-first |
| B2 | Solo-maintainer bus factor / OSS sustainability | H | M | Clean architecture + docs from day one; small dependency surface; pin + vendor critical deps |
| B3 | Model landscape shifts under the app | H | L | Runtime + model behind config; golden-set harness makes swaps measurable in an afternoon |

---

## 9. Launch Plan

**Go-live checklist:** all Must-Have ACs green · golden-set QWK ≥ 0.70 on ≥ 2 assignment types · bias suite within thresholds · egress test 100% · signing/notarization verified on clean machine · Sparkle update path tested (v0.9→v1.0) · docs complete (README, install, FERPA one-pager, rubric-authoring guide) · export/backup verified · 3+ beta instructors completed a full real assignment round-trip.

**Rollout:** Private alpha (Phase 2 exit — dev + 1 instructor, local files only) → Private beta (Phase 5 — 3–5 LACCD-adjacent instructors, real Canvas courses, weekly feedback) → **Public v1.0 on GitHub** (Releases DMG + Homebrew cask; Show HN / r/Professors / educator-tech communities) → post-launch: triage weekly, patch via Sparkle, publish calibration-results write-up (credibility artifact).

---

## 10. Future Roadmap

**Phase 2 (3–6 months post-v1):** MLX in-process runtime; per-teacher LoRA (1000+ exemplar gate, QWK-gated vs retrieval baseline); Deno Desktop re-evaluation; batch cross-assignment analytics; rubric library sharing (import/export, community repo); Mac App Store build (sandboxed, Background Assets).

**Long-term (6–12 months):** additional LMSes (Moodle, D2L); K-12 mode (COPPA, parent-facing reports); Windows/Linux (Tauri makes this tractable; model runtime is the work); peer-review orchestration; accessibility checking of student deliverables as a rubric criterion; multilingual grading.

---

## 11. Appendix

### 11.1 Glossary
- **Anchor exemplar:** a teacher-graded sample injected into the prompt to calibrate scoring — the app's core "grade like me" mechanism.
- **Lane:** review-priority bucket (Review First / Standard / High Confidence) assigned from confidence + consistency + criterion type.
- **QWK:** Quadratic Weighted Kappa — agreement metric between AI draft and teacher final; the product's north-star quality number.
- **Weak hint:** conditioning the grader on the answer key/reference solution; boosts recall of correct answers but requires over-crediting monitoring.
- **Envelope:** canvas-pp-cli's structured JSON result `{action, resource, status, success, data, partial_failure?}`.

### 11.2 References
- `RESEARCH.md` — full cited research report (models, parsing, citations, platform, packaging, reliability literature) — the evidentiary basis for every technical choice above.
- `docs/SPEC.md` — condensed technical spec sheet.
- canvas-pp-cli — `/Users/laccd/code/canvas-pp-cli` (local audit summarized in RESEARCH.md §10).

### 11.3 Change Log
| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-10 | Johnny + Claude | Initial draft from deep-research + discovery decisions |
