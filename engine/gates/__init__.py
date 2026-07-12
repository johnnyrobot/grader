"""Ship gates — the three CI tests that block a release.

Trust is the product. A grading tool that is occasionally unfair is worse than no grading tool.
Each gate is runnable standalone and returns exit 0 (pass) or 1 (fail).

  uv run python engine/gates/quality.py   -- golden set QWK ≥ 0.70, no regression > 0.03
  uv run python engine/gates/bias.py      -- style perturbation delta within threshold
  uv run python engine/gates/egress.py     -- no stray packets during a grading batch

Gates degrade gracefully when the golden set or Ollama isn't available — they report
SKIP with a clear reason and exit 0, so CI doesn't block on missing test data.
"""