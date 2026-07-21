# Grader

A **fully-local** macOS desktop app that helps college instructors grade anything — essays,
problem sets, lab reports, code, scanned handwriting — with an AI that learns to grade **the way
you grade**.

Student work never leaves your machine.

---

## Why

Grading is the largest unautomated time sink in teaching. Cloud AI graders exist, but they
(a) require sending student work to third parties — a FERPA problem most institutions prohibit,
(b) grade like a generic model rather than like *you*, and (c) position the AI as the authority.

Grader inverts all three. Local models. Your rubric, your source materials, and your own
previously-graded work as the source of truth. And **you approve every score** — the AI drafts,
you decide.

## How the calibration works

You grade a few papers. Grader embeds them. From then on, every draft grade is conditioned on
**your most similar previously-graded work**, injected as anchor examples:

> *"I scored this a 4 on Evidence because it resembles these three you scored 4 — same pattern of
> citing a source without integrating it."*

This is how human raters calibrate, and it's what the research supports. It works from your first
graded paper, improves every time you grade one more, and it's explainable — no black box, no
per-teacher model to train.

## Status

🟢 **Ready for a supervised public beta under the two-tier evidence standard.** The engine,
neuroinclusive desktop review workflow, public-proxy quality/fairness, guarded Canvas
score/comment round-trip, local Developer ID signing/notarization, public updater channel, and
signed 0.1.0 → 0.1.1 in-app update path pass. Citation verification remains visibly experimental,
and teacher-specific accuracy claims still require institution-authorized local calibration data.

| | |
|---|---|
| Engine (grading, calibration, triage) | 🟢 Working |
| Calibration validation | 🟡 P1 built (anchors +0.135 QWK at k=6) |
| Desktop app (Tauri + React) | 🟢 Built — light/dark preferences, onboarding, queue, review, settings, model manager |
| UI verification | 🟢 16 component tests + 2 API tests + 4 Playwright E2E tests; no serious/critical axe findings |
| Canvas integration | 🟢 Live app-level import → teacher override/approve → post → Progress complete → score/comment read-back passed in `ZZ-GRADER-TEST` |
| Document parsing + handwriting | 🟡 Built — Docling + Apple Vision + grader-vision |
| Citation verification | 🟡 20/29 correct (69%); retraction check 3/3; accepted only as visibly experimental until accuracy reaches the enabled-mode gate |
| CI gates | 🟢 Privacy PASS; perf PASS; public-proxy quality/fairness PASS; teacher-local still unvalidated |
| GitHub release preflight | 🟢 PASS — private source repo + public assets repo; no Apple `.p12` or hosted signing secrets required |
| Updater evidence | 🟢 PASS — signed `Grader.app.tar.gz` validates after extraction and matches the public manifest |
| Signing + notarization | 🟢 PASS — Developer ID private key and stored `grader` notarytool profile verified |
| Updates | 🟢 PASS — public 0.1.1 channel and real signed 0.1.0 → 0.1.1 install/relaunch path verified |
| Beta rollout | 🟢 `supervised_public_beta` — 19/19 release-matrix rows pass |

See [`docs/HANDOFF.md`](docs/HANDOFF.md) for exact status and [`docs/phases/`](docs/phases/) for
per-phase detail.

## Public beta readiness

**Verdict as of 2026-07-13: supervised public beta ready.** This is not an unattended or
teacher-specific accuracy claim: early testers need guided onboarding, citations stay experimental,
and real student work requires institutional authorization and the documented FERPA controls.

Confirmed in the current checkout:

- Python tests: 234 passed.
- Frontend tests: 18 passed; TypeScript/Vite production build and Rust `cargo check` pass.
- The complete mocked desktop workflow passes in Playwright across onboarding, queue, review,
  settings, model management, and local-engine recovery. The visual sweep now fail-closes unless it
  records all 16 required screens, screenshots, 47 required instructor controls, 167 visible
  controls, 200% text scaling, and automated accessibility scans.
- Signed-app Computer Use sweeps now have their own verifier:
  `scripts/computer_use_sweep_evidence.py` validates `.ax.txt`/screenshot capture pairs and
  fail-closes on stale review states such as approved + “Teacher changes not saved yet.”
- The privacy gate passes in isolation: 3/3 captured connections were allowlisted.
- The performance gate passes: 37.8 seconds for a 1,500-word essay, with a projected 30-item
  batch of 1,134 seconds against a 2,700-second budget.
- Public-proxy quality and fairness now pass on the local anchored Mohler ASAG public-proxy
  artifact: 40/40 scored rows, QWK 0.845 against a 0.623 human-ceiling-adjusted floor, both source
  checks passing, all five perturbation families passing, and 0 grading errors.
