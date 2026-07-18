# 09 — Cactus Needle for Canvas Tool Calling (Verified Findings)

**Date:** 2026-07-16 · **Method:** all-Opus deep-research workflow (run `wf_56838661-dd4`,
105 agents, 22 sources, 108 claims extracted, top 25 adversarially verified 3-vote each:
24 confirmed, 1 refuted, 0 unverified). Raw verified claims: `raw-deep-research-needle.json`.
Decision doc: `../NEEDLE-HANDOFF.md`.

**Question:** Is Cactus Compute's Needle model viable for driving the Canvas REST API from
Grader — directly, inside/alongside `canvas-pp-cli`, or fine-tuned into a Canvas-specialized
model? Local grounding measured from the instructor's Canvas API doc
(`~/Desktop/CANVASRESTAPIllms-full.md`, 1.4 MB): **~417 distinct method+path endpoints**
(220 GET / 86 POST / 66 PUT / 45 DELETE); the connected Canvas MCP server exposes **646 tools**.

---

## Bottom line

Needle **cannot** drive the full 400–650-tool Canvas surface out of the box — no model can;
all-tools-in-context collapses well before that scale. But a **fine-tuned Needle behind a
retrieval router, scoped to the ~20–30 endpoints Grader actually uses**, is viable and cheap
to test. Needle never replaces `canvas-pp-cli`; at most it becomes an on-device
intent→API-call router in front of it.

---

## 1. What Needle is (all claims 3-0 verified, primary sources)

- **26M-parameter** custom "Simple Attention Network" (SAN): encoder-decoder, **pure attention,
  no FFN/MLP layers**. Encoder: 12 layers, GQA (8H/4KV), RoPE, gated residuals. Decoder:
  8 layers, self-attn + cross-attn. d_model=512, 8192 BPE vocab.
- **Distilled from Gemini 3.1 Flash Lite.** Pretrained 200B tokens (16× TPU v6e, 27 hrs);
  post-trained on **2B tokens of single-shot function-call data** (45 min).
- A purpose-built function-caller, **not a general LLM** — architecturally unlike Grader's
  decoder-only Ollama models.
- **MIT licensed**, weights fully open (`Cactus-Compute/needle` on HF), dataset-generation code
  open. From-scratch architecture, so no upstream base-model license applies. Caveat: whether
  Google's Gemini ToS permits distillation into a redistributable model is Cactus's unresolved
  legal question.
- **I/O format:** consumes tool definitions in OpenAI function-calling format; emits a JSON
  array of `{name, arguments}` objects. Two entry points with different schema conventions:
  - Cactus SDK: `cactus run Cactus-Compute/needle [--tools my_tools.json]` (file-based, OpenAI
    format, demo toolset by default)
  - Standalone repo: `needle run --query ... --tools '<inline JSON>'` (simplified schema,
    per-param `required` booleans, no `type:function` wrapper) + `needle playground` Gradio UI.

Sources: github.com/cactus-compute/needle · huggingface.co/Cactus-Compute/needle ·
cactuscompute.com/blog/needle · docs.cactuscompute.com

## 2. The evidence gap (the most important finding)

Needle's **only** published benchmark is qualitative: "beats FunctionGemma-270m, Qwen-0.6B,
Granite-350m, LFM2.5-350m on single-shot function call" — **zero numbers**. Independently
confirmed absent across all primary sources:

- **No BFCL or any numeric accuracy score.**
- **No documented context window / max sequence length** (bounds how many tool schemas even
  fit in one call).
- **No documented maximum tools per call.**
- Training data covered only **15 consumer-device tool categories** (timers, messaging,
  navigation, smart home, …) synthesized via Gemini. A large heterogeneous enterprise REST
  surface is entirely out of distribution.
- The vendor concedes the comparison models "have more scope/capacity and excel in
  conversational settings."

Any accuracy expectation for Canvas is extrapolation. (3-0 verified.)

## 3. Tool-count limits and the two-stage router (literature, 3-0 / 2-1 verified)

- Dumping all tool descriptions in context ("blank conditioning") scored **13.62%**
  tool-selection accuracy vs **43.13%** with retrieve-then-inject-best (RAG-MCP,
  arXiv:2505.03275 — non-peer-reviewed preprint by the method's proponents; the *direction*
  is corroborated by Gorilla (ICML 2025), ToolLLM, Toolshed arXiv:2410.14594).
- Selection reliability degrades past **~100 tools** (some reports: 30–50); note 43% absolute
  is still low even with retrieval.
- Hosted APIs hard-cap at **128 tool definitions** (verified live OpenAI error); OpenAI
  recommends **~20 tools per agent**. Local runtimes have no API cap, but small models degrade
  well before hundreds of tools regardless.
