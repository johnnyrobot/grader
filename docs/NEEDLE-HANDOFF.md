# Needle × Canvas — Decision Handoff

**Written:** 2026-07-16 · **For:** deciding whether (and how) to adopt Cactus Needle for
Canvas tool calling in Grader. Read this, then the evidence in
`docs/research/09-needle-canvas-tool-calling.md` (verified findings; raw claims in
`docs/research/raw-deep-research-needle.json`).

**Status: research done, no code touched, no decision made.** Nothing in the product depends
on this yet.

---

## 1. The question that prompted this

Can Needle (Cactus Compute's 26M on-device function-calling model) drive the Canvas REST API —
either replacing/augmenting how Grader calls Canvas, embedded in the CLI tool, or fine-tuned
into a custom "knows all things Canvas" model? Concern: the Canvas API is huge — **~417
endpoints** in the local API doc, **646 tools** on the Canvas MCP server.

## 2. TL;DR answer

- **Full-surface, out-of-the-box: no.** Needle has no published accuracy numbers, no documented
  context window or max tool count, and was trained on 15 consumer tool categories. The
  literature says *every* model collapses with hundreds of tools in context (13.6% selection
  accuracy all-in-context vs 43.1% with retrieval; degradation from ~100 tools; hosted APIs cap
  at 128).
- **Scoped + routed: plausible and cheap to test.** Two-stage pattern (embedding retrieval
  narrows 646 → top-5 candidate tools; the model picks + fills arguments; `canvas-pp-cli`
  executes). Fine-tuning Needle on Grader's actual ~20–30 grading endpoints needs only
  ~2.4k–3.6k synthetic examples (`needle finetune data.jsonl`, runs locally on a Mac).
- **Needle never replaces `canvas-pp-cli`** — it's a candidate intent→API-call *router* in
  front of it. All Canvas writes still flow through the approval gate and the
  `ZZ-GRADER-TEST` guard.
- **A full "Canvas LLM"** (all 650 tools) means ~50k–78k training examples and an unproven bet
  that 26M params can discriminate at that scale. Not where to start.

## 3. Options on the table

| | Option | Cost | Risk | When it wins |
|---|---|---|---|---|
| **A** | **Do nothing** — keep `canvas-pp-cli` with explicit code paths (current state) | 0 | none | The app's Canvas calls are already deterministic; no NL routing need exists yet |
| **B** | **Retrieval router + `grader-text:8b`** — embed the Canvas tool index, top-k retrieve, let the existing 8B Ollama model pick/fill the call | days | low | If NL→Canvas routing is wanted; no new runtime, model already shipped |
| **C** | **Retrieval router + fine-tuned Needle** (scoped, ~25 tools) | ~1–2 wks (data gen + finetune + eval + Cactus runtime integration) | medium | Only if B is too slow/heavy — Needle's win is latency/memory, not accuracy (unproven) |
| **D** | **Full Canvas-LLM fine-tune** (650 tools) | ~50k–78k examples + unproven capacity | high | Don't start here; revisit only if C works and full coverage becomes a product goal |

**Recommended sequencing if pursuing this at all: B first as the baseline, C only if B's
latency/footprint disappoints.** B and C share the retrieval front-end, so B's work isn't wasted.

## 4. Decisions to make (in order)

1. **Does Grader need NL→Canvas routing at all?** Current product flows (import → grade →
   review → approve → post) are fixed pipelines; a router only matters for a free-form
   "ask Canvas" feature. If no near-term feature needs it → Option A, file and move on.
2. If yes: **accept a second inference runtime?** Needle has no documented GGUF/Ollama/MLX
   path — it means shipping the Cactus SDK (Python binding, Metal) beside Ollama, notarized.
   If no → Option B only.
3. If C: **data pipeline must be local.** Cactus's web-UI data generator calls Gemini's cloud
   API — off-limits for anything touching real course data. Synthetic-only generation from the
   API doc is fine, but decide the generator (local model vs scripted templates).
4. **License comfort:** Needle is MIT, but its Gemini-distillation provenance is legally
   unresolved upstream. Check against `grader-licensing-constraints` standards before shipping
   weights.

## 5. Cheap experiments before committing (each < a day, no product code)

1. **Context probe:** install the standalone `needle` CLI, feed it 5 / 20 / 50 / 100 real
   Canvas tool schemas (convert from the MCP server or the API doc), measure where it breaks —
   answers the undocumented context-window question empirically.
2. **Zero-shot sanity:** `needle run --query "give user 9 a score of 2 on assignment 2841"
   --tools '<the 5 grading tools>'` — is out-of-distribution Canvas usable at all without
   fine-tuning?
3. **Baseline shootout:** same 25-tool retrieval set, same 20 test intents, `grader-text:8b`
   vs zero-shot Needle. If the 8B is accurate and fast enough, Option C dies cheaply.
4. **Micro-finetune:** 5 tools × 120 examples (~600 lines of JSONL, scriptable from the API
   doc), `needle finetune`, re-run the shootout. Answers "does fine-tuning close the gap?"

## 6. Hard constraints that bound any design (non-negotiable, from CLAUDE.md)

- No grade posts to Canvas without an approved grade in the data layer — a router may *draft*
  calls, never auto-execute writes.
- Canvas live writes only in `ZZ-GRADER-TEST*` courses.
- Student work stays on-device; no cloud data generation involving real course content.
- Grading paths use only `grader-text:8b` / `grader-vision:1.3b` — Needle would be a Canvas
  *integration* component, not a grading model.

## 7. Key facts to remember when reading

- Needle: 26M params, encoder-decoder "Simple Attention Network" (no FFN), distilled from
  Gemini 3.1 Flash Lite, MIT, single-shot function calling, released ~May 2026, zero
  independent benchmarks as of 2026-07-16.
- Fine-tune rule of thumb: **≥120 examples per tool** or it overfits.
- Tool-calling I/O: OpenAI-format tools in → `[{"name": ..., "arguments": {...}}]` out.
- The two CLI surfaces (`cactus run` vs `needle run`) use different tool-schema conventions —
  pick one and stick to it.

## 8. Artifacts

- Verified findings: `docs/research/09-needle-canvas-tool-calling.md`
- Raw verified claims + run log: `docs/research/raw-deep-research-needle.json`
- Local grounding source: `~/Desktop/CANVASRESTAPIllms-full.md` (1.4 MB, ~417 endpoints —
  desktop file, not in repo)
- Primary upstream: github.com/cactus-compute/needle · huggingface.co/Cactus-Compute/needle ·
  cactuscompute.com/blog/needle · docs.cactuscompute.com