- Guarded live Canvas app E2E passes on `canvas.johnnyrobot.ai` course `106`, assignment `2841`:
  import → teacher override/approval → Canvas post → Progress completed → exact score and at
  least one teacher feedback comment read back.
- `scripts/release_readiness.py` writes a consolidated JSON/Markdown verdict with blocker
  summaries plus an end-to-end testing and evaluation matrix that maps public-beta requirements to
  evidence. The current report is `supervised_public_beta`: all 19 matrix rows pass, including
  fresh-profile model reconciliation, crash recovery, keyboard-only review, Computer Use, signed
  release provenance, the public updater channel, and the in-app 0.1.0 → 0.1.1 update/relaunch.

Supervised-beta boundaries:

1. Keep citation checking visibly experimental for beta users unless accuracy reaches at least 90%.
2. Keep Canvas rubric-assessment posting disabled until local rubric criteria can be mapped to real
   Canvas criterion IDs; score/comment round-trip is validated, attachment write-back still needs
   separate evidence.
3. Keep Apple signing/notarization local using the verified Developer ID identity and `grader`
   Keychain profile. Require `scripts/bump_release_version.py --target-version <N+1> --apply` and
   `scripts/github_release_preflight.py --candidate-tag v<N+1>` to pass before publishing; no
   certificate `.p12` export or Apple password belongs in GitHub.
4. Keep the public `latest.json`, signed `Grader.app.tar.gz`, DMG, checksums, and provenance
   together. The signed/notarized 0.1.0 → 0.1.1 in-app install/relaunch path passed against
   `/Applications/Grader.app`; rerun it for every future release bundle or updater-key change.
5. Keep teacher-local validation separate until an instructor supplies authorized historical grading.
6. Complete a supervised private pilot before opening enrollment publicly.

## Design commitments

1. **No student PII leaves the device.** The only outbound traffic is your own Canvas, model
   downloads, and *cited-source* lookups (a DOI, a title, a URL). Enforced by an allowlist and a
   CI test that fails the build on any stray packet.
2. **Nothing posts without your approval.** Enforced in the data layer, not just the UI.
3. **Show your work.** Every score cites the rubric criterion, the evidence, and the anchors that
   informed it.
4. **Formative first.** Research consistently finds people accept AI *feedback* but distrust AI
   *scores* — so you always own the number.
5. **Adaptable presentation.** Light mode is the default; dark mode, spacious density, and reduced
   motion are persisted preferences built from semantic design tokens.

## Stack

