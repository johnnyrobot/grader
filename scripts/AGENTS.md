# SCRIPTS

Infrastructure scripts: model baking, Canvas sandbox seeding, public-proxy evidence, frontend
evidence, and release-readiness checks. Canvas and clean-machine scripts have safety-critical
guards.

## STRUCTURE

```
scripts/
├── make_models.sh              # bake context-capped Ollama variants (CRITICAL for 16 GB tier)
├── build_public_proxy.py       # build gitignored Mohler ASAG public-proxy evidence
├── frontend_evidence.py        # collect component/build/E2E/visual-sweep release artifact
├── updater_evidence.py         # validate updater config/latest.json/signature/artifact
├── clean_machine_evidence.py   # collect signed/notarized clean-machine release evidence
└── seed_canvas_sandbox.py      # Canvas round-trip seeder/verifier (dry-run by default)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Fix OOM on 16 GB machines | `make_models.sh` | Caps `num_ctx=16384`, sets `temperature=0` |
| Build public-proxy evidence | `build_public_proxy.py` | Writes Mohler ASAG artifacts under `artifacts/public_proxy/`; optional local grading |
| Collect frontend release evidence | `frontend_evidence.py` | Runs component tests, production build, managed Playwright E2E, and reads visual-sweep metadata |
| Validate updater evidence | `updater_evidence.py` | Requires updater config, signed latest.json, and optional artifact filename/hash checks |
| Clean-machine release evidence | `clean_machine_evidence.py` | Writes `artifacts/release-evidence/clean-machine.json`; PASS requires signing plus structured manual evidence for first-run/crash/keyboard/updater checks |
| Seed Canvas sandbox | `seed_canvas_sandbox.py` | `--apply --students 6` to create + exercise |
| Tear down sandbox | `seed_canvas_sandbox.py` | `--teardown --course-id 106 --apply` |
| Understand Canvas API quirks | `seed_canvas_sandbox.py` | All 4 gotchas encoded in code comments |

## CONVENTIONS

- **`make_models.sh`**: `CTX` env var override: `GRADER_CTX=8192 ./scripts/make_models.sh`
- **`build_public_proxy.py`**: output is gitignored. `--grade` calls the local Grader model;
  `--grade-perturbations` is required before fairness can pass. Use `--limit` for short pilot
  scoring runs and `--anchor-exemplars-per-source ... --anchor-k ...` only for diagnostic anchored
  comparisons.
- **`frontend_evidence.py`**: starts and stops its own Vite dev server for Playwright. If it fails
  on port 5173, check for a stale dev server before rerunning.
- **`updater_evidence.py`**: blocks public beta when `latest.json` is missing or any platform
  signature is empty. It does not replace the clean-machine N→N+1 update test.
- **`seed_canvas_sandbox.py`**: dry-run by default. `--apply` to write. Refuses courses without `ZZ-GRADER-TEST` marker.
- **`clean_machine_evidence.py`**: cannot prove a clean-machine pass by itself. Generate the
  evidence template with `--print-manual-template`, fill it on the clean Mac with notes plus
  screenshot/log paths, then run with `--manual-evidence ... --evidence-root ...`. Manual flags
  without evidence are legacy-only and should not be used for release claims.
- Fake students use `ZZGraderTest` prefix + `.invalid` email domain (RFC 2606 — can never resolve)
- Canvas credentials: env vars (`CANVAS_BASE_URL`, `CANVAS_API_TOKEN`) or canvas-pp-cli's `credentials.toml`

## ANTI-PATTERNS

- **NEVER pipe `ollama list` into `grep -q` under `set -o pipefail`** — SIGPIPE makes installed models look missing. `make_models.sh:25` snapshots to a variable first.
- **NEVER run `seed_canvas_sandbox.py` without `--apply` expecting writes** — it stubs every write in dry-run mode.
- **NEVER point `seed_canvas_sandbox.py` at a real course** — `guard_sandbox()` checks for `ZZ-GRADER-TEST` marker in course name.
- **NEVER delete users not starting with `ZZGraderTest`** — teardown's `--delete-users` filters by prefix.

## NOTES

- **Why `make_models.sh` exists**: Ollama loads at full default context. Qwen3-VL's 262k default → 4B model consumed 41 GB. Capping at 16k → ~6 GB. Grading needs ~5,500 tokens.
- **Canvas gotchas in code**: bulk-update is async (poll Progress); submission files need `as_user_id` masquerade; Canvas returns HTML without `Accept: application/json`; Canvas Free-for-Teacher is dead.
- Live sandbox: course 106, assignment 2836, rubric 189, test student 8, cohort 9-14
- `seed_canvas_sandbox.py` is 621 lines — the cohort section (C1-C5) exercises multi-student paths the Test Student can't reach
