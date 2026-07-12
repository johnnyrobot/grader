# Grader — Test Data Acquisition Plan

**Version:** 1.0 · **Date:** 2026-07-11
**Purpose:** every dataset, fixture, and environment needed to build and ship v1, with licenses and access paths.
**Sources:** 107-agent verified deep-research run + HF/Kaggle Hub sweep + Canvas environment research. Companion to `RESEARCH.md` and `docs/PRD.md`.

---

## 0. The one thing public data cannot give you

Public corpora validate the **machinery** — QWK harness, bias suite, OCR pipeline, parsing, write-back. They cannot validate **"grades like Johnny"**, because no public dataset contains your judgment.

**Verified gap:** *there is no public corpus pairing higher-ed student work + the instructor's own course rubric + the grade they awarded.* Every verified essay corpus is K-12 (ASAP 2.0 grades 6–10; PERSUADE 2.0 grades 6–12; ELLIPSE grades 8–12), and the only higher-ed-native rubrics (AAC&U VALUE) ship with no student work attached.

**Therefore the data strategy is a split:**
- **Public data → engine correctness.** Builds and gates everything in CI. Available today, zero waiting.
- **Your graded work → calibration proof.** 20–30 items to demo it, ~100 to gate it. Only you can supply this.

You are **not blocked**: P0 and most of P1 can be built entirely on public data. Your archive becomes the *calibration* test, layered in when convenient.

---

## 1. License reality (read this before bundling anything)

| Tier | Datasets | What you may do |
|---|---|---|
| **Safe to bundle/ship** | **ASAP 2.0** (CC BY 4.0) · **Menagerie** (MIT) · DocLayNet (CDLA-Permissive) · Retraction Watch (Crossref, free) | Ship as sample data, redistribute, commercial-safe. Attribution required. |
| **Dev/test only** | PERSUADE 2.0, ELLIPSE, AAC&U VALUE (all CC BY-NC-SA 4.0) · IAM (NC + registration) | Use locally to build and evaluate. **Do not commit to the repo; do not bundle in the app.** |
| **Per-item clearance** | ASAG2024, S-GRADES | Aggregators that confer **no unified license** — each constituent keeps its upstream terms. One download ≠ one license. |

Grader is Apache-2.0 OSS and doesn't sell, so NC terms don't block *development* — but they do block **bundling NC data as shipped sample content** and block committing it to a public repo. Keep all fixtures in a git-ignored `fixtures/` directory with a download script.

**AAC&U VALUE rubrics — the one to act on now:** 16 genuine *higher-ed* rubrics (Written Communication, Critical Thinking, Quantitative Literacy, Teamwork…), free to download, but AAC&U explicitly enumerates **"assessment software or digital products"** as a commercial use needing **prior written permission** (value@aacu.org). It's permission-gated, not prohibited. Since Grader ships free and open-source, you likely qualify for non-commercial use with attribution + ShareAlike — but if you ever want them as built-in templates, **email value@aacu.org now**; it's a slow-path dependency worth starting early.

---

## 2. What to get, by phase

### P0 — Engine spike (week 1). *Need: one rubric + a few submissions.*
| Resource | Why | Access |
|---|---|---|
| **ASAP 2.0** — essays + official holistic rubric PDF | CC BY 4.0, human-scored 1–6, rubric downloads anonymously (verified HTTP 200, 83 KB PDF) | Kaggle `lburleigh/asap-2-0` (free account) or GitHub `scrosseye/ASAP_2.0` |
| **Mendeley `sf3kvjwknt/1`** — 50 digitized student exams | ⭐ **The single best fixture in this report** — scanned handwriting + *teacher-annotated* PDFs + item-level human marks CSV + answer key, all CC BY 4.0, no DUA | `data.mendeley.com/datasets/sf3kvjwknt/1` |

That's enough to grade a document end-to-end **today**.

### P1 — Calibration core (weeks 2–4). *Need: the golden set + inter-rater baseline.*
| Resource | Why | Access |
|---|---|---|
| **ASAP 2.0** (~24k essays, holistic 1–6, 20 expert raters, pre-adjudication κ .745) | Primary QWK harness; CC BY = safe to keep | Kaggle / GitHub |
| **ELLIPSE** (~6.5k, **six analytic trait scores** + real rubric .docx, two raters/essay) | The only corpus with **per-trait analytic** scores — matches how rubric-driven grading actually works. Dev-only (NC). | GitHub `scrosseye/ELLIPSE-Corpus` (zip passwords published in README) |
| **PERSUADE 2.0** (~26k, holistic + per-element effectiveness, **3 rubric PDFs**, demographics: ELL / race / econ / disability) | ⭐ **The bias-suite dataset** — demographic labels let you *measure* the style/ELL bias the literature warns about. Dev-only (NC). | GitHub `scrosseye/persuade_corpus_2.0` (CSVs on Drive) |
| **ASAG2024** (`Meyerger/ASAG2024`) — merges SciEntsBank, Beetle II, Mohler, SAF, Stita, CU-NLP, DigiKlausur; ~56k human-graded STEM short answers, normalized scale + reference answers | Covers problem-set/short-answer grading in one pull. Clear each upstream license before shipping. | HuggingFace |
| **Mohler ASAG** (`nkazi/MohlerASAG`, CC BY 4.0) — **two independent human graders per item** | ⭐ Gives a **human-vs-human κ baseline**. Critical: it tells you whether QWK 0.70 is good or bad *relative to humans grading the same work* — a far more honest gate than agreement with one rater. | HuggingFace |
| **Menagerie** (MIT) — 667 CS1 Java submissions, ~272 human-graded on 4 dimensions | The code-grading path; MIT = shippable | GitHub `m-messer/Menagerie` |

