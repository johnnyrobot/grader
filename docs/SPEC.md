# Grader — Technical Spec Sheet (v1)

One-page engineering reference. Authority: `docs/PRD.md` (requirements) · `RESEARCH.md` (evidence & citations).

## Product
| | |
|---|---|
| Product | Grader — local-first AI grading copilot for instructors |
| Platform | macOS 14+ (Apple Silicon), desktop app |
| License | Apache 2.0 (open source) |
| Audience | Higher-ed / community-college instructors (Canvas-based) |
| Distribution | Direct: notarized DMG via GitHub Releases + Homebrew cask; Sparkle auto-updates (delta) |
| Privacy invariant | Student content/grades never leave the device; outbound = Canvas (user's own), model downloads, cited-source metadata lookups only. CI egress test enforces. |
| Approval invariant | No grade posts to Canvas without explicit teacher approval. |

## Hardware tiers
| RAM | Vision (transcribe) | Text (grade/feedback) | Notes |
|---|---|---|---|
| 16 GB (minimum) | **MiniCPM-V 4.6** (1.3B) | Qwen3 8B (Q4_K_M) | default tier |
| 24–32 GB+ | MiniCPM-V 4.5 (8B) | Qwen3 8B or 14B | auto-recommended |
| All tiers | Granite-Docling-258M (parsing) · nomic-embed (embeddings) | | ~12 GB disk (16 GB tier) |

⚠️ **Qwen3-VL was dropped in P0** — it emits 20k+ chars of un-disableable `<think>` reasoning
(140–263s/item vs MiniCPM-V's 5.8s). See `docs/phases/P0.md`.

⚠️ **Always run the context-capped variants** (`scripts/make_models.sh` → `grader-text:8b`,
`grader-vision:1.3b`). Ollama loads models at their FULL default context; Qwen3-VL's 262k default
made a 4B model consume **41 GB**. Capped at `num_ctx=16384` it fits the 16 GB tier.

**Vision transcribes, text grades.** Never let the VLM judge — short outputs avoid the reasoning
spiral, and handwritten/typed work then share one grading path.

Weights are **never bundled** — first-run resumable, checksummed download.

## Stack
| Layer | v1 | v2 direction |
|---|---|---|
| Shell | Tauri 2 + WKWebView; React + TS + Tailwind | Deno Desktop re-eval; Mac App Store build |
| Engine | Python 3.12 (uv) · FastAPI · loopback + per-launch token | unchanged |
| Agent | Pydantic AI v2 · structured outputs · temp 0 · Prompted-Output fallback flag | + LoRA-calibrated models |
| LLM runtime | Ollama sidecar (app-scoped: own port/models-dir/lifecycle) via OpenAI-compat client | MLX in-process (mlx-vlm/mlx-lm) + per-teacher LoRA (1000+ exemplar, QWK-gated) |
| Doc read | Docling (MIT) + Granite-Docling · Apple Vision OCR (ocrmac) · TrOCR fallback | Kraken-trained HTR optional |
| Doc write | python-docx (margin comments) · pypdf (annotations) · openpyxl (cell comments) · docxtpl (reports). **Banned: PyMuPDF (AGPL), marker (GPL/OpenRAIL)** | — |
| Citations | doi.org content-negotiation → Crossref `query.bibliographic` (polite pool, app mailto) → Semantic Scholar → OpenAlex (enrich) → ddgs → httpx+trafilatura → crawl4ai **library-only** + Playwright chromium `--only-shell` | user-key search upgrade (Serper/Brave/Tavily) |
| Canvas | bundled canvas-pp-cli (`--agent` JSON envelopes) + in-app authenticated attachment downloader; writes: grades, rubric assessments, comments, comment files | canvas-pp-mcp toolset option |
| Storage | SQLCipher SQLite + sqlite-vec · files under `~/Library/Application Support/Grader/` · Canvas token in Keychain · append-only AuditEvent log | — |

## Grading pipeline (per submission)
```
import → parse (Docling / Vision-OCR + VLM page read) → embed
→ retrieve: rubric(vN) + k≤8 anchor exemplars (similar, verdict-balanced) + source materials
→ draft @ temp 0 (schema-validated; ≤2 retries → human-flag)
→ confidence gate: self-report → low? 3-sample consistency vote
→ lane: Review First | Standard | High Confidence  (essay criteria weighted toward review)
→ teacher approve/override (override ⇒ new exemplar, immediate)
→ render annotated files (copies only) → post to Canvas (approved only)
→ audit append at every step
```

## Calibration mechanics
- Anchors: every teacher-graded item embedded per assignment-type; retrieval-based few-shot (NOT fine-tuning) is the v1 mechanism.
- Rubrics: analytic, 3–5 anchored levels enforced-by-default (0–5 scales; wide scales warned).
- Answer-key "weak hint" + over-crediting drift check.
- Reflect-and-Revise: ≥3 patterned overrides → proposed rubric amendment (teacher approves; versioned).
- Health: rolling QWK, override rate, per-criterion disagreement.

## Quality gates (CI)
| Gate | Threshold |
|---|---|
| Golden set (~100 human-graded items, ≥4 types) | QWK ≥ 0.70; regression > 0.03 blocks ship |
| Bias suite (style perturbations, content-constant) | score delta within threshold; essays strictest |
| Egress audit (full batch capture) | 0 non-allowlisted packets; 0 student tokens in allowlisted requests |
| Canvas sandbox round-trip | import → grade → post incl. injected partial failures |
| Perf (16 GB reference) | 1,500-word essay ≤ 90 s; 30-item batch ≤ 45 min; UI < 100 ms |

## Performance / storage budgets
- Models ≤ 12 GB · data ~1–5 GB/semester · app bundle target < 200 MB (excl. Playwright shell if bundled).
- Scale envelope: 18k submissions/yr, sub-second queue queries.

## v1 must-haves (locked)
Canvas pull + write-back · citation verification · handwritten/scanned work · annotated file write-back · calibration core · confidence-lane review UX · audit trail · model manager.

## Open empirical items (Phase 0 spike resolves)
1. Tool-Output vs Prompted-Output reliability per model (Qwen3-VL 4B/8B, Qwen3 8B @ Q4).
2. Ollama vs MLX throughput on 16 GB reference hardware.
3. Handwriting accuracy on real student work (50-page internal set).
4. (v2) mlx-vlm LoRA on MiniCPM-V; mlx-vlm adapter fuse path.

## Build phases
P0 engine spike (wk 1) → P1 calibration core (2–4) → P2 Tauri shell/UX (5–8) → P3 Canvas (9–10) → P4 docs/citations/handwriting (11–13, parallelizable w/ P3) → P5 hardening + release (14–16).

## Cost
Project: $99/yr (Apple Developer). User: $0; optional user-supplied search API key.
