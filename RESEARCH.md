# Grader — Deep-Research Architecture Report

*A fully-local macOS desktop app that grades anything a teacher needs graded, with the teacher as the source of truth.*

Synthesized 2026-07-10 from a 107-agent adversarially-verified deep-research run (23 confirmed / 2 refuted claims) plus four targeted gap-fill research agents and a local audit of `~/code/canvas-pp-cli`. Vendor benchmark numbers are self-reported unless noted; re-benchmark on Grader's own tasks before locking choices.

---

## 1. Executive summary — the recommended stack

| Layer | Recommendation | Runner-up / fallback |
|---|---|---|
| Grading VLM (primary) | **Qwen3-VL 8B** (Apache 2.0; Ollama + mlx-community MLX quants; mlx-vlm LoRA-supported) | MiniCPM-V 4.5 (8B, stronger vendor OCR claims, but no official MLX + unverified LoRA) |
| Grading VLM (low-RAM tier) | **Qwen3-VL 4B** ("wins clearly on document accuracy" at its size) or **MiniCPM-V 4.6** (1.3B, official Ollama library) | Gemma 3n E4B (inference only — excluded from mlx-vlm LoRA; Gemma ToU license) |
| Document parsing | **Docling (MIT) + Granite-Docling-258M (Apache 2.0, official MLX build)** → DocTags → Markdown/JSON | MiniCPM-o 4.5 (SOTA end-to-end EN parsing, OmniDocBench 0.109) for hard pages |
| Text feedback / tool-calling model | **Qwen3 8B** (native Ollama tools API) or **Granite 4.1** (enterprise structured JSON) | LFM2.5 1.2B ultra-light; Phi-4-mini for multi-step planning |
| Inference runtime | **MLX in-process (mlx-vlm / mlx-lm), exposed OpenAI-compatible** | Ollama sidecar (direct-distribution only; cannot serve VLM LoRA adapters) |
| Agent framework | **Pydantic AI v2** — Ollama/OpenAI-compatible/MLX providers, schema-validated rubric outputs with auto-retry | — |
| Teacher calibration | **Retrieved anchor exemplars (RAG over teacher-graded work) + rubric auto-refinement**; LoRA only at 1000+ examples | mlx-lm/mlx-vlm LoRA (all-layers, LR 10–15×, held-out QWK gate) |
| Citation verification | **doi.org + Crossref + Semantic Scholar (keyless) → ddgs search → crawl4ai/Playwright fetch → local LLM claim-check** | User-supplied Serper/Brave/Tavily key for reliability |
| Document write-back | **python-docx comments + docxtpl reports + pypdf annotations + openpyxl** (all permissive licenses) | — |
| Handwriting OCR | **Apple Vision (VNRecognizeTextRequest) first pass → TrOCR/Kraken fallback**; grading VLM reads pages directly | — |
| Canvas LMS | **canvas-pp-cli `--agent` JSON envelopes (or embed `canvas-pp-mcp`)** + app-side authenticated attachment downloader | — |
| App shell | **Tauri (production) or Deno Desktop 2.9 (if you accept "experimental")** over a bundled Python engine process | Swift + WKWebView |
| Weights distribution | **Never bundle. Small notarized app + first-run model download** (the universal pattern: Ollama, LM Studio, Draw Things, Jan) | Background Assets (200 GB Apple hosting free) if MAS |

The one place the research **reversed** an early conclusion: the initial verified sweep called Ollama "the lower-risk packaging path." Two independent findings overturn that as the *primary* runtime: (a) Ollama's `ADAPTER` supports only Llama/Mistral/Gemma *text* architectures and is broken for vision projection files (`mmproj` → 500 error, ollama#15346), so it can never serve a teacher-calibrated (LoRA) VLM; (b) spawning an Ollama server + `~/.ollama` writes is incompatible with Mac App Store sandboxing, while Draw Things proves the in-process-inference shape works on MAS. MLX in-process solves both. Ollama remains a fine v1 shortcut if you ship direct-only and defer LoRA.

---

## 2. Model strategy

### 2.1 The grading VLM

