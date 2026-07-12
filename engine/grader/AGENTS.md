# GRADER PACKAGE

Core grading logic: schema contract, LLM agent, anchor retrieval, metrics, triage routing.

## STRUCTURE

```
grader/
├── schema.py        # Pydantic models: Rubric, GradeDraft, CriterionType, Flag — THE contract
├── agent.py         # Pydantic AI Agent: grade() entry point, output modes, temp 0
├── exemplars.py     # SQLite anchor store: embed, retrieve (similarity + band balance + override boost)
├── metrics.py       # QWK, agreement (signed bias), human ceiling
├── triage.py        # Confidence → review lane; consistency vote on low confidence
├── overrides.py     # Override → exemplar loop; pattern detection → rubric refinement proposals
├── rubrics.py       # Rubric ingestion: paste/build parser, Canvas import, versioned store, amendment
└── calibration.py   # Calibration health panel (rolling QWK, override rate) + cold-start rule
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add a rubric constraint | `schema.py` → `RubricCriterion` or `GradeDraft.validate_against()` | Push into schema, not prompt |
| Change output mode | `agent.py` → `build_agent()` `mode` param | tool/native/prompted — tool is P0 winner |
| Modify anchor retrieval | `exemplars.py` → `ExemplarStore.retrieve()` | `k`, `balance`, `exclude_ids` params |
| Add a triage rule | `triage.py` → `triage()` | Blocking flags → confidence → consistency vote |
| Change QWK computation | `metrics.py` → `quadratic_weighted_kappa()` | Pure function, no deps |

## CODE MAP — Internal call graph

```
bench.py / calibrate.py
  └→ agent.grade()
       ├→ build_agent() → build_model() [ONLY place that knows Ollama URL]
       ├→ _user_prompt() → rubric.render()
       └→ output_validator → GradeDraft.validate_against(rubric) → ModelRetry

calibrate.py
  ├→ ExemplarStore.retrieve() → embed() → cosine() → band-balanced top-k
  ├→ render_anchors() → formats for prompt injection
  └→ metrics.agreement() / human_ceiling()

triage.triage()
  ├→ blocking flags check (NEEDS_HUMAN, UNREADABLE, POSSIBLE_MISCONDUCT, OFF_TOPIC)
  ├→ confidence < 0.70? → consistency vote via agent.grade() resamples
  └→ open_ended criteria? → never HIGH_CONFIDENCE (style bias risk)
```

## CONVENTIONS

- `schema.py` is the reliability mechanism — if a constraint can be a Pydantic validator, it goes there, not in prompt prose
- `GradeDraft.validate_against(rubric)` returns `list[str]` problems → caller feeds to `ModelRetry`, not silent acceptance
- `grade()` **never raises** — exceptions caught into `GradeRun.error`. A failed grade is data, not a crash.
- `build_model()` is the **only** place that knows about Ollama (`base_url`, `api_key`). MLX swap touches nothing else.
- `mean_error` in `Agreement` is **signed**: +ve = AI lenient, -ve = AI harsh. Symmetric metrics hide leniency drift.
- Exemplar retrieval is band-balanced: round-robin across score bands, not pure nearest-neighbour (which returns all A-papers)
- `Exemplar.student_ref` is a local pseudonym — **never** a real student name

## NOTES

- `agent.py` retries=3 — schema failures re-prompt the model to repair its own output
- `exemplars.py` uses brute-force cosine (semester = ~1k exemplars = microseconds). sqlite-vec only earns its keep well past that.
- `triage.py` thresholds: `LOW_CONFIDENCE=0.70`, `HIGH_CONFIDENCE=0.85`, `UNSTABLE_SPREAD=0.15`
- `triage.py` consistency vote resamples at temp 0 — still varies because decode path isn't perfectly fixed
- All docstrings cite arXiv IDs justifying design choices — read them before changing parameters