⚠️ **Do not double-count:** ASAP 2.0 and PERSUADE 2.0 share ~12,871 essays (~53% of ASAP 2.0). Dedupe by essay text hash before pooling.

⚠️ **Domain caveat:** all of these are K-12. They prove the *engine*; they do not prove higher-ed performance. Your own graded work closes that gap.

### P3 — Canvas round-trip (weeks 9–10). *Need: a writable Canvas course.*

**🚨 Canvas Free-for-Teacher is permanently discontinued** — Instructure killed it after the April–May 2026 Canvas breach (which was traced to FFT accounts). New signups are closed; a replacement is promised for fall 2026. The obvious free-sandbox path no longer exists.

**✅ DONE — sandbox is live and the round-trip is verified.** `scripts/seed_canvas_sandbox.py` seeds and exercises the full path on `canvas.johnnyrobot.ai`. This also closes the gap `canvas-pp-cli`'s own `HANDOFF.md` flagged (*"zero students in any course — grade/submission paths unproven"*).

| Artifact | ID |
|---|---|
| Course | **106** — `ZZ-GRADER-TEST-SANDBOX (safe to delete)` |
| Assignment | **2836** — Essay 1, 20 pts, `online_upload` + `online_text_entry` |
| Rubric | **189** — 4 analytic criteria × 0–5 (0–5 scale per research) |
| Test Student | **8** (Canvas built-in Student View user) |

**Verified round-trip** (`python scripts/seed_canvas_sandbox.py --apply`):
submit as Test Student (file attachment) → `PUT` grade **16/20** + 4-criterion rubric assessment with per-criterion comments + text comment + **annotated-feedback file attachment** → read back → **authenticated attachment download (2,862 bytes)** — the exact path Grader's importer will use.

Script safety: dry-run by default; refuses any course whose name lacks the `ZZ-GRADER-TEST` marker; idempotent (reuses the sandbox rather than piling up duplicates); `--teardown --course-id 106 --apply` removes it.

**✅ Cohort seeded too** (`--students 6`): 6 fake students (ids 9–14, `ZZGraderTest Student NN`, `@zz-grader-test.invalid` addresses so no mail can ever escape) enrolled and submitting work across four deliberate quality tiers (strong / middling / weak / barely-attempted). This unlocks the paths the Test Student can't reach:

- **`submissions bulk-update` verified** — all 6 graded in one call, each with a rubric assessment and comment. Resulting gradebook spread: **100 / 60 / 20 / 0 / 100 / 60** — a realistic distribution for exercising at-risk and confidence-lane logic.
- **Roster / gradebook / at-risk** now return real `current_score` values instead of empty sets.

Regenerate any time: `python scripts/seed_canvas_sandbox.py --apply --students 6` (idempotent). Teardown: `--teardown --course-id 106 --apply [--delete-users]`.

**Four Canvas findings — all would have bitten the P3 build:**
1. **Submission attachments must be uploaded *as the student*.** A teacher POSTing to `/users/:sid/files` gets **403** even for the Test Student. Masquerade with `?as_user_id=:sid` (needs the account-level `become_user` permission). Comment attachments (`/submissions/:id/comments/files`) are teacher-scoped and need no masquerade.
2. **`bulk-update` is asynchronous.** `POST .../submissions/update_grades` returns a **Progress** object, *not* the grades — they are not live when the call returns (observed ~4 s to `completed`). Grader's post-back must poll `/progress/:id` before reporting success, or it will read stale scores and tell the teacher a batch posted when it hasn't.
3. **canvas-pp-cli's read envelope is `{meta, results}`**, not `{data}` — the `{action, resource, status, success, data, partial_failure?}` shape documented in the PRD applies to **mutations only**. Reads put the payload under `results` with a `meta.source` field showing `live` vs local-mirror. Parsing the wrong key silently yields zero rows (it fooled me once — it will fool the importer).
4. **Canvas requires an explicit `Accept: application/json` header** — omit it and you get HTML back, not JSON.

