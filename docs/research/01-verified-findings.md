# Deep-research workflow — verified findings (23 confirmed / 2 refuted, 107 agents)

Full JSON: /private/tmp/claude-503/-Users-laccd-code-grader/080d0017-519e-43a1-a760-016a4fba756d/tasks/wybtnbb8i.output (lines 43-351)

## Confirmed (11 merged findings)

1. **MiniCPM-V 4.5** (8B/8.7B, Qwen3-8B + SigLIP2-400M, rel 2025-08-26): OpenCompass 77.0; vendor claims beats GPT-4o-latest, Gemini-2.0 Pro, Qwen2.5-VL 72B; "most performant MLLM under 30B" (era-relative, Aug/Sep 2025; superseded by MiniCPM-o 4.5 77.6-78.2 and Qwen3-VL 8B by mid-2026). HIGH. [github.com/OpenBMB/MiniCPM-V, hf.co/openbmb/MiniCPM-V-4_5, arXiv:2509.18154]
2. **MiniCPM-V 4.5 OCR**: OCRBench 89.0 (vendor claims > GPT-4o-latest, Gemini 2.5); SOTA PDF parsing on OmniDocBench among general MLLMs (EN edit dist 0.175); handwriting via demos + OmniDocBench handwritten-notes category (no dedicated handwriting benchmark). HIGH, self-reported. [same + arXiv:2412.07626]
3. **MiniCPM-o 4.5** (rel 2026-02-03): SOTA end-to-end EN doc parsing OmniDocBench 0.109 OverallEdit, beats Gemini-3 Flash (0.155), GPT-5 (0.218). Scope: end-to-end-model category, EN only; PaddleOCR-VL pipeline slightly better (0.105); trails HunyuanOCR on Chinese. HIGH. [github/hf + arXiv:2604.27393]
4. **Deployment**: MiniCPM-V official llama.cpp (PR #15575), Ollama, vLLM (v0.10.2), SGLang; int4/GGUF/AWQ/GPTQ in 16 sizes. **No official MLX**; mlx-community hosts only 4.6 and o-4.5 quants (not 4.5). → Ollama/llama.cpp is lower-risk packaging path. HIGH.
5. **MiniCPM-V 4.6** (rel 2026-05-11): 1.3B edge VLM (SigLIP2-400M + Qwen3.5-0.8B), vendor claims beats Gemma4-E2B-it, ~1.5x throughput of Qwen3.5-0.8B; in Ollama official library since 2026-06-25 (14.1K downloads). Ultra-light fallback tier. HIGH. [ollama.com/library/minicpm-v4.6]
6. **Granite-Docling-258M** (IBM, Apache 2.0, Sept 2025, official MLX build for Apple Silicon): 258M doc-conversion VLM outputting DocTags → Markdown/JSON/HTML; IBM says use only inside Docling library; replaces ensemble of table/code/equation/OCR models. NOT for general image understanding — parsing specialist only; still need general VLM for grading judgment. HIGH. [ibm.com announcement, hf.co/ibm-granite/granite-docling-258M, arXiv:2503.11576]
7. **Handwritten grading research** (arXiv:2606.11477, June 2026, single-author preprint): VLMs 98.4% on handwritten exam-answer recognition vs 88-91% YOLOv5; Qwen3-VL-30B local 96.7% weak-hint mode beats GPT-5.2 (95.6%); weak-hint (supplying reference solution) cuts FNR to 0.58% (FN 52→14) BUT raises FPs for some models (Grok FP 1→193, GPT-5.2 5→80) → answer-key conditioning needs FP monitoring. MEDIUM (single source, narrow task: single-letter answers, not essays; best numbers from cloud Gemini Flash).
8. **Deno Desktop**: official in Deno v2.9.0 (rel 2026-06-25, feat #33441), EXPERIMENTAL. macOS Info.plist support. Note: claim listing specific maturity gaps (clipboard/secure-storage/MSI) was REFUTED 0-3 — production-readiness genuinely unverified both directions. HIGH. [docs.deno.com/runtime/desktop/, deno.com/blog/v2.9]
9. **Deno Desktop packaging**: self-contained per-platform bundle (app code + Deno runtime), default native OS webview (WKWebView on macOS, ~40 MB, full Node/npm compat), optional bundled CEF ~150 MB. vs Tauri ~2-10 MB, Electron ~100 MB+. Framework overhead second-order vs multi-GB weights. HIGH.
10. **Pydantic AI structured outputs**: schema-validated Pydantic models (also scalars/lists/TypedDicts/dataclasses/unions), auto-retry on validation failure (ModelRetry, default retry 1). Default Tool Output mode = schema as params of special output tool → depends on local model tool-calling; Native and Prompted Output modes as fallbacks. → benchmark MiniCPM-class on Tool vs Prompted Output. HIGH. [github.com/pydantic/pydantic-ai, pydantic.dev/docs/ai/core-concepts/output/]
11. **Pydantic AI local providers**: dedicated Ollama docs page; any OpenAI-compatible endpoint via OpenAIChatModel base_url; MLX on Apple Silicon listed among OpenAI-compatible providers (e.g. mlx_lm.server); custom model implementations supported. Caveat: Ollama docs note structured-output limits for some models. HIGH. [pydantic.dev/docs/ai/models/ollama, /openai]

## Refuted
- "MiniCPM-V 4.5 leading OCR" as sourced to arXiv paper alone (1-2) — same facts survived 3-0 from HF card.
- Deno Desktop specific maturity-gap list (0-3) — do not cite those gaps.

## Caveats (from workflow)
- Nearly all benchmarks vendor self-reported, not independently replicated; era-relative. Re-benchmark on Grader's own tasks.
- arXiv 2606.11477: don't cite as proof VLMs can grade free-form handwritten essays.
- Deno Desktop 1 month old, experimental.

## Coverage gaps (being filled by 4 gap agents)
Gemma 3n; Qwen3-VL/InternVL as graders; LoRA/QLoRA teacher calibration; marker/unstructured + doc WRITE-back; web search/crawling (crawl4ai/Firecrawl/SearXNG); macOS signing/notarization of weights; LLM-grading reliability lit. Canvas = covered by local canvas-pp-cli-findings.md.

## Stats
5 angles, 25 sources, 124 claims extracted, top 25 verified → 23 confirmed / 2 killed; 11 after synthesis; 107 agent calls, ~5.0M subagent tokens.

## Reliability-lit sources fetched but claims budget-dropped (gap agent re-covering)
- mdpi.com/2504-4990/8/3/74 (SURE: self-consistency + selective human review)
- arXiv:2603.29559, arXiv:2603.18765, sciencedirect S2666920X24000353, arXiv:2606.12422
