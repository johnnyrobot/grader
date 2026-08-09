# Public Beta Test And Evaluation Matrix

This matrix is the working checklist for moving Grader from developer-assisted alpha to supervised
public beta. It separates evidence tiers so synthetic fixtures can prove mechanics without being
misrepresented as grading accuracy.

## Evidence Tiers

| Tier | Purpose | May unlock public beta? | Storage |
| --- | --- | --- | --- |
| `synthetic_fixture` | UI, queue mechanics, release scripts, crash/update paths, Canvas guardrails | No | Committed only when fake and license-safe |
| `public_proxy` | Open human-scored data for QWK/fairness release evidence | Yes, for supervised public beta | Download/cache locally with manifest; do not vendor large corpora |
| `teacher_local` | Instructor-specific "grades like me" validation | Yes, for teacher-specific claims | Local only; never committed |

## End-To-End Matrix

| Area | Requirement | Evidence source | Current status | Public beta gate |
| --- | --- | --- | --- | --- |
| Onboarding | First-run explains local-first boundary, engine readiness, appearance controls, and start path | Playwright + screenshot visual sweep | PASS on synthetic fixture; visual evidence generated under `artifacts/release-evidence/visual-sweep/` | PASS mechanics; not a grading-quality gate |
| Queue | Instructor can filter lanes, refresh, change assignment, start grading, and open submissions | Playwright + synthetic instructor fixture + visual sweep | PASS on synthetic fixture; visual evidence generated under `artifacts/release-evidence/visual-sweep/` | PASS mechanics; not a grading-quality gate |
| Review | Instructor can approve unchanged AI draft, edit scores/feedback, save override, use keyboard shortcuts, and return | Playwright + keyboard-only check + visual sweep | PASS on synthetic fixture; visual evidence generated under `artifacts/release-evidence/visual-sweep/` | PASS mechanics; not a grading-quality gate |
| Settings | General, appearance, model defaults, and rubrics tabs can be opened, edited, saved, reset, and loaded | Playwright + visual sweep | PASS on synthetic fixture; all settings tabs captured in the visual sweep | PASS mechanics; not a grading-quality gate |
| Model manager | Runtime status, refresh, install buttons, installed/needed states, and inventory display work | Playwright + visual sweep | PASS on synthetic fixture; install controls captured after WCAG contrast fix | PASS mechanics; not a grading-quality gate |
| Public-proxy quality | QWK >= 0.70 or adjusted human-ceiling floor, source floor, mean-error bounds, no leakage | `scripts/build_public_proxy.py mohler-asag --grade --grade-perturbations --anchor-exemplars-per-source 40 --anchor-k 6`, then `engine/gates/quality.py --profile public_proxy --public-proxy-dir artifacts/public_proxy/mohler_asag_diverse_anchored_pilot` | PASS on local anchored 40-item Mohler ASAG artifact: 40/40 scored rows, QWK 0.845 vs adjusted floor 0.623, both source checks pass, mean error +0.10 | PASS for supervised public-proxy quality; still separate from teacher-local claims |
| Public-proxy fairness | Style perturbation deltas within thresholds | `scripts/build_public_proxy.py mohler-asag --grade --grade-perturbations --anchor-exemplars-per-source 40 --anchor-k 6`, then `engine/gates/bias.py --profile public_proxy --public-proxy-dir artifacts/public_proxy/mohler_asag_diverse_anchored_pilot` | PASS on local anchored 40-item Mohler ASAG artifact: 40/40 rows have all five perturbations, all perturbation families pass, 0 grading errors | PASS for supervised public-proxy fairness; still separate from teacher-local claims |
| Teacher-local quality | Instructor-specific QWK and override behavior | `fixtures/golden_set/` local authorized data | Missing | Not required for generic public proxy claim; required for teacher-specific claim |
| Citations | >=90% fixture accuracy, zero false positives, 3/3 retractions | `engine/verify_citations.py --output ...` | PASS_EXPERIMENTAL: 20/29 accuracy, 0 false positives, 3/3 retractions; UI labels citation checks experimental | PASS enabled mode or visibly experimental with zero false positives and all retractions caught |
| Canvas app round trip | Import -> teacher override/approval -> post -> Progress completed -> read-back exact score and teacher feedback comment | `scripts/live_canvas_e2e.py --apply` against `ZZ-GRADER-TEST` | PASS: live guarded run on `canvas.johnnyrobot.ai`, course `106`, assignment `2841`; exact score and at least one teacher comment read back. The harness now accepts either env credentials or the authenticated `canvas-pp-cli` config/credentials files. | PASS for score/comment beta scope; rubric assessment remains disabled until Canvas criterion-ID mapping exists, and attachment write-back still needs separate evidence |
| Privacy | Captured outbound connections remain allowlisted | `engine/gates/egress.py` | PASS: 3/3 allowlisted in latest run | PASS artifact must be supplied to readiness report |
| Performance | 1,500-word essay under 90 seconds on target memory floor | `engine/gates/perf.py --memory-floor 16` | PASS: 37.8 seconds in latest run | PASS artifact must be supplied to readiness report |
| Desktop build | React build, unit tests, E2E workflow, Rust check pass | `scripts/frontend_evidence.py`, `cargo check` | PASS: 18 component tests, 4 E2E tests, build, and Rust check; frontend artifact records 16 visual-sweep screens and 167 controls | PASS artifact must be supplied to readiness report |
| Accessibility | No serious/critical WCAG violations, 200% text scaling, keyboard-only loop | Playwright axe + visual inspection | PASS on synthetic fixture; visual sweep covers 16 screens and 167 visible controls | PASS mechanics; still requires clean-machine app check |
| Computer Use instructor sweep | Signed app is inspected as an instructor using Computer Use, with screenshots/accessibility-tree notes for every visible screen and safe control path | `scripts/computer_use_sweep_evidence.py`, then `scripts/clean_machine_evidence.py` manual evidence | PASS: the version-bound 73-capture signed-app sweep covers onboarding, queue, review, settings, model manager, keyboard paths, and recovery; the final updater run adds before/after Computer Use captures for the installed release. | PASS for supervised beta; rerun for every changed release bundle |
| Release version consistency | Git tag/updater version matches Tauri config, Rust crate/lockfile, frontend package/lockfile, Python project, engine launcher, and dev mock update fixture | `scripts/bump_release_version.py --target-version <N+1>` for release prep, `scripts/release_version_guard.py --tag v<version>`, and GitHub release workflow | NEW: fail-closed guard now covers package, lockfile, and runtime update-reporting surfaces before release asset publishing | Required before signed updater/release-channel evidence |
| GitHub release publication preflight | Private source repo, public assets repo, updater endpoint, release-source checks, candidate version, and optional N+1 collision checks are ready | `scripts/bump_release_version.py`, then `scripts/github_release_preflight.py --candidate-tag v<N+1>` | PASS: `johnnyrobot/grader-releases` is public and readable; Apple signing stays local and no `.p12`/Apple password is required in GitHub | Required before publishing N+1 assets |
| Updater evidence | Signed updater manifest references the signed artifact and current version, passes macOS signature/notarization checks, and is fresh relative to the current source commit | `scripts/updater_key_doctor.py`, `scripts/generate_appcast.py --signing-key ...`, then `scripts/updater_evidence.py` | PASS: rotated updater key/Keychain probe pass; extracted `Grader.app.tar.gz` passes codesign and spctl and matches 0.1.1 | Required before updater path |
| Updater channel preflight | The live release channel advertises a signed N+1 `Grader.app.tar.gz` from `github.com/johnnyrobot/grader-releases` before clean-machine testing starts | `scripts/publish_local_release.py --apply`, then `scripts/updater_channel_preflight.py --current-version <installed-N-version>` | PASS: public 0.1.1 manifest and updater bundle are reachable and hashes match | Required before updater path; not a substitute for in-app evidence |
| Signing/notarization | Signed/notarized app or DMG passes codesign, stapler, spctl | `scripts/signing_doctor.py`, release script, clean Mac | PASS locally: Developer ID private-key access and stored `grader` notarytool profile validate | Clean-machine behavior remains separate |
| Clean-machine | First launch, model setup, crash recovery, keyboard review, Computer Use instructor sweep, updater N->N+1 | `scripts/clean_machine_evidence.py --print-manual-runbook`, structured evidence, and clean GitHub macOS build | PASS under the hybrid supervised-beta standard: local fresh-profile behavior and signed 0.1.0 → 0.1.1 updater path pass; final source separately clean-builds on a fresh GitHub-hosted macOS runner. This is not represented as a separate physical clean Mac. | PASS for supervised beta; physical clean-Mac evidence remains the stronger unattended-release standard |
| Release report | Consolidated verdict is `supervised_public_beta` | `scripts/release_readiness.py` | PASS: 19/19 rows pass, including one explicit `PASS_EXPERIMENTAL` citation row | Final release gate |

