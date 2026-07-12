# Gap report: LoRA calibration + Mac weight shipping (opus agent)

## A. Fine-tuning on Apple Silicon
- mlx-lm LoRA/QLoRA: lora/dora/full; archs incl Mistral/Llama/Qwen2/Gemma/MiniCPM/InternLM2; ~250 tok/s train on M1 Max 32GB (Mistral-7B); OOM levers: quantized base (QLoRA), batch 1-2, --num-layers 4-8, --grad-checkpoint. Fuse via mlx_lm.fuse; GGUF export ONLY Mistral/Mixtral/Llama fp16 → can't fuse-then-Ollama for Qwen/MiniCPM.
- mlx-vlm: supports LoRA/QLoRA for VLMs. Qwen3-VL explicitly supported. **Gemma3n EXCLUDED from LoRA** (inference only). MiniCPM-V 4.6 has dedicated README (inference); LoRA implied by "etc." — verify empirically. Fusing undocumented.
- Ollama ADAPTER: only Llama/Mistral/Gemma text archs; must match base; "use non-quantized (non-QLoRA) adapters"; vision mmproj BROKEN (issue #15346, 500 error). → Cannot serve fine-tuned VLM. Dead end for VLM plan.
- Unsloth: NVIDIA-only training (macOS inference only). llama.cpp finetune: crash bugs, no VLM LoRA. → MLX is the only credible on-device training stack.
- 20-200 examples on 1.3-8B: minutes of training; compute non-issue. But OVERFITTING dominant risk (memorizes teacher quirks; perspectivist lit: fine-tuning on annotator data "relies on annotator-specific signals", arXiv 2601.09065, 2508.02853).
- Evidence LoRA can work small-scale: Thinking Machines "LoRA Without Regret" (all-layers incl MLP, LR ≈10x FullFT, ~15x for <100 steps, rank-independent); "LoRA Learns Less and Forgets Less" (arXiv 2405.09673) — forgets less, preserves base capability → good for style nudge.
- Evidence retrieval better for grading: "Anchor is the key" (anchor exemplars = how human raters calibrate; substantially reduce LLM-human mean differences); Reflect-and-Revise (arXiv 2510.09030) rubric self-refinement from teacher overrides; Operationalizing AES (arXiv 2506.21603) individual-rater targeting.
- REC (ordered): 1) rubric + retrieved anchor exemplars in-prompt (k-similar, teacher score+comment; explainable, instant updates, no per-teacher artifacts); 2) rubric auto-refinement from overrides; 3) LoRA only at 1000+ examples w/ held-out QWK gate, all-layers, LR 10-15x.

## B. Shipping weights
- App Store size: macOS limit is 200GB (4GB limit is iOS). Policy-legal to bundle but impractical.
- Notarization: 100GB bundles → 3.5-5hr notarization; few-GB → tens of minutes but paid EVERY release. Apple DTS: extract resources, notarize code only; use Background Assets framework for large resources; notarize outermost container only. Watch App Translocation.
- Background Assets: macOS supported; Apple-hosted asset packs = 200GB hosting FREE with dev membership; ODR deprecated (never on macOS anyway).
- Precedent: NOBODY bundles weights. Ollama.app 163.6MB DMG → ~/.ollama on pull; LM Studio in-app HF browser; Draw Things = sandboxed MAC APP STORE app downloading multi-GB weights into container at runtime (key precedent); Jan/Msty same.
- Sparkle deltas: keep weights OUTSIDE bundle or every update re-downloads GBs. Sparkle = direct distribution only.
- MAS sandbox: helper binary needs exactly app-sandbox + inherit entitlements, only sublaunchable. Ollama's server+symlink+~/.ollama model incompatible with MAS. Draw Things in-process shape = MAS-compatible → embed MLX in-process.

## Bottom line
1. Runtime: MLX in-process, not Ollama (ADAPTER can't do VLMs + MAS sandbox).
2. Model: Qwen3-VL small = safe fine-tunable pick; Gemma 3n excluded; MiniCPM-V LoRA unverified.
3. Calibration: retrieval anchors first; LoRA at 1000+ w/ QWK gate.
4. Distribution: never bundle weights; first-run download; Background Assets or self-host; Sparkle deltas tiny if weights external.
Open empirical: mlx-vlm LoRA on MiniCPM-V? mlx-vlm fuse path?
