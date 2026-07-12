# Gap report: edge models + document read/write (sonnet agent)

## A. Models

### Gemma 3n E2B/E4B
- MatFormer nested transformer: E4B (8B raw/4B effective) contains extractable E2B (6B raw/2B effective). Per-Layer Embeddings → ~2GB (E2B) / ~3GB (E4B) memory. Multimodal: audio/image/video/text, 32K ctx.
- Day-one: Ollama, MLX, LMStudio, llama.cpp, vLLM, Unsloth.
- License: Gemma ToU (NOT Apache) — permissive w/ Prohibited Use Policy + redistribution notice reqs.
- Tool-calling weak natively; community fine-tunes (orieg/gemma3-tools) + FunctionGemma (270M). Gemma 4 E4B competitive w/ Qwen3-4B, Phi-4-Mini on BFCL v4 per ertas.ai 2026.

### Qwen3-VL small (2B/4B/8B)
- Apache 2.0 whole family. Ollama: 2B-235B; mlx-community MLX quants via mlx-vlm. 262K ctx all sizes; doc OCR/chart/table/UI grounding.
- 8B best overall; 4B "wins clearly on document accuracy" per codersera; 2B (~1.9GB) most frugal.
- Ollama JSON-schema structured output (`format`); Qwen3 chat template bakes in tool calling; vllm-mlx/Rapid-MLX add OpenAI-compat + MCP on Apple Silicon. Caveat: quantized MLX tool-calling degrades after 5-10 multi-turn rounds; temp 0 recommended for schema adherence.

### InternVL 3.5
- 2B: 50.7 avg (vs InternVL3-2B 32.4); 4B: 57.4. Repo MIT BUT some variants use Qwen RESEARCH LICENSE base LMs (non-commercial) — check per-variant. Community Ollama builds only (blaifa/InternVL3_5).

### Text-only graders / tool calling
- Qwen3 4B/8B: default local tool-calling pick, native Ollama tools API. 8B ~8GB Q4_K_M.
- Phi-4-mini (3.8B): best under-4B reasoning/multi-turn tool planning; occasional hallucinated function names.
- Granite 4/4.1 (IBM): enterprise structured JSON `<tool_call>`, Ollama tool-schema support confirmed Jun 2026.
- LFM2.5 1.2B (Liquid): top score 0.880 in Mike Veerman 21-model local tool-calling bench, zero wrong-tool calls, fastest; 350M variant fine-tunable for tools (Distil Labs).
- SmolLM3 (3B): strongest fully-open (weights+data+code), /think modes, 64K ctx.
- BFCL: Llama-3-Groq-Tool-Use 89.06% (8B)/90.76% (70B).
- Rec: Qwen3 4B/8B or Granite 4.1 primary; LFM2.5 ultralight fallback; Phi-4-mini if multi-step planning matters.

## B. Documents

### Parsers
- marker (datalab): ~91.7% accuracy, weak on borderless tables, GPU-hungry. **Code GPL-3.0; weights OpenRAIL-M — free only under $2M revenue/funding; else commercial license from Datalab.** Constraint for commercial app.
- unstructured: Apache 2.0.
- Docling: MIT (Granite-Docling Apache 2.0); 97.9% table extraction (IBM benchmark); CPU default + MLX acceleration on Apple Silicon. Most Mac-friendly license+runtime.
- Bottom line: Docling + unstructured licensing-clean; marker viral/gated.

### Handwriting OCR
- Apple Vision VNRecognizeTextRequest: handwriting supported for subset of languages; strong for printed; unverified for messy cursive — test empirically. Python wrapper: ocrmac.
- TrOCR (trocr-base-handwritten, IAM-pretrained): leading local HTR option.
- Kraken v5 (2026): best open-source HTR for connected scripts, retrainable on student handwriting.
- Rec: Vision first pass → TrOCR/Kraken fallback for cursive. No student-essay benchmarks found — validate internally.

### Write-back
- python-docx v1.2.0+: NATIVE add_comment() (run-anchored, author/initials); no reply-threading/resolved state; not in headers/footers.
- docxtpl: Jinja2 .docx templating for structured feedback reports.
- PDF: PyMuPDF rich annots BUT AGPL-3.0 (or $10k-50k/yr Artifex) — applies to distributed binaries; pypdf (BSD) add_annotation(): Text/Popup sticky notes, FreeText, shapes, links — license-clean choice.
- openpyxl Comment: cell comments; round-trip loses formatting.
- Rec stack: python-docx comments + docxtpl reports; pypdf PDF annots + Docling read; openpyxl xlsx.