This is the `supervised_public_beta` profile of `scripts/release_readiness.py`, and it is the one the
verdict is decided on. The report also emits a `ship_v1` profile — the same rows plus what only a
beta can produce, currently the `Draft bias` row from ADR-0001. Those rows block v1.0 and never the
beta; see `docs/BETA_ROLLOUT.md`.

## Required Manual Visual Sweep

Use Computer Use or Playwright screenshots to inspect every visible screen at desktop width and at
a narrow responsive width. The current Playwright visual sweep writes
`artifacts/release-evidence/visual-sweep.json`, screenshots for 16 named screens, and a control
inventory covering 167 visible controls:

1. Onboarding: intro, appearance, machine check, connected state.
2. Queue: all lane filters, assignment field, empty state, populated table, update notice.
3. Review: unchanged approval, score changes, feedback editing, unsaved warning, approval result.
4. Settings: general, appearance, model defaults, rubrics, validation messages.
5. Model manager: runtime online/offline, installed/needed models, refresh, install, inventory.
6. Recovery: local-engine disconnect alert and restart action.

Record each sweep as `PASS`, `FAIL`, or `BLOCKED`, with screenshot paths or a Computer Use note.
The latest Playwright sweep is `PASS`. The latest local signed-app Computer Use sweep is also `PASS`:
`artifacts/release-evidence/computer-use-signed-app/` contains 73 accessibility/screenshot capture
pairs covering onboarding, queue, filters, review, settings, local models, signed-app keyboard-only
review, and approval states. `scripts/computer_use_sweep_evidence.py --app ... --expected-version ...`
now validates that capture set with all required markers, no stale approved/unsaved state, and the
specific `.app` bundle/version that the evidence is being attached to. A newer single-screen smoke
capture in `artifacts/release-evidence/computer-use-current-smoke/` confirms Computer Use can open
the rebuilt 0.1.1 signed app, while preserving the warning that the full 73-screen sweep should be
rerun on the final release bundle.
For clean-machine evidence, each manual check must also set all check-specific `assertions` to
`true`; generic screenshots or notes alone no longer satisfy the public-beta gate.