- **Two-stage retrieve-then-call routing** (embed tool index → semantic search → top-k schemas
  to the model) is the standard, near-mandatory pattern at 400–650 tools. Fine-tuning is the
  other documented scaling path — the two compose.

## 4. Runtime on Apple Silicon (3-0 / 2-1 verified)

- Needle runs on **Cactus's own from-scratch inference engine**: C/C++ core; Python, Swift,
  Kotlin, Flutter, React Native, Rust bindings; **Metal backend** on macOS/iOS; ARM NEON CPU
  kernels. Vendor-reported speed: ~6000 tok/s prefill, ~1200 tok/s decode (hardware unspecified).
- **No documented GGUF / llama.cpp / Ollama / MLX path for Needle.** Cactus marketing pages
  advertise generic GGUF loading, but it is not documented for Needle specifically (this
  ambiguity produced the run's one refuted claim and one 2-1 vote).
- Practical implication: Needle would ship as a **second inference runtime** (likely the Python
  binding) beside Ollama — trivial memory at 26M params, but another dependency to sign/notarize,
  and Metal contention with `grader-text:8b` is untested.

## 5. Fine-tuning path (3-0 verified)

- **Documented and local:** `needle finetune data.jsonl` on a Mac/PC, or a Gradio web UI that
  generates data, trains, evaluates, bundles.
- **Data format:** JSONL, three fields per line — `query`, `tools` (JSON-encoded array of
  {name, description, parameters}), `answers` (JSON-encoded expected calls).
- **Volume rule:** ≥**120 examples per tool** (100 train / 10 val / 10 test); fewer overfits
  ("perfect training metrics but the model won't generalize").
- Scale math: full Canvas surface (417–646 tools) → **~50k–78k examples**. Grader's actual
  grading slice (~20–30 endpoints) → **~2.4k–3.6k examples**.
- **Privacy catch:** the web-UI data generator calls the **Gemini cloud API**. Only the CLI
  path with locally pre-generated data is fully offline — required for Grader's posture.
- No precedent found either way for whether 26M params can discriminate among 400+ tools even
  after fine-tuning.

## 6. Refuted / weak claims (transparency)

- **Refuted (1-2):** "Cactus is a proprietary hybrid edge-cloud engine that doesn't run on
  GGUF" — Cactus is MIT-licensed and its pages do advertise GGUF loading; the claim overstated.
- **2-1 votes:** Metal-backend specifics (GGUF-support ambiguity); "~100-tool degradation
  threshold" (position-vs-registry-size ambiguity in RAG-MCP; resolved in the claim's favor,
  and ToolRet/other reports suggest ~100 is conservative).

## 7. Caveats

- Needle released **~May 2026**; everything is ~2 months old, vendor-reported, with **no
  independent third-party benchmark** at research time (2026-07-16).
- Speed numbers are Cactus-reported on unspecified hardware.
- RAG-MCP percentages come from a self-interested preprint; treat direction, not digits.
- Two CLI interfaces with different tool schemas — integration must target the right one.
- Gemini-distillation ToS question unresolved (matters only if license cleanliness matters).

## 8. Open questions (from the verified run)

1. Fine-tuned on a Canvas tool set, what selection accuracy does Needle actually reach — and
   does 26M have the capacity for 400–650 tools, or only for a scoped subset? (Empirical only.)
2. What is Needle's real context window / max input tokens? Undocumented; directly bounds
   retrieved top-k schema count.
3. Can the Cactus runtime co-exist with Grader's Ollama stack (memory, process model, Metal
   contention)? Is there a supported GGUF/Ollama export for Needle, or is the Cactus SDK
   mandatory?
4. Does fine-tuning on Gemini-distilled outputs for a commercial product create Gemini-ToS
   exposure — and does the offline requirement force a custom local synthetic-data pipeline?

## 9. Sources (22 fetched; quality as assessed)

**Primary:** github.com/cactus-compute/needle · huggingface.co/Cactus-Compute/needle ·
cactuscompute.com/blog/needle · cactuscompute.com · docs.cactuscompute.com (+ /v2.0.1/) ·
github.com/cactus-compute/cactus README · arXiv:2505.03275 (RAG-MCP) · arXiv:2410.14594
(Toolshed) · arXiv:2411.15399 · arXiv:2505.06416 · proceedings.mlr.press/v267/patil25a
(Gorilla, ICML 2025).
**Secondary:** NYU Shanghai RITS · startupfortune.com · deepwiki.com/cactus-compute/needle.
**Blog:** Red Hat Next (tool-RAG) · apxml.com · writer.com/engineering/rag-mcp ·
mindstudio.ai · huggingface.co/blog/rshemet · tianpan.co (tool-selection at scale).
