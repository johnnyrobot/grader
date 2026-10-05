# Contributing to Grader

Grader is an open-source, local-first grading tool for college instructors.
Contributions are welcome — bug fixes, new rubric parsers, citation sources,
document format support, and accessibility improvements.

## Development setup

```bash
git clone https://github.com/your-org/grader.git
cd grader
uv sync
ollama pull qwen3:8b && ollama pull minicpm-v4.6:latest
./scripts/make_models.sh                    # bake context-capped variants

uv run python engine/bench.py                # verify engine works
uv run python engine/gates/egress.py         # verify privacy gate passes
```

Frontend:
```bash
cd app && npm install
npx tsc --noEmit && npx vite build           # verify frontend builds
cd src-tauri && cargo check                  # verify Rust builds
```

## Project structure

```
engine/grader/     Python grading package (schema, agent, exemplars, triage, citations, etc.)
engine/gates/      CI ship gates (quality, bias, egress, perf)
engine/api.py      FastAPI engine — the single interface for the Tauri shell
app/src/           React + TypeScript frontend
app/src-tauri/     Tauri Rust shell
fixtures/          Citation fixtures (committed), golden set (gitignored — FERPA)
scripts/           Model baking, Canvas sandbox seeder
```

## Before submitting a PR

1. **Run the gates:**
   ```bash
   uv run python engine/gates/egress.py --capture-duration 12
   ```
   The egress gate must pass — no new outbound connections to non-allowlisted hosts.

2. **Build everything:**
   ```bash
   cd app && npx tsc --noEmit && npx vite build
   cd src-tauri && cargo check
   ```

3. **No type suppressions:** `as any`, `@ts-ignore`, `@ts-expect-error` are banned.

4. **No student data:** Never commit real student work. The `.gitignore` blocks
   `fixtures/golden_set/`, `fixtures/teacher-goldset/`, `*.student.*`, `private/`.

5. **No NC-licensed corpora:** PERSUADE 2.0, ELLIPSE, AAC&U VALUE, IAM are gitignored.

6. **No AGPL/GPL dependencies:** PyMuPDF (AGPL), marker (GPL), crawl4ai Docker (CVEs)
   are banned. Use pypdf (BSD), Docling (MIT), trafilatura instead.

## Architecture principles

- **Schema is the reliability mechanism** — push constraints into Pydantic models,
  not prompt prose. `GradeDraft.validate_against(rubric)` is the contract.
- **`grade()` never raises** — failures are `GradeRun` with `error` set.
- **`build_model()` is the only place that knows about Ollama** — MLX swap touches
  nothing else.
- **Temperature 0** for grading; uncertainty measured by separate sampling.
- **`mean_error` and `Draft bias` are signed** — both are AI minus teacher: positive
  = AI lenient, negative = AI harsh. `Draft bias` is additionally scaled as a
  fraction of criterion max, and is what the calibration panel reports.
- **Exemplar retrieval is band-balanced** — round-robin across score bands, not
  pure nearest-neighbour.

## Report issues

- Bugs: include the error, the command you ran, and your macOS version
- Citation verification: include the reference text and expected verdict
- Performance: include your machine's RAM, CPU, and the model used
- Privacy: if you find a network call that shouldn't exist, report it immediately

## License

Apache 2.0. By contributing, you agree your contributions are licensed under Apache 2.0.