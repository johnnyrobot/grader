#!/usr/bin/env bash
# Build context-capped Ollama variants for Grader.
#
# WHY THIS EXISTS (a P0 finding that would have shipped a broken 16 GB tier):
#
# Ollama loads a model at its FULL default context window. Qwen3-VL advertises 262,144
# tokens, so `qwen3-vl:4b` — a 4-BILLION-parameter model whose Q4 weights are only ~3.3 GB —
# was observed consuming **41 GB of GPU memory**. The KV cache, not the weights, dominates.
# On the 16 GB machine the PRD targets, that model would simply fail to load.
#
# Grading never needs anywhere near that. A 1,500-word essay + rubric + 6 anchor exemplars +
# system prompt + output is ~5,500 tokens. 16,384 gives comfortable headroom (and room for
# long submissions); 262,144 is 16x more than we will ever use, at ~16x the memory cost.
#
# So we bake capped variants and grade against those. Run once after pulling base models.
#
#   ./scripts/make_models.sh
#
set -euo pipefail

CTX="${GRADER_CTX:-16384}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Snapshot the model list ONCE. Do not pipe `ollama list` into `grep -q` under
# `set -o pipefail`: grep exits at the first match, SIGPIPEs ollama, and the pipeline then
# reports failure — so an installed model looks missing.
INSTALLED="$(ollama list)"

make_variant() {
  local base="$1" name="$2"
  if ! grep -q "^${base}[[:space:]]" <<<"$INSTALLED"; then
    echo "  SKIP ${base} — not pulled (run: ollama pull ${base})"
    return
  fi
  cat > "$TMP/Modelfile" <<EOF
FROM ${base}
# Cap the context. See header: the default 262k window costs ~38GB of KV cache.
PARAMETER num_ctx ${CTX}
# Grading is deterministic by design — reproducibility is >95% at temp 0 vs ~70% at temp 1.
# Uncertainty is measured by separate sampling, never by making the grade itself noisy.
PARAMETER temperature 0
EOF
  echo "  building ${name} (from ${base}, num_ctx=${CTX})"
  ollama create "${name}" -f "$TMP/Modelfile" >/dev/null
}

echo "Building Grader model variants (num_ctx=${CTX})"
make_variant "qwen3:8b"           "grader-text:8b"
# Vision = MiniCPM-V, NOT Qwen3-VL. P0 measured Qwen3-VL at 140-263s per item because it
# emits 20k+ chars of un-disableable <think> reasoning (ollama `think:false` and Qwen's
# `/no_think` are both ignored). MiniCPM-V does the same task in 5.8s. See docs/phases/P0.md.
make_variant "minicpm-v4.6:latest" "grader-vision:1.3b"

echo
echo "Built:"
ollama list | grep -E "^grader-" || echo "  (none — pull the base models first)"
echo
echo "Memory check: run a grade, then \`ollama ps\` — SIZE should now be single-digit GB,"
echo "not 41 GB. If it still shows a huge SIZE, the variant is not being used."