Tauri + React shell · Python engine (FastAPI) · **Pydantic AI v2** · **Ollama** (→ MLX) ·
**MiniCPM-V 4.6** (transcription) + **Qwen3 8B** (grading) · **Docling** (parsing) · SQLite + local embeddings ·
Canvas via [canvas-pp-cli](https://github.com/mvanhorn/printing-press-library). Canvas credentials
can come from `CANVAS_BASE_URL` + `CANVAS_API_TOKEN`/`CANVAS_TOKEN`, or from the already configured
`canvas-pp-cli` `config.toml` + `credentials.toml` files.

Every choice is justified with citations in [`RESEARCH.md`](RESEARCH.md).

## Development

```bash
uv sync                                              # deps
ollama pull qwen3:8b && ollama pull minicpm-v4.6:latest
./scripts/make_models.sh                             # create capped grader tags

uv run python engine/bench.py                        # which model/output-mode works
uv run python engine/calibrate.py --n-test 20        # do teacher anchors actually help?

cd app && npm test -- --run                          # React component tests
cd app && npm run build                              # TypeScript + Vite production build
cd app && npm run test:e2e                           # requires the Vite dev server on :5173
cd app/src-tauri && cargo check                      # desktop Rust boundary
```

## Public beta gate commands

```bash
uv run python scripts/build_public_proxy.py mohler-asag --out artifacts/public_proxy/mohler_asag_diverse_anchored_pilot --grade --grade-perturbations --anchor-exemplars-per-source 40 --anchor-k 6
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir artifacts/public_proxy/mohler_asag_diverse_anchored_pilot
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir artifacts/public_proxy/mohler_asag_diverse_anchored_pilot
uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json --output artifacts/citation-results.json
uv run python scripts/frontend_evidence.py --out artifacts/release-evidence/frontend.json
uv run python scripts/dev_mock_engine.py  # optional: safe synthetic backend for local UI/Computer Use inspection
uv run python scripts/signing_doctor.py --out artifacts/release-evidence/signing.json --no-fail
uv run python scripts/signing_doctor.py --print-repair-runbook > signing-repair-runbook.md  # local-only recovery steps if the doctor blocks
uv run python scripts/release_version_guard.py --tag v0.1.1 --out artifacts/release-evidence/release-version.json
uv run python scripts/bump_release_version.py --target-version 0.1.1 --allow-same --out artifacts/release-evidence/version-bump-plan.json  # dry-run/idempotency check; use --apply only when preparing a future release commit
uv run python scripts/github_release_preflight.py --candidate-tag v0.1.1 --out artifacts/release-evidence/github-release-preflight-current.json --no-fail
uv run python scripts/updater_key_doctor.py --print-private-runbook > updater-key-private-runbook.md
uv run python scripts/updater_key_doctor.py --signing-key tauri-updater-key.pem --password-keychain-service TAURI_SIGNING_PRIVATE_KEY_PASSWORD --password-keychain-account grader-updater --out artifacts/release-evidence/updater-key-current.json
uv run python scripts/generate_appcast.py --app /path/to/Grader.app --version 0.1.1 --signing-key tauri-updater-key.pem --password-keychain-service TAURI_SIGNING_PRIVATE_KEY_PASSWORD --password-keychain-account grader-updater --output latest.json
uv run python scripts/updater_evidence.py --manifest latest.json --artifact /path/to/Grader.app.tar.gz --out artifacts/release-evidence/updater-current.json  # fails if local artifacts predate the current git commit
uv run python scripts/publish_local_release.py --dmg /path/to/Grader.dmg --updater-bundle /path/to/Grader.app.tar.gz --manifest latest.json --version 0.1.1  # dry-run; add --apply only after all local evidence passes
uv run python scripts/updater_channel_preflight.py --current-version 0.1.0 --out artifacts/release-evidence/updater-channel-current.json  # run after publishing 0.1.1 to test installed 0.1.0 -> 0.1.1
uv run python scripts/live_canvas_e2e.py --apply --course-id 106 --assignment-id 2841 --course-name "ZZ-GRADER-TEST-SANDBOX (safe to delete)" --output artifacts/release-evidence/canvas-live-current.json
uv run python scripts/clean_machine_evidence.py --print-manual-runbook > clean-machine-runbook.md
uv run python scripts/clean_machine_evidence.py --print-manual-template > clean-machine-manual-evidence.json
uv run python scripts/computer_use_sweep_evidence.py --capture-dir /path/to/computer-use-captures --out /path/to/evidence-root/computer_use_instructor_sweep.json
uv run python scripts/clean_machine_evidence.py --app /Applications/Grader.app --dmg /path/to/Grader.dmg --updater-manifest /path/to/latest.json --expected-version 0.1.1 --manual-evidence clean-machine-manual-evidence.json --evidence-root /path/to/screenshots-and-logs --out artifacts/release-evidence/clean-machine.json
uv run python scripts/release_evidence_package.py --out-dir artifacts/release-evidence/clean-machine-handoff --github-release-evidence artifacts/release-evidence/github-release-preflight-current.json --updater-channel-evidence artifacts/release-evidence/updater-channel-current.json
uv run python scripts/release_readiness.py --profile public_beta --expected-version 0.1.1 --public-proxy-dir artifacts/public_proxy/mohler_asag_diverse_anchored_pilot --citation-artifact artifacts/citation-results.json --canvas-artifact artifacts/release-evidence/canvas-live-current.json --privacy-artifact artifacts/release-evidence/privacy.json --performance-artifact artifacts/release-evidence/performance.json --frontend-artifact artifacts/release-evidence/frontend.json --rust-artifact artifacts/release-evidence/rust.json --release-version-artifact artifacts/release-evidence/release-version.json --github-release-artifact artifacts/release-evidence/github-release-preflight-current.json --updater-artifact artifacts/release-evidence/updater-current.json --updater-channel-artifact artifacts/release-evidence/updater-channel-current.json --updater-key-artifact artifacts/release-evidence/updater-key-current.json --clean-machine-artifact artifacts/release-evidence/clean-machine-local-current.json --out artifacts/release-readiness
```

The clean-machine manual template is fail-closed: every manual row must include `passed: true`,
tester/timestamp/notes, at least one existing evidence file, and all check-specific `assertions`
set to `true`. The Computer Use row additionally requires a `PASS` JSON from
`scripts/computer_use_sweep_evidence.py`. `scripts/release_evidence_package.py` can assemble the
signed DMG/updater bundle/latest.json, current evidence JSON, runbook, and manual template into one gitignored
clean-machine tester handoff folder; it is not a substitute for the clean-machine test itself.

The committed `fixtures/public_proxy/` set is a tiny mechanics fixture. Real public-proxy datasets
must be downloaded into gitignored local storage and reported separately from teacher-local data.
The release commands above intentionally fail on the tiny fixture; use
`--allow-mechanics-fixture` only for local smoke tests of the gate math.

```bash
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir fixtures/public_proxy --allow-mechanics-fixture
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir fixtures/public_proxy --allow-mechanics-fixture
```

For diagnostic comparisons, the Mohler builder can also run limited anchor-conditioned scoring
passes, for example:

```bash
uv run python scripts/build_public_proxy.py mohler-asag --out artifacts/public_proxy/mohler_asag_anchored_pilot --grade --anchor-exemplars-per-source 40 --anchor-k 6 --force-regrade --limit 4
```

The calibration run uses [Mohler ASAG](https://huggingface.co/datasets/nkazi/MohlerASAG)
(CC BY 4.0) — 2,273 short answers, each scored by **two independent human graders**, so we can
report agreement against the *human ceiling* rather than against an arbitrary target. The current
anchored 40-item Mohler public-proxy scoring run produced 40 scored rows and passes the quality
gate at QWK 0.845 against a 0.623 adjusted floor, and the matching fairness perturbation run
passes. Use `scripts/signing_doctor.py` before `scripts/sign_and_notarize.sh` to verify the
Developer ID private key and notarytool profile before mutating a release bundle. If it blocks on
`errSecInternalComponent` or a missing notary profile, generate the local-only repair runbook with
`uv run python scripts/signing_doctor.py --print-repair-runbook`; do not paste Apple credentials or
keychain passwords into chat or commit them. `scripts/generate_appcast.py` now fails closed unless a
Tauri updater signing key is provided; set `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` for encrypted updater
keys in a private shell, or store it in macOS Keychain and pass
`--password-keychain-service TAURI_SIGNING_PRIVATE_KEY_PASSWORD --password-keychain-account grader-updater`.
`--allow-unsigned` is only for local smoke fixtures.
`scripts/release_readiness.py --expected-version ...` records artifact path/mtime/SHA-256 metadata
and fails PASS updater or clean-machine artifacts that do not match the evaluated app version.
`scripts/release_version_guard.py` fails release tags whose app/package/runtime-update metadata still
reports a different version. `scripts/bump_release_version.py` is the dry-run-first helper for
moving those eight surfaces together before an N+1 release commit: Tauri config, Rust crate and
lockfile, frontend package and lockfile, Python project, engine launcher `GRADER_APP_VERSION`, and
the dev mock update fixture. The current supervised public-beta gate passes; use
`scripts/bump_release_version.py --target-version <N+1> --apply` in the release-prep commit and
`scripts/github_release_preflight.py --candidate-tag v<N+1>` before creating the release tag so
private/public repository access, version drift, missing release-source tests, or stale tag/release
state are caught without writing to GitHub. The GitHub workflow verifies Python tests, frontend
component tests, Rust `cargo check`, and an unsigned Tauri build; Apple signing/notarization stays
on the maintainer Mac. `scripts/publish_local_release.py --apply` uploads only the verified DMG,
signed `Grader.app.tar.gz`, signed `latest.json`, checksums, and source provenance to the public assets repository. Use
`scripts/updater_key_doctor.py` to verify the updater key/password before regenerating
`latest.json`, and use `scripts/updater_channel_preflight.py` after publishing N+1 to confirm the
live release channel advertises a signed newer, reachable `Grader.app.tar.gz` before clean-machine updater
testing starts.

## Docs

| | |
|---|---|
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | **Start here** — status, gotchas, next command |
| [`docs/INSTALL.md`](docs/INSTALL.md) | Installation guide (from source + release builds) |
| [`docs/PRD.md`](docs/PRD.md) | Product requirements |
| [`docs/SPEC.md`](docs/SPEC.md) | Technical spec sheet |
| [`docs/DATA-PLAN.md`](docs/DATA-PLAN.md) | Datasets, licenses, access paths |
| [`docs/FERPA.md`](docs/FERPA.md) | FERPA privacy one-pager |
| [`docs/RUBRIC_AUTHORING.md`](docs/RUBRIC_AUTHORING.md) | Rubric authoring guide |
| [`docs/BETA_ROLLOUT.md`](docs/BETA_ROLLOUT.md) | Beta program guide + feedback form |
| [`RESEARCH.md`](RESEARCH.md) | The cited architecture report |
| [`docs/research/`](docs/research/) | Raw research (adversarially verified) |

## License

Apache 2.0.