**Qwen3-VL (2B/4B/8B) is the recommended family** despite MiniCPM-V 4.5's headline benchmarks, because it is the only candidate that clears *all four* gates at once:

1. **License:** Apache 2.0 across the family — clean for a commercial desktop app. (MiniCPM-V is also permissive; Gemma 3n carries Gemma Terms-of-Use with a Prohibited Use Policy; some InternVL variants sit on non-commercial Qwen Research License bases — check per-variant.)
2. **Runtimes:** official Ollama library *and* mlx-community MLX quants via mlx-vlm; vllm-mlx/Rapid-MLX add OpenAI-compatible serving on Apple Silicon.
3. **Fine-tunable:** explicitly supported by mlx-vlm LoRA/QLoRA (`Qwen/Qwen3-VL-4B-Instruct` appears in its examples). Gemma 3n is *explicitly excluded* from mlx-vlm LoRA; MiniCPM-V's LoRA support is implied-but-unverified.
4. **Grading evidence:** in the one handwritten-exam-grading study found (arXiv 2606.11477, single-author preprint — treat as directional), locally-deployable **Qwen3-VL-30B hit 96.7% in "weak-hint" mode, beating GPT-5.2 (95.6%)**, and VLMs generally beat trained YOLO detectors 98.4% vs 88–91%.

All Qwen3-VL sizes share 262K context and the doc-OCR/chart/table capabilities; the 8B wins most benchmarks, the 4B wins document accuracy per-GB, the 2B (~1.9 GB) is the floor. Caveats: quantized MLX tool-calling degrades after 5–10 multi-turn rounds; run grading calls at temperature 0.

**MiniCPM-V 4.5 (8B)** remains the benchmark-leader alternative: OpenCompass 77.0, OCRBench 89.0, OmniDocBench EN 0.175 (vendor-reported, era-relative to Aug 2025). It has official llama.cpp/Ollama/vLLM support and 16 quantized sizes — but **no official MLX build** (mlx-community only covers 4.6 and o-4.5) and unverified LoRA. Keep it in the eval harness; don't build the calibration path on it.