## Current Verdict

Ready for a supervised public beta under the two-tier evidence standard. The anchored real-data
public-proxy quality/fairness, Canvas score/comment round-trip, signed release channel, and native
0.1.0 → 0.1.1 updater path pass; citations are acceptable only in visibly experimental beta mode.
The consolidated report has 19/19 passing rows. For each future release, run
`scripts/bump_release_version.py --target-version <N+1> --apply` in the release-prep commit, run
`scripts/github_release_preflight.py --candidate-tag v<N+1>` before creating/pushing N+1, then run
`scripts/publish_local_release.py --apply` after local signing/updater evidence and
`scripts/updater_channel_preflight.py --current-version <installed-N-version>` after publishing N+1
so version drift, missing or malformed GitHub release-channel state, and missing release assets are
caught before the clean-Mac test begins.
Generate `clean-machine-runbook.md` with
`scripts/clean_machine_evidence.py --print-manual-runbook` before
clean-Mac testing. `scripts/release_evidence_package.py` can assemble the signed DMG/updater bundle/latest.json,
current evidence JSON, runbook, and manual template into one clean-machine tester handoff folder, but
the package is not itself passing evidence. Canvas rubric-assessment
posting is intentionally disabled until real Canvas criterion IDs can be mapped, and attachment
write-back still needs separate evidence.