**Fallback:** self-host `instructure/canvas-lms` via its Docker dev script. Budget a full day: needs ≥8 GB (realistically 16 GB+) allocated to Docker, and **Apple Silicon support is undocumented** — expect `--platform linux/amd64` emulation for parts of the stack. `instructure/canvas-self-hosted` is archived/alpha — not an option. Institutional `.beta`/`.test` clusters require an existing paying institution and wipe weekly.

### P4 — Documents, handwriting, citations (weeks 11–13).

**Handwriting (~50 pages):**
| Resource | Why |
|---|---|
| **Mendeley `sf3kvjwknt/1`** | Again the top pick — real scanned exams *with* human marks and answer key |
| **arXiv 2606.11477 released benchmark** ✅ *(verified real — 61 anonymized exams, 3,141 answer positions)* | The exact benchmark behind the 98.4%-VLM / Qwen3-VL-30B-96.7% results in `RESEARCH.md`. Check the paper's data-availability section for the release link. |
| **CROHME** (HF mirrors: `Neeze/CROHME-full`) | Handwritten **math** — shown-work grading |
| **IAM** (`Voxel51/iam_handwriting_finevision` mirror) | Standard HTR benchmark; NC + attribution terms still apply |
| IEEE DataPort "Student Handwritten Exam Dataset" (21 students, 5,914 phone-scanned images) | Realistic phone-photo condition; free IEEE account |

**Document parsing:** `docling-project/DocLayNet-v1.1` (permissive, matches our Docling choice) + `opendatalab/OmniDocBench` (the modern benchmark our chosen models report on).

**Citations — the thinnest category, build it yourself:**
| Resource | Role |
|---|---|
| **Retraction Watch DB** (`api.labs.crossref.org/data/retractionwatch`, free CSV ~40 MB) | Ground truth for citations to retracted work |
| **CiteCheck** (GitHub `xzy-xzy/CiteCheck`, MIT) | Human-labeled faithful-vs-hallucinated citation pairs |
| `birdie0111/SciCiteVal` (CC BY 4.0) | Manual citation-verification annotations |
| **Synthetic fixture set (build this)** | Take real DOIs from Crossref → deliberately corrupt them (wrong year, wrong author, plausible-but-nonexistent title, valid-format-but-dead DOI, retracted paper, paywalled page, JS-rendered page). This is the honest way to test the resolution ladder's five verdicts. |

No public corpus of *student papers with bibliographies* was found — the synthetic set plus a few of your own students' reference lists covers it.

---

## 3. Your data — the minimum ask

| When | What | Why it's irreplaceable |
|---|---|---|
| Any time (P1) | **20–30 graded items** — the work + your scores (+ comments if you have them) | Seeds the anchor-exemplar store; demonstrates "grades like me" |
| Before v1 gate (P5) | **~100 graded items across ≥4 assignment types**, with rubrics | The calibration QWK gate; proves higher-ed performance the K-12 corpora can't |
| P4 | **~20–50 pages of your students' handwritten work** | Real phone-photo/scan conditions; public sets are cleaner than reality |
| P4 | A few papers **with reference lists** (sloppy citations welcome) | Real-world citation messiness |

**Handling rules (non-negotiable):**
- De-identify before use: strip names/IDs; the harness needs only the work + your score.
- **Never commit to the public repo** — real student work in an OSS repo is a FERPA problem even anonymized. Fixtures live in a git-ignored directory; CI runs on public/synthetic data only.
- Prefer coursework from past terms where grades are final.

---

## 4. Immediate action list

1. **Download now (no blockers, ~1 hour):** ASAP 2.0 (+ rubric PDF), Mendeley exam dataset, Mohler ASAG, ASAG2024, Menagerie, DocLayNet. → unblocks P0 and most of P1.
2. **Email `value@aacu.org`** about VALUE-rubric use in a free/open-source grading tool. Slow path; start it now.
3. **Decide on the Canvas sandbox** — throwaway course on `canvas.johnnyrobot.ai` (fast, needs your OK) vs local Docker Canvas (slow, isolated). *Awaiting your call.*
4. **Pull 20–30 of your own graded items** whenever convenient — this is the only true blocker for the calibration *proof* (not for the build).
5. **Build the synthetic citation fixture set** during P4 — no good public option exists.

---

## 5. Corrections to prior research

- ✅ arXiv **2606.11477**, **2606.12422**, **2510.17389** were flagged as possibly hallucinated by a verifier. **All three independently verified as real** (fetched 2026-07-11): "Towards Fully Automated Exam Grading" (Grabowski, Jun 2026), "Creating and Evaluating K-12 GenAI Assessment Graders" (Tian et al., May 2026), "EduAdapt" (Naeem et al., Oct 2025). `RESEARCH.md` citations stand — and 2606.11477 **released its benchmark**, which is now a P4 asset.
- ❌ **Canvas Free-for-Teacher is dead** (discontinued after the 2026 breach). Any plan assuming a free Canvas signup is out of date.
