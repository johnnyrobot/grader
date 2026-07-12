# ENGINE

Grading engine entry points + the `grader` package. Run everything with `uv run python`.

## STRUCTURE

```
engine/
├── grader/              # the Python package (schema, agent, exemplars, metrics, triage)
├── bench.py             # P0: model/output-mode spike
├── calibrate.py         # P1: anchor-vs-baseline experiment (NEXT COMMAND)
├── bench_results.json   # generated output (bench.py)
└── calibration_results.json  # generated output (calibrate.py)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Benchmark models/modes | `bench.py` | `--models qwen3:8b --modes tool,prompted,native --repeats 3` |
| Run calibration experiment | `calibrate.py` | `--n-test 20 --n-exemplars 60 --k 6` |
| Understand grading flow | `grader/agent.py` → `grader/schema.py` | `grade()` is the entry point |
| Add a new metric | `grader/metrics.py` | QWK, agreement, human_ceiling already there |
| Modify triage thresholds | `grader/triage.py` | `LOW_CONFIDENCE`, `HIGH_CONFIDENCE`, `UNSTABLE_SPREAD` constants |

## CONVENTIONS

- Both scripts use `sys.path.insert(0, str(Path(__file__).parent))` — not installed as a package (`package = false` in pyproject.toml). Import as `from grader.agent import grade`.
- `bench.py` RUBRIC matches Canvas sandbox rubric 189 (4 criteria × 0-5, `mechanics` is `OPEN_ENDED`). SUBMISSIONS are 4 quality tiers with expected score bands.
- `calibrate.py` loads Mohler ASAG from Huggingface parquet URL (no local download needed). Single-criterion 0-5 rubric. Reports baseline vs anchored QWK + % of human ceiling.
- Generated JSON files (`bench_results.json`, `calibration_results.json`) are outputs, not configs.

## NOTES

- P0 finding: `qwen3:8b` in **tool mode** = 100% schema success, 0 retries, correct rank-ordering, zero temp-0 variance.
- `calibrate.py` is built but **not yet run** — it's the highest-value next command in the project.
- Always grade against capped model variants (`grader-text:8b`), never raw `qwen3:8b` — see `scripts/make_models.sh`.