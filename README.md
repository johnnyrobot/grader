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

🚧 **Developer-assisted alpha; not ready for an unattended public beta.** The engine and desktop
review workflow work, including the neuroinclusive light-first interface and the tiered
release-gate machinery. Public-proxy quality/fairness pass and Canvas score/comment round-trip is
validated, and a current-source local app/DMG now signs, notarizes, staples, and passes macOS
signature checks. Signed updater-manifest evidence now passes locally. Public beta is still blocked
by clean-machine updater N→N+1 behavioral validation.

| | |
|---|---|
| Engine (grading, calibration, triage) | 🟢 Working |
| Calibration validation | 🟡 P1 built (anchors +0.135 QWK at k=6) |
| Desktop app (Tauri + React) | 🟢 Built — light/dark preferences, onboarding, queue, review, settings, model manager |
| UI verification | 🟢 14 component tests + 4 Playwright E2E tests; no serious/critical axe findings |
| Canvas integration | 🟢 Live app-level import → teacher override/approve → post → Progress complete → score/comment read-back passed in `ZZ-GRADER-TEST` |
| Document parsing + handwriting | 🟡 Built — Docling + Apple Vision + grader-vision |
| Citation verification | 🟡 20/29 correct (69%); retraction check 3/3; accepted only as visibly experimental until accuracy reaches the enabled-mode gate |
| CI gates | 🟢 Privacy PASS; perf PASS; public-proxy quality/fairness PASS; teacher-local still unvalidated |
| Updater evidence | 🟢 Signed `latest.json` references the current-source signed/notarized DMG and passes updater evidence |
| Signing + notarization | 🟢 Current-source local app/DMG signed, notarized, stapled, and passed `codesign`/`stapler`/`spctl` checks |
| Updates | 🟡 Tauri updater integrated with signed manifest generation and in-app install/restart UI; real N→N+1 path still needs release-channel evidence |
| Beta rollout | ⬜ Blocked until release-readiness report returns `supervised_public_beta` |

See [`docs/HANDOFF.md`](docs/HANDOFF.md) for exact status and [`docs/phases/`](docs/phases/) for
per-phase detail.

## Public beta readiness

**Verdict as of 2026-07-13: no.** Grader is suitable for a developer-assisted usability alpha
using synthetic data or institution-approved test data. It is not ready for an unattended public
beta with real student work.

Confirmed in the current checkout:

- Python tests: 130 passed, 12 intentionally skipped.
- Frontend tests: 13 passed; TypeScript/Vite production build and Rust `cargo check` pass.
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
  import → targeted grade for Canvas user `9` → teacher override/approval → Canvas post →
  Progress completed → score `2` and one teacher feedback comment read back.
- `scripts/release_readiness.py` writes a consolidated JSON/Markdown verdict with blocker
  summaries plus an end-to-end testing and evaluation matrix that maps public-beta requirements to
  evidence. With current local artifacts, the report is `blocked`: the latest local app/DMG signs,
  notarizes, staples, and passes macOS signature checks; signed updater manifest/release provenance
  passes; and local signed-app evidence covers fresh-profile first-run model reconciliation, crash
  recovery, keyboard-only review, and the Computer Use instructor sweep. The only remaining
  clean-machine blocker is the updater N→N+1 path.

Public-beta blockers:

1. Keep citation checking visibly experimental for beta users unless accuracy reaches at least 90%.
2. Keep Canvas rubric-assessment posting disabled until local rubric criteria can be mapped to real
   Canvas criterion IDs; score/comment round-trip is validated, attachment write-back still needs
   separate evidence.
3. Validate the updater N→N+1 path on a clean supported Mac against the signed `latest.json` and
   signed/notarized DMG. The current local signed-app fresh-profile first-run, crash recovery,
   Computer Use sweep, keyboard-only review path, signed updater manifest, and native
   install/restart UI pass static/build checks against `/Applications/Grader.app` installed as
   `com.grader.app`; rerun those checks if the release bundle identity changes.
4. Keep teacher-local validation separate until an instructor supplies authorized historical grading.
5. Complete a supervised private pilot before opening enrollment publicly.

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
Canvas via [canvas-pp-cli](https://github.com/mvanhorn/printing-press-library)

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
uv run python scripts/release_version_guard.py --tag v0.1.0 --out artifacts/release-evidence/release-version.json
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD='<updater-key-password>'  # required for encrypted updater keys
uv run python scripts/updater_key_doctor.py --signing-key /path/to/tauri-updater-key.pem --out artifacts/release-evidence/updater-key.json
uv run python scripts/generate_appcast.py --dmg /path/to/Grader.dmg --version 0.1.0 --signing-key /path/to/tauri-updater-key.pem --output latest.json
uv run python scripts/updater_evidence.py --manifest latest.json --artifact /path/to/Grader.dmg --out artifacts/release-evidence/updater.json  # fails if local artifacts predate the current git commit
uv run python scripts/updater_channel_preflight.py --current-version 0.1.0 --out artifacts/release-evidence/updater-channel-current.json  # run after publishing the N+1 release
uv run python scripts/live_canvas_e2e.py --dry-run
uv run python scripts/clean_machine_evidence.py --print-manual-runbook > clean-machine-runbook.md
uv run python scripts/clean_machine_evidence.py --print-manual-template > clean-machine-manual-evidence.json
uv run python scripts/computer_use_sweep_evidence.py --capture-dir /path/to/computer-use-captures --out /path/to/evidence-root/computer_use_instructor_sweep.json
uv run python scripts/clean_machine_evidence.py --app /Applications/Grader.app --dmg /path/to/Grader.dmg --updater-manifest /path/to/latest.json --expected-version 0.1.0 --manual-evidence clean-machine-manual-evidence.json --evidence-root /path/to/screenshots-and-logs --out artifacts/release-evidence/clean-machine.json
uv run python scripts/release_evidence_package.py --out-dir artifacts/release-evidence/clean-machine-handoff --updater-channel-evidence artifacts/release-evidence/updater-channel-current.json
uv run python scripts/release_readiness.py --profile public_beta --expected-version 0.1.0 --public-proxy-dir artifacts/public_proxy/mohler_asag_diverse_anchored_pilot --citation-artifact artifacts/citation-results.json --canvas-artifact artifacts/release-evidence/canvas-live.json --privacy-artifact artifacts/release-evidence/privacy.json --performance-artifact artifacts/release-evidence/performance.json --frontend-artifact artifacts/release-evidence/frontend.json --rust-artifact artifacts/release-evidence/rust.json --release-version-artifact artifacts/release-evidence/release-version.json --updater-artifact artifacts/release-evidence/updater.json --updater-channel-artifact artifacts/release-evidence/updater-channel-current.json --updater-key-artifact artifacts/release-evidence/updater-key.json --clean-machine-artifact artifacts/release-evidence/clean-machine-local-current.json --out artifacts/release-readiness
```

The clean-machine manual template is fail-closed: every manual row must include `passed: true`,
tester/timestamp/notes, at least one existing evidence file, and all check-specific `assertions`
set to `true`. The Computer Use row additionally requires a `PASS` JSON from
`scripts/computer_use_sweep_evidence.py`. `scripts/release_evidence_package.py` can assemble the
signed DMG/latest.json, current evidence JSON, runbook, and manual template into one gitignored
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
keys in non-interactive release runs. `--allow-unsigned` is only for local smoke fixtures.
`scripts/release_readiness.py --expected-version ...` records artifact path/mtime/SHA-256 metadata
and fails PASS updater or clean-machine artifacts that do not match the evaluated app version.
`scripts/release_version_guard.py` fails release tags whose app/package metadata still reports a
different version. The app is still not public-beta ready until signed updater evidence and
clean-machine release behavior evidence pass. Use `scripts/updater_key_doctor.py` to verify the
updater key/password before regenerating `latest.json`, and use
`scripts/updater_channel_preflight.py` after publishing N+1 to confirm the live release channel
advertises a signed newer `Grader.dmg` before clean-machine updater testing starts.

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