**MiniCPM-V 4.6 (1.3B**, SigLIP2-400M + Qwen3.5-0.8B, in Ollama's official library since 2026-06-25**)** is the verified ultra-light tier for 8 GB Macs — benchmarks near 7–8B models on OCRBench/DocVQA per vendor.

### 2.2 The parsing specialist (separate from the grading judge)

**Granite-Docling-258M is a parsing specialist, not a grader** — IBM's card says it is "not intended for general image understanding" and should run *inside the Docling library*, where it replaces the ensemble of table/code/equation/OCR models. It outputs **DocTags** (structure-preserving markup) converting losslessly to Markdown/JSON/HTML, is Apache 2.0, and — uniquely — ships an **official MLX build for Apple Silicon**. For pathological pages, **MiniCPM-o 4.5** is the strongest single local model (OmniDocBench EN 0.109 OverallEdit, beating Gemini-3 Flash 0.155 and GPT-5 0.218 among end-to-end models, EN only).

So the pipeline separates concerns: **Docling parses → grading VLM judges**. Don't make one model do both.

### 2.3 Text/tool models for the agent loop

For feedback drafting, rubric reasoning over already-parsed text, and tool-calling: **Qwen3 8B** (native tool-calling in its chat template, works through Ollama's tools API "without hacks", ~8 GB at Q4_K_M) or **Granite 4.1** (IBM enterprise-tuned structured `<tool_call>` JSON, confirmed Ollama tool-schema support Jun 2026). **LFM2.5-1.2B** topped an independent 21-model local tool-calling bench (0.880, zero wrong-tool calls) as the ultra-light option. Reference points: Llama-3-Groq-Tool-Use-8B posts 89.06% BFCL.

Note from the grading-reliability literature: 1.5–3B models grade poorly (50–60% accuracy in EduAdapt); **7–8B is the credible floor for grading judgment**, with 14B+ (Qwen 14B, Mistral-Small-24B) consistently strong if the teacher's Mac has 24–32 GB. Tier by hardware:

| Mac RAM | Grading VLM | Text/agent model |
|---|---|---|
| 8 GB | MiniCPM-V 4.6 (1.3B) / Qwen3-VL 2B | LFM2.5 1.2B / Qwen3 4B |
| 16 GB | Qwen3-VL 4B | Qwen3 8B (Q4) |
| 24–32 GB+ | Qwen3-VL 8B | Qwen3 14B / Granite 4.1 |

---

## 3. Inference runtime: MLX in-process, Ollama as compatibility fallback

- **MLX (mlx-vlm + mlx-lm)** — in-process on Apple Silicon; serve OpenAI-compatible locally (`mlx_lm.server`, vllm-mlx) so Pydantic AI talks to it like any provider. This is the only path that can *train and serve* per-teacher LoRA adapters for VLMs on-device, and the only shape compatible with Mac App Store sandboxing (the Draw Things precedent).
- **Ollama sidecar** — simplest v1 if distributing direct (notarized DMG) and deferring LoRA: official MiniCPM-V 4.6 / Qwen3-VL / Qwen3 / Granite images, JSON-schema-constrained `format` outputs. Its hard limits: `ADAPTER` = Llama/Mistral/Gemma text only, non-quantized adapters recommended, vision adapters broken; `mlx_lm.fuse`'s GGUF export covers only Mistral/Mixtral/Llama fp16, so you can't "fuse in MLX, serve in Ollama" for Qwen/MiniCPM either.
- **Unsloth (NVIDIA-only training) and llama.cpp finetune (crash bugs, no VLM LoRA) are not viable on Mac.** MLX is the only credible on-device training stack.

Pragmatic path: **v1 ships Ollama-backed (fast to build), with the engine abstracted behind an OpenAI-compatible client so v2 swaps to MLX in-process without touching the agent code.** Both are OpenAI-compatible endpoints — the abstraction is nearly free.

---

## 4. Document pipeline (read AND write)

### Read
- **Docling (MIT)**: PDF, DOCX, PPTX, XLSX, HTML, images, LaTeX; layout analysis, reading order, table structure (97.9% table-extraction on IBM's benchmark); CPU default with **MLX acceleration on Apple Silicon**; exports Markdown/HTML/JSON/DocTags. The licensing-and-runtime-cleanest choice.
- **unstructured (Apache 2.0)**: viable alternate ETL.
- **marker — avoid for a commercial app**: code is GPL-3.0, weights are OpenRAIL-M gated at $2M revenue/funding; strong accuracy but a licensing landmine unless isolated and licensed.
- **Handwriting**: Apple Vision `VNRecognizeTextRequest` (free, on-device, zero-dependency — strong on print, unproven on messy cursive; Python wrapper `ocrmac`) → **TrOCR** (IAM-pretrained) or **Kraken v5** (retrainable HTR) as fallback → and for grading purposes, hand the page image directly to the grading VLM (the arXiv 2606.11477 result suggests VLMs beat detector pipelines at reading student answers in context). Validate on real student work — no student-essay handwriting benchmarks exist.

### Write (returning marked-up feedback)
- **.docx**: `python-docx` v1.2.0+ has **native `add_comment()`** (run-anchored margin comments with author/initials; no reply threads or resolved state). `docxtpl` (Jinja2 templating) for structured feedback reports.
- **.pdf**: **pypdf (BSD)** — `add_annotation()` with Text/Popup sticky notes, FreeText, highlights-adjacent shapes, links. **Avoid PyMuPDF unless licensed**: AGPL-3.0 or a reported $10k–50k/yr Artifex commercial license, and AGPL obligations attach to distributed binaries.
- **.xlsx**: `openpyxl` cell `Comment` (one-way write is reliable; round-tripping loses comment formatting).

---

## 5. Citation-verification stack (the design inversion)

The naive design is search→crawl→LLM. The research says **invert it**: most citations in student work are academic, and free **keyless metadata APIs settle "does this source exist and match?" in one HTTP call** — no browser, no search quota, roughly 10× less cost/latency/failure surface.

```
Layer 0 — RESOLVE (kills ~90% of academic refs)
  • DOI cited?           → doi.org content negotiation → citeproc-JSON (keyless, all registries)
  • Bibliography string? → Crossref /works?query.bibliographic=…&mailto=app@grader (join the
                           free "polite pool" day one: 10 req/interval vs 5; the mailto must be
                           an app contact address — never a teacher's or student's email)
  • Enrich               → Semantic Scholar (keyless, 100 req/5 min) → OpenAlex (free key;
                           DOI singleton lookups free — credit-based since Feb 2026, enrichment only)
Layer 1 — SEARCH (non-academic sources only: news, blogs, .gov)
  • Default: ddgs (pure-Python, keyless; backoff + cache — a desktop app's few queries/essay
    from a residential IP is the friendly profile)
  • Upgrade: user-supplied key — Serper (cheapest paid), Brave (free tier GONE since Feb 2026,
    uncapped billing — warn users), or Tavily (1k credits/mo free)
Layer 2 — FETCH & RENDER
  • Fast path: httpx + trafilatura/readability (most pages are static)
  • JS path:   crawl4ai v0.9.x as a Python LIBRARY (Apache 2.0) over Playwright chromium
    --only-shell (bundle ~<281 MB or lazy-install; ~700 MB RAM peak when rendering)
  • NEVER run crawl4ai's Docker/HTTP server — its 2026 releases patched RCE/SSRF/auth-bypass
    in exactly that component. Library use sidesteps the class.
Layer 3 — CLAIM CHECK (fully local)
  • Local LLM compares the student's claim against extracted text. Nothing student-authored
    leaves the machine; outbound traffic is only cited-source data (a DOI, a title, a URL).
```

**Ruled out:** self-hosted Firecrawl (AGPL core + closed Fire-engine anti-bot layer + Redis/Postgres/Docker stack — unshippable in a consumer app); bundled SearXNG (heavy *and* gets bot-classified by upstream engines → silent degradation); Google Scholar scraping (no API, blocks within a few queries); Bing API (retired Aug 2025); Kagi ($15–25/1k).

---

## 6. App platform

The hard constraint nobody escapes: **the engine is Python.** Docling, Pydantic AI, mlx-lm/mlx-vlm, crawl4ai, python-docx/pypdf are all Python. Whatever shell you pick, the app is "UI shell + local Python engine process (FastAPI/uvicorn on localhost) + model runtime."

| Shell | Verdict |
|---|---|
| **Tauri** (~2–10 MB) | The production-safe pick. Mature signing/notarization/updater story; sidecar processes are first-class. |
| **Deno Desktop 2.9** (~40 MB default, native WKWebView; optional CEF ~150 MB) | Real and official (Deno v2.9.0, 2026-06-25, feat #33441) with macOS Info.plist support — but **explicitly experimental and one month old**. Its docs' size/comparison numbers verified; claims about its specific maturity gaps were refuted in verification, so its production-readiness is genuinely unknown in both directions. Fine for the prototype if you accept churn; risky to bet the ship date on. |
| **Electron** (~100 MB+) | No advantage here — you'd bundle Chromium you don't need. |
| **Swift/SwiftUI + WKWebView** | Best-integrated (Apple Vision OCR, Background Assets, MAS) but slowest to iterate the UI. |

Framework size is second-order next to multi-GB weights either way. **Recommendation: prototype in whichever of Deno Desktop/Tauri you enjoy; ship v1 on Tauri unless Deno Desktop has stabilized by then.** The Python engine boundary means the shell is swappable.

---

## 7. Agent framework: Pydantic AI v2 — confirmed fit, one benchmark required

- **Structured outputs**: rubric scores as Pydantic models (scalars/lists/TypedDicts/dataclasses/unions too), schema-validated with **automatic retry re-prompting on validation failure** — exactly the reliability mechanism a rubric-score JSON needs.
- **Local providers**: dedicated Ollama docs page; any OpenAI-compatible endpoint via `OpenAIChatModel(base_url=…)`; MLX-on-Apple-Silicon listed among OpenAI-compatible providers; custom models supported.
- **The one risk**: default "Tool Output" mode delivers the schema as a special output tool's parameters — it depends on the local model's tool-calling. Small quantized models wobble here. **Benchmark Tool Output vs Prompted Output mode per model early** (Qwen3's native tool template is why it's the recommended agent model), and pin temperature 0 for schema adherence.

Agent design: one Pydantic AI grading agent with tools — `parse_document`, `lookup_rubric`, `retrieve_anchor_exemplars`, `verify_citation`, `canvas_*`. Grading calls at temp 0; a separate pass generates the uncertainty signal (§8).

---

## 8. Teacher-as-source-of-truth calibration (the heart of the product)

The evidence says **don't start with fine-tuning. Start with retrieval — it's what human rater calibration already is.**

1. **Rubric + retrieved anchor exemplars in-prompt.** Embed every teacher-graded submission; at grading time retrieve the k most similar (verdict-balanced — mix score levels to avoid base-rate bias) and inject them with the teacher's actual score and written comments. The AES literature ("Anchor is the key") shows anchor exemplars substantially reduce LLM–human score differences; retrieval-by-similarity beats random selection; diminishing returns past ~8–12 exemplars. This is also *explainable*: "scored 4 because it resembles these three you gave 4s" — and it updates the instant the teacher grades one more paper, with zero per-teacher model artifacts.
2. **Rubric auto-refinement from overrides** (Reflect-and-Revise pattern): when the teacher corrects a score, the agent diagnoses the discrepancy and proposes a rubric-text amendment the teacher approves. Persistent disagreement patterns should improve the rubric, not just the one grade.
3. **LoRA as a later optimization, not the foundation.** At 20–200 examples the dominant risk is memorizing the teacher's quirks on those items (the annotator-modeling literature finds exactly this failure). Compute is trivial (minutes on M-series via mlx-lm/mlx-vlm; QLoRA 8B fits a 16 GB Mac) — the data is the bottleneck. Revisit at 1000+ graded items per teacher, gated on held-out QWK beating the retrieval baseline; use all-layers LoRA (incl. MLP), LR ≈10–15× the full-FT rate, low rank. Serve via mlx-vlm (Ollama can't).

**Rubric ingestion**: structure rubrics as analytic (criterion-separated) with 3–5 anchored levels — a 0–5 scale gives the strongest LLM–human agreement and 0–10 the weakest (arXiv 2601.03444); binary MET/UNMET criteria are most reliable of all. Import Canvas rubrics directly via `canvas-pp-cli rubrics show`. Source materials (readings, answer keys, lecture notes) enter as retrieval context; the answer-key "weak hint" pattern cut false negatives to 0.58% in the handwriting study **but inflated false positives for some models — monitor over-crediting when conditioning on answer keys**.

---

## 9. Reliability guardrails + human-in-the-loop UX

From the grading-reliability literature (SURE/MDPI; arXiv 2603.29559, 2603.18765, 2606.12422, 2603.28304, and others):

- **Temperature 0 for grading calls** (>95% verdict reproducibility vs ~70% at temp 1).
- **Two uncertainty signals, combined**: the literature disagrees on which is better — SURE validates self-consistency voting (regrading only flagged items cut manual time 40–90%); arXiv 2603.29559 found cheap self-reported confidence *better calibrated* (ECE 0.166 vs 0.229) at 5× less compute. Use self-reported confidence on every item; escalate to a 3–5-sample consistency vote only on low-confidence items; both-low → teacher-review queue. (CHiLLGrader-style thresholds auto-cleared 35–65% of items at expert quality.)
- **Subject-weighted review**: essay-type items carry documented style bias (informal language −1.9 pts/10, non-native phrasing −1.35, persisting *despite* "ignore style" instructions) — route more essay items to review than math/code items. Watch verbosity bias, leniency on weak essays, and AI-text favoritism.
- **Separate trust levels for score vs feedback**: teachers and students accept AI *narrative feedback* but distrust AI *numbers* (arXiv 2606.12422). The teacher always approves the posted score; AI-drafted comments can be lighter-touch. Position the product as formative-first.
- **Feedback quality gate**: evaluate drafted comments against Hattie's four conditions (goal-linked, thinking-causing, understandable, actionable) — optionally with a second-LLM feedback-evaluator pass — before showing them.
- **Async review queue + audit trail**: AI batch-scores and flags; the teacher works the queue at their own pace; every grade records provenance (model, prompt/rubric version, confidence, justification, teacher edits). This is also the FERPA story: local inference strengthens the "school official" exception's direct-control requirement, and exceeding FERPA's (weak) security baseline — encryption at rest, no PII in any cloud call, documented data flows — is a genuine differentiator. COPPA (under-13): opt-in defaults.

**Pedagogy expertise** rides on top: the agent's system context encodes feedback frameworks (Hattie, feedback-literacy scaffolding) and offers *suggestions to the instructor* — never silent overrides. The teacher's rubric, exemplars, and edits always win.

---

## 10. Canvas integration (audited locally)

`~/code/canvas-pp-cli` is a self-contained Go binary wrapping the full Canvas REST API (~1,180 commands) plus a mirror **MCP server** (`canvas-pp-mcp`). Integration is ready-made:

- **Drive it programmatically** with `canvas-pp-cli <cmd> --agent` (= `--json --compact --no-input --yes`): JSON envelopes `{action, resource, status, success, data, partial_failure?}`, classified exit codes, auto-JSON when stdout isn't a TTY. Or embed the MCP server as a Pydantic AI toolset.
- **The grading loop maps 1:1**: `to-grade --all-my-courses` (cross-course ungraded queue) → `submissions index/show --include rubric_assessment,submission_comments` → grade in Grader → `submissions update` (posted grade + rubric assessment + comment) or `submissions bulk-update --stdin` for batch posting. Rubrics import via `rubrics show`. Moderated grading, gradebook history, and late policies are also covered.
- **One gap Grader must fill**: the CLI returns attachment *URLs*, not bytes (`submissions show` → `attachments[].url`; `files public-url`). Build a small authenticated downloader (HTTP GET with the `CANVAS_API_TOKEN` bearer) feeding files into the Docling pipeline. Annotated feedback files go back via the 3-step upload (`submission-comments … create-file` → `--comment-file-ids`).
- Auth: `CANVAS_API_TOKEN` + `CANVAS_BASE_URL` in the child env; secrets live in a 0600 `credentials.toml`. A local SQLite mirror (`sync`, `--data-source local --max-age`) gives offline course/roster browsing.

---

## 11. Packaging & distribution

- **Never bundle weights.** Universal pattern — Ollama.app (163.6 MB DMG), LM Studio, Jan, Msty, and crucially **Draw Things, a sandboxed Mac App Store app that downloads multi-GB weights into its container at runtime**. The macOS App Store size cap is actually 200 GB (4 GB is iOS), but bundling makes every release re-notarize gigabytes (100 GB bundles report 3.5–5 hr notarizations; even a few GB costs tens of minutes *per release*) and breaks delta updates.
- **Ship**: small signed+notarized app; first-run model download with checksums + resume into `~/Library/Application Support/Grader/models`. Apple DTS explicitly blesses "notarize the code, deliver big resources separately"; **Background Assets** gives 200 GB of Apple-hosted asset-pack delivery free with the developer membership (ODR is deprecated and never covered macOS).
- **Updates**: Sparkle deltas (direct distribution) stay tiny *because* weights live outside the bundle.
- **Channel choice**: **direct distribution first** — it permits the Python engine process and an optional Ollama sidecar without sandbox contortions. MAS later requires the Draw Things shape: in-process MLX inference, helpers signed with exactly `app-sandbox` + `inherit`.
- Watch App Translocation when referencing outside-bundle resources.

---

## 12. Open empirical questions (cannot be resolved from docs)

1. Does **mlx-vlm LoRA actually train on MiniCPM-V 4.5/4.6**? (Supported-by-"etc.", excluded-list doesn't name it — one afternoon to verify.)
2. Does mlx-vlm have an **adapter fuse/merge path**, or must inference always load base+adapter?
3. Can **Qwen3-VL 4B/8B drive Pydantic AI's Tool Output mode reliably** at Q4, or does Grader standardize on Prompted Output? (Measure, per model, before freezing the agent design.)
4. **Apple Vision vs TrOCR vs the grading VLM on real student handwriting** — no published benchmark exists; build a 50-page internal test set.
5. **Deno Desktop stability** by ship time — re-evaluate vs Tauri at v1 feature-freeze.
6. MLX vs Ollama/llama.cpp **throughput on the chosen models** on 8/16/32 GB Macs.

## 13. Suggested build order

1. **Engine spike**: Python engine (FastAPI) + Pydantic AI + Ollama Qwen3-VL 4B + Docling; grade one rubric'd PDF end-to-end with structured output. *(Validates riskiest integration in days.)*
2. **Calibration core**: rubric ingestion, exemplar store + embedding retrieval, anchor-conditioned grading, teacher-override loop + rubric refinement; confidence + selective-consistency flagging; QWK eval harness against a small human-graded set.
3. **Canvas loop**: canvas-pp-cli wrapper, attachment downloader, review-queue UI, grade/comment write-back (bulk-update).
4. **Documents out**: python-docx/pypdf/openpyxl annotated feedback; docxtpl reports.
5. **Citations**: Layer 0 metadata APIs → ddgs → crawl4ai; local claim-check.
6. **Ship hardening**: MLX in-process swap, first-run model downloader, Sparkle, notarization; LoRA experiments once real teachers pass ~1000 graded items.

---

## 14. Source index (primary)

Models: github.com/OpenBMB/MiniCPM-V · hf.co/openbmb/MiniCPM-V-4_5 / MiniCPM-o-4_5 · arXiv:2509.18154, 2604.27393 · ollama.com/library/minicpm-v4.6 · Qwen3-VL (Apache 2.0; codersera VRAM guide) · InternVL3.5 (arXiv:2508.18265; license caveat GitHub OpenGVLab/InternVL#900) · Gemma 3n (developers.googleblog.com; MatFormer hf.co/blog/rishiraj/matformer-in-gemma-3n) · BFCL gorilla.cs.berkeley.edu · LFM2.5 bench mikeveerman.be.
Parsing: ibm.com Granite-Docling announcement · hf.co/ibm-granite/granite-docling-258M · arXiv:2503.11576, 2501.17887 · marker LICENSE github.com/VikParuchuri/marker · OmniDocBench arXiv:2412.07626.
Grading reliability: mdpi.com/2504-4990/8/3/74 (SURE) · arXiv:2603.29559, 2603.18765, 2606.12422, 2606.11477, 2603.28304, 2601.03444, 2603.00077, 2601.08843, 2410.02736, 2606.19544, 2510.17389, 2603.11957 · ScienceDirect S1075293526000413 ("Anchor is the key"), S2666557326000455 · Hattie & Timperley 2007.
Calibration/LoRA: github.com/ml-explore/mlx-lm LORA.md · github.com/Blaizzy/mlx-vlm LORA.MD · docs.ollama.com/import · ollama#15346 · thinkingmachines.ai/blog/lora · arXiv:2405.09673, 2510.09030, 2506.21603, 2601.09065.
Citations/crawl: github.com/unclecode/crawl4ai (+releases) · playwright.dev/python/docs/browsers · github.com/firecrawl/firecrawl SELF_HOST.md · crossref.org REST docs + rate-limit blog · citation.doi.org · developers.openalex.org/api-reference/authentication · semanticscholar.org/product/api · pypi.org/project/ddgs · Brave pricing api-dashboard.search.brave.com.
Platform/packaging: docs.deno.com/runtime/desktop (+comparison, backends) · deno.com/blog/v2.9 · pydantic.dev/docs/ai (output, ollama, openai) · github.com/pydantic/pydantic-ai · developer.apple.com App Store Connect max build sizes · Apple Dev Forums thread 813586 · developer.apple.com/documentation/BackgroundAssets · sparkle-project.org delta updates · docs.ollama.com/macos · Draw Things App Store listing · bdash.net.nz sandboxing; twocanoes.com MAS helper entitlements.
Write-back: python-docx comments docs · pypdf annotations docs · pymupdf license discussion #971 · openpyxl comments docs · docxtpl docs.
Canvas: local audit of ~/code/canvas-pp-cli (README.md, internal/cli/*, cmd/canvas-pp-mcp).
