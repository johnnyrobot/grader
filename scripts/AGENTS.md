# SCRIPTS

Infrastructure scripts: model baking, Canvas sandbox seeding, public-proxy evidence, frontend
evidence, and release-readiness checks. Canvas and clean-machine scripts have safety-critical
guards.

## STRUCTURE

```
scripts/
├── make_models.sh              # bake context-capped Ollama variants (CRITICAL for 16 GB tier)
├── build_public_proxy.py       # build gitignored Mohler ASAG public-proxy evidence
├── dev_mock_engine.py          # synthetic loopback engine for safe local UI/Computer Use inspection
├── build_engine_bundle.py      # assemble the Python engine Grader.app ships (interpreter + deps + engine/)
├── verify_engine_bundle.py     # prove that bundle runs with no checkout, no uv, no system Python
├── frontend_evidence.py        # collect component/build/E2E/visual-sweep release artifact
├── bump_release_version.py     # dry-run-first N+1 version metadata updater
├── release_version_guard.py    # fail closed when tag/app/runtime versions drift
├── github_release_secrets.py   # optional legacy hosted-runner secret helper
├── github_release_preflight.py # read-only local-sign/public-assets readiness
├── publish_local_release.py    # guarded upload of verified public release assets
├── signing_doctor.py           # diagnose Developer ID private-key and notarytool readiness
├── updater_key_doctor.py       # diagnose Tauri updater private-key/password readiness
├── generate_appcast.py         # generate signed Tauri updater latest.json manifests
├── updater_evidence.py         # validate updater config/latest.json/signature/artifact
├── updater_channel_preflight.py # validate the live GitHub updater channel before N→N+1 testing
├── computer_use_sweep_evidence.py # validate signed-app Computer Use AX/screenshot captures
├── clean_machine_evidence.py   # collect signed/notarized clean-machine release evidence
├── release_evidence_package.py # assemble clean-machine tester handoff package
├── ports.py                    # source_revision — the one git-revision seam the evidence scripts share
├── seed_canvas_sandbox.py      # Canvas round-trip seeder/verifier (dry-run by default)
└── live_canvas_e2e.py          # guarded live Canvas import→approve→post→read-back E2E (dry-run by default)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Fix OOM on 16 GB machines | `make_models.sh` | Caps `num_ctx=16384`, sets `temperature=0` |
| Build public-proxy evidence | `build_public_proxy.py` | Writes Mohler ASAG artifacts under `artifacts/public_proxy/`; optional local grading |
| Inspect UI safely | `dev_mock_engine.py` | Serves synthetic queue/settings/models/rubric responses on loopback for local browser/Computer Use inspection only |
| Build the engine Grader.app ships | `build_engine_bundle.py` | Writes `app/src-tauri/resources/` (~1.1 GB: relocatable CPython from uv's managed store, runtime deps via `--target`, and `engine/`). `--check` verifies a complete bundle and is what `npm run build` runs; `--skip-runtime` refreshes only the engine source, for iterating on Python without a multi-minute rebuild |
| Prove the app can leave this machine | `verify_engine_bundle.py` | Relocates the bundle outside the checkout and starts it with no `uv`, no Homebrew, and a throwaway `HOME`; asserts `/health`, an authenticated request, that `lsof` shows no file open under the repo, and that the engine wrote nothing into the bundle. Run by `clean_machine_evidence.py` as `engine_bundle_self_contained`. Not a substitute for `foreign_machine_first_run` (issue #23) |
| Collect frontend release evidence | `frontend_evidence.py` | Runs component tests, production build, managed Playwright E2E, and validates visual-sweep screenshots/control coverage |
| Prepare N+1 version | `bump_release_version.py` | Dry-run-first helper that moves Tauri config, Rust crate/lockfile, frontend package/lockfile, Python project, engine launcher, and dev mock update fixture together |
| Guard release version | `release_version_guard.py` | Requires the release tag/version to match Tauri config, Rust crate/lockfile, frontend package/lockfile, Python project, engine launcher, and dev mock update fixture before publishing assets |
| Optional hosted-runner secrets | `github_release_secrets.py` | Legacy/optional helper; the selected local-signing release path does not require a `.p12` export or Apple secrets in GitHub |
| Preflight GitHub release publication | `github_release_preflight.py` | Read-only check for private source/public assets repo access, updater endpoint, release-source workflow, and optional N+1 tag/release availability |
| Publish locally signed assets | `publish_local_release.py` | Dry-run by default; guarded `--apply` uploads DMG, signed `.app.tar.gz`, signed `latest.json`, checksums, and source provenance to `johnnyrobot/grader-releases` |
| Diagnose signing credentials | `signing_doctor.py` | Writes `artifacts/release-evidence/signing.json`; probes Developer ID private-key access and notarytool profile before signing; `--print-repair-runbook` emits secret-free local recovery steps |
| Diagnose updater key | `updater_key_doctor.py` | Writes `artifacts/release-evidence/updater-key-current.json`; signs a tiny temp file to verify the Tauri updater key and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, either from a private env var or a named macOS Keychain item |
| Generate updater manifest | `generate_appcast.py` | Requires `--signing-key` for release manifests; can read encrypted-key password from a private env var or named macOS Keychain item; use `--allow-unsigned` only for local smoke fixtures |
| Validate updater evidence | `updater_evidence.py` | Requires updater config, signed latest.json, artifact filename/hash/source-freshness checks, and local artifact signature/notarization checks |
| Preflight updater channel | `updater_channel_preflight.py` | Fails fast when the configured GitHub `latest.json` is absent, unsigned, points at the wrong artifact/channel, is not newer than installed N, or advertises unreachable release assets |
| Validate signed-app Computer Use sweep | `computer_use_sweep_evidence.py` | Produces PASS/FAIL JSON from `.ax.txt` + `.png` captures and fails stale approved/unsaved review states |
| Clean-machine release evidence | `clean_machine_evidence.py` | Writes `artifacts/release-evidence/clean-machine.json`; PASS requires signing, app/DMG/latest.json provenance, current-source freshness, the automated `engine_bundle_self_contained` check, plus structured manual evidence for foreign-machine-first-run/first-run/crash/keyboard/Computer Use/updater checks |
| Package clean-machine handoff | `release_evidence_package.py` | Copies or references signed release artifacts, evidence JSON, clean-machine runbook, and manual template for a clean-Mac tester |
| Seed Canvas sandbox | `seed_canvas_sandbox.py` | `--apply --students 6` to create + exercise |
| Tear down sandbox | `seed_canvas_sandbox.py` | `--teardown --course-id 106 --apply` |
| Understand Canvas API quirks | `seed_canvas_sandbox.py` | All 4 gotchas encoded in code comments |

## CONVENTIONS

- **`make_models.sh`**: `CTX` env var override: `GRADER_CTX=8192 ./scripts/make_models.sh`
- **`build_public_proxy.py`**: output is gitignored. `--grade` calls the local Grader model;
  `--grade-perturbations` is required before fairness can pass. Use `--limit` for short pilot
  scoring runs and `--anchor-exemplars-per-source ... --anchor-k ...` only for diagnostic anchored
  comparisons.
- **`frontend_evidence.py`**: starts and stops its own Vite dev server for Playwright. It removes
  stale visual-sweep JSON before E2E and fail-closes unless the new sweep has every required
  instructor screen, screenshot, and core control label. If it fails on port 5173, check for a stale
  dev server before rerunning.
- **`dev_mock_engine.py`**: safe for local/manual/Computer Use UI inspection. It uses synthetic
  fixture data only and is not release evidence by itself; signed clean-machine claims still require
  `clean_machine_evidence.py`.
- **`release_version_guard.py`**: run before signing/publishing release assets:
  `uv run python scripts/release_version_guard.py --tag v0.1.1 --out artifacts/release-evidence/release-version.json`.
  It prevents tag/app/runtime metadata drift across `tauri.conf.json`, `Cargo.toml`,
  `Cargo.lock`, `package.json`, `package-lock.json`, `pyproject.toml`, `scripts/start_engine.sh`,
  and `scripts/dev_mock_engine.py`. The GitHub release workflow runs it before building so a tag
  such as `v0.1.1` cannot publish an app bundle or update-check path still reporting an older
  version.
- **`bump_release_version.py`**: run before the N+1 release-prep commit:
  `uv run python scripts/bump_release_version.py --target-version <N+1>` for a dry run, then add
  `--apply` only when deliberately preparing the release commit. It performs no git, tag, GitHub,
  signing, or publishing operations.
- **`github_release_preflight.py`**: run before creating or pushing the N+1 release tag:
  `uv run python scripts/github_release_preflight.py --candidate-tag v<N+1>`.
  It is read-only: it checks `gh` access, the configured public updater repository, source and
  release repo access, release-source Python/frontend/Rust/Tauri checks, release-version
  consistency, and optional candidate tag/release availability. Apple signing/notarization is local
  and requires no GitHub Actions secrets. A stale inactive GitHub account can be a warning when the
  active account can read both repositories.
- **`publish_local_release.py`**: use after local signing, notarization, signed-manifest generation,
  and updater evidence. It is dry-run by default and `--apply` fails closed unless `latest.json`
  contains signed macOS entries targeting the exact public release tag. It uploads only the DMG,
  Tauri-compatible `.app.tar.gz`, manifest, SHA-256 checksums, and source provenance; never the updater private key or passwords.
- **`github_release_secrets.py`**: retained only for a future hosted-runner signing design. It is not
  part of the selected public-beta path and should not be used merely because a local `.p12`
  password does not exist.
- **`updater_evidence.py`**: blocks public beta when `latest.json` is missing, any platform
  signature is empty, the manifest does not reference the supplied artifact, the supplied
  manifest/artifact predates the current git commit, or the local artifact fails
  signature/notarization checks. For `.app.tar.gz` bundles it extracts and verifies the bundled
  `.app` with codesign and spctl. It does not replace the clean-machine N→N+1 update test.
- **`generate_appcast.py`**: despite the historical name, this creates Tauri `latest.json`
  manifests, not Sparkle XML. It fails closed without a Tauri updater signing key unless
  `--allow-unsigned` is supplied for a non-release smoke fixture. Encrypted updater keys also need
  `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` in non-interactive release runs, or
  `--password-keychain-service TAURI_SIGNING_PRIVATE_KEY_PASSWORD --password-keychain-account grader-updater`
  after the private release operator stores the password in macOS Keychain. `--app` packages the
  signed `.app` as the `.app.tar.gz` format required by Tauri's macOS updater.
- **`updater_key_doctor.py`**: run before `generate_appcast.py` when the updater manifest is the
  blocker. It signs a temporary file and writes a secret-free JSON artifact; it does not print or
  store the private key or password. Use `--print-private-runbook` for env-var and Keychain setup
  commands that keep the password out of chat and repo files. A PASS means the key/password can sign
  updater artifacts, not that the release manifest or clean-machine updater path has passed.
- **`updater_channel_preflight.py`**: run immediately after publishing the signed N+1 release
  manifest and before asking a clean-machine tester to exercise the updater path:
  `uv run python scripts/updater_channel_preflight.py --current-version <installed-N-version>`.
  It fetches the configured GitHub `latest.json`, requires a newer version, signed darwin manifests,
  the expected `Grader.app.tar.gz` artifact name, the `johnnyrobot/grader-releases` release channel, and reachable
  release asset URLs via HEAD/Range probes. It does not replace the in-app N→N+1 evidence; it catches
  a missing or malformed release channel early.
- **`signing_doctor.py`**: run before `sign_and_notarize.sh`. It fail-closes on missing Developer
  ID certificates, inaccessible private keys, invalid notarytool profiles, and missing hardened
  runtime/updater config. Use `--print-repair-runbook` to generate local-only recovery commands for
  keychain private-key ACLs and notarytool profile setup; do not commit filled credentials or paste
  secrets into chat. Use `--skip-notary` only for explicit sign-only testing.
- **`computer_use_sweep_evidence.py`**: run after a Computer Use sweep of the signed app:
  `uv run python scripts/computer_use_sweep_evidence.py --capture-dir <captures> --app /path/to/Grader.app --expected-version <version> --out <evidence-root>/computer_use_instructor_sweep.json`.
  It requires accessibility-tree/screenshot pairs, checks core instructor screens, records the
  inspected `.app` bundle id/version/tree hash, and fail-closes on wrong app versions or stale review
  states such as approved + "Teacher changes not saved yet" or "Score updated from N to N". Include
  the PASS JSON in clean-machine manual evidence for `computer_use_instructor_sweep`.
- **`seed_canvas_sandbox.py`**: dry-run by default. `--apply` to write. Refuses courses without `ZZ-GRADER-TEST` marker.
  `--reset-grades --course-id N --assignment-id M` clears the `ZZGraderTest` students' grades so the
  live E2E can run again on the same students; it never touches a submission whose owner's name
  lacks the prefix.
- **`live_canvas_e2e.py`**: dry-run by default; `--apply` writes to live Canvas and the course name
  must start with `ZZ-GRADER-TEST`. Needs **two ungraded submissions with work in them** — one for
  the ordinary post path, one for the upload-failure probe, since a grade Canvas has taken cannot be
  posted again. Recycle the ones already there rather than provisioning more students:
  `seed_canvas_sandbox.py --reset-grades --course-id 106 --assignment-id 2841 --apply`. Writes its
  feedback file under `--attachment-dir` (default `$GRADER_DATA_DIR`, else `fixtures`), because the
  engine refuses to send a file from anywhere else. The probe **PUTs an unroutable Canvas URL into
  the running engine's settings** and restores it in a `finally`; a `SIGKILL` mid-probe leaves the
  engine pointed at loopback, so check `/api/settings` if a run is killed.
- **`clean_machine_evidence.py`**: cannot prove a clean-machine pass by itself. Generate the
  tester runbook with `--print-manual-runbook` and the evidence template with
  `--print-manual-template`. Fill it on the clean Mac with `passed: true`, `performed_at`, `tester`,
  notes, and existing screenshot/log paths, then run with `--app ... --dmg ... --updater-manifest ...
  --expected-version ... --manual-evidence ... --evidence-root ...` so the PASS records the exact
  tested app/DMG/latest.json hashes and confirms those artifacts are fresh relative to the current
  git commit. If the loose build `.app` signature is stale, the script mounts the supplied DMG and
  verifies the bundled `.app` for the app codesign/spctl checks. The template includes
  `computer_use_instructor_sweep`; this is the required signed-app Computer Use inspection and
  complements the automated Playwright visual sweep. That manual check requires a PASS JSON artifact
  from `computer_use_sweep_evidence.py`. Manual flags without evidence are legacy-only and should not
  be used for release claims. The template also includes `foreign_machine_first_run`: the signed app
  launched on a Mac that has **never held this repository**. A fresh user profile on the build
  machine does not satisfy it — that is exactly what produced the 0.1.1 evidence that missed #23,
  because the old engine was found on the filesystem, which a new profile does not change. The
  automated `engine_bundle_self_contained` check runs `verify_engine_bundle.py` and narrows the gap
  on the build machine; it does not close it.
- **`release_evidence_package.py`**: build this after signing/notarization and updater evidence so a
  clean-machine tester receives one deterministic, gitignored handoff folder:
  `uv run python scripts/release_evidence_package.py --out-dir artifacts/release-evidence/clean-machine-handoff`.
  It writes hashes/status summaries, copies the release DMG/updater bundle/latest.json when supplied, and includes
  `clean-machine-runbook.md`, `clean-machine-manual-evidence.json`,
  `github-release-secrets-runbook.md`, and `updater-key-private-runbook.md`. Include
  `--github-release-evidence artifacts/release-evidence/github-release-preflight-current.json` so
  the tester sees whether publication prerequisites have passed. It is a handoff package, not
  release evidence by itself.
- **`release_readiness.py`**: pass `--expected-version ...` for release runs. The script defaults
  to `app/src-tauri/tauri.conf.json`, records artifact path/mtime/SHA-256 metadata, and rejects PASS
  updater or clean-machine artifacts whose recorded version does not match the evaluated app. Its
  JSON/Markdown report includes the end-to-end public-beta matrix; keep new public-beta evidence
  requirements represented there instead of only in prose. The matrix has two profiles:
  `supervised_public_beta`, which `decide_verdict` answers, and `ship_v1`, a superset carrying what
  only a beta can produce — a `ship_v1`-only row blocks v1.0 and can never move the beta verdict, so
  put a requirement there when it is a ship signal rather than an entry gate. Both profiles reach the
  report (`e2e_matrix`, `ship_v1_matrix`). `--draft-bias-artifact` supplies the teacher-local
  `Draft bias` evidence the `ship_v1` calibration row reads; it is never committed (`teacher_local`
  tier) and its absence is `BLOCKED_TEACHER_APPROVALS`, not a failure. It has no producer script on
  purpose — nothing here can reach a teacher's machine — so it is written by hand from one
  `GET /api/calibration/<assignment_id>` response during the supervised beta:
  `{"status": "PASS", "evidence_tier": "teacher_local", "assignment_id": ..., "n": ..., "draft_bias": ...}`,
  with `n` at or above `EVIDENCE_FLOOR` and a non-null `draft_bias`. Never paste student work,
  names, or scores into it.
- Fake students use `ZZGraderTest` prefix + `.invalid` email domain (RFC 2606 — can never resolve)
- Canvas credentials: env vars (`CANVAS_BASE_URL`, `CANVAS_API_TOKEN`) or canvas-pp-cli's `credentials.toml`

## ANTI-PATTERNS

- **NEVER pipe `ollama list` into `grep -q` under `set -o pipefail`** — SIGPIPE makes installed models look missing. `make_models.sh:25` snapshots to a variable first.
- **NEVER run `seed_canvas_sandbox.py` without `--apply` expecting writes** — it stubs every write in dry-run mode.
- **NEVER point `seed_canvas_sandbox.py` at a real course** — `guard_sandbox()` checks for `ZZ-GRADER-TEST` marker in course name.
- **NEVER delete users not starting with `ZZGraderTest`** — teardown's `--delete-users` filters by prefix.
- **NEVER treat a `live_canvas_e2e.py` PASS as current without checking `source_commit`** — the artifact records the commit it ran against precisely so a run from three commits ago cannot be quoted as proof of present behaviour.

## NOTES

- **Why `make_models.sh` exists**: Ollama loads at full default context. Qwen3-VL's 262k default → 4B model consumed 41 GB. Capping at 16k → ~6 GB. Grading needs ~5,500 tokens.
- **Canvas gotchas in code**: bulk-update is async (poll Progress); submission files need `as_user_id` masquerade; Canvas returns HTML without `Accept: application/json`; Canvas Free-for-Teacher is dead. Two more, both found by reading results back off Canvas rather than trusting a 200: `submissions update` needs a **nested** JSON body over `--stdin` (bracket keys are form-encoding, and as JSON keys Canvas ignores them and echoes the unchanged submission), and `submissions index` returns **only Canvas's first page of ten** with no flag to ask for more — `grader/canvas.py` paginates that one itself.
- Live sandbox: course 106, assignment 2836, rubric 189, test student 8, cohort 9-14
- `seed_canvas_sandbox.py` is 621 lines — the cohort section (C1-C5) exercises multi-student paths the Test Student can't reach
