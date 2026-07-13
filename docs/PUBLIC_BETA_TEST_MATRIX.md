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
| Canvas app round trip | Import -> teacher override/approval -> post -> Progress completed -> read-back exact score and teacher feedback comment | `scripts/live_canvas_e2e.py --apply` against `ZZ-GRADER-TEST` | PASS: live guarded run on `canvas.johnnyrobot.ai`, course `106`, assignment `2841`, Canvas user `9`; score `2` and one teacher comment read back | PASS for score/comment beta scope; rubric assessment remains disabled until Canvas criterion-ID mapping exists, and attachment write-back still needs separate evidence |
| Privacy | Captured outbound connections remain allowlisted | `engine/gates/egress.py` | PASS: 3/3 allowlisted in latest run | PASS artifact must be supplied to readiness report |
| Performance | 1,500-word essay under 90 seconds on target memory floor | `engine/gates/perf.py --memory-floor 16` | PASS: 37.8 seconds in latest run | PASS artifact must be supplied to readiness report |
| Desktop build | React build, unit tests, E2E workflow, Rust check pass | `scripts/frontend_evidence.py`, `cargo check` | PASS: 12 component tests, 4 E2E tests, build, and Rust check; frontend artifact records 16 visual-sweep screens and 167 controls | PASS artifact must be supplied to readiness report |
| Accessibility | No serious/critical WCAG violations, 200% text scaling, keyboard-only loop | Playwright axe + visual inspection | PASS on synthetic fixture; visual sweep covers 16 screens and 167 visible controls | PASS mechanics; still requires clean-machine app check |
| Computer Use instructor sweep | Signed app is inspected as an instructor using Computer Use, with screenshots/accessibility-tree notes for every visible screen and safe control path | `scripts/clean_machine_evidence.py --print-manual-runbook`, then `computer_use_instructor_sweep` manual evidence | BLOCKED: no signed-app Computer Use sweep evidence has been supplied yet | Required for public beta; complements, not replaces, Playwright |
| Updater evidence | Signed updater manifest references the signed artifact and current version, and the local artifact passes macOS signature/notarization checks | `scripts/generate_appcast.py --signing-key ...`, then `scripts/updater_evidence.py` | PASS: signed `latest.json` references `Grader.dmg`; updater evidence records artifact SHA-256, stapler/spctl checks, and mounted bundled-app codesign/spctl checks | Required before clean-machine updater path |
| Signing/notarization | Signed/notarized app or DMG passes codesign, stapler, spctl | `scripts/signing_doctor.py`, release script, clean Mac | BLOCKED: fresh local signing fails with `errSecInternalComponent`, and notarytool profile `grader` is unavailable; `sign_and_notarize.sh` now runs the doctor before mutating the app bundle | PASS automated signing/notarization evidence; clean-machine behavior remains separate |
| Clean-machine | First launch, model setup, crash recovery, keyboard review, Computer Use instructor sweep, updater N->N+1 | `scripts/clean_machine_evidence.py --print-manual-runbook`, then JSON from a clean Mac/VM with structured manual evidence notes, files, and release artifact hashes | BLOCKED: current local evidence records release provenance, DMG stapler, and mounted-DMG app codesign/spctl, but still misses first-run models, crash recovery, keyboard-only review, Computer Use instructor sweep, and updater path; clean-machine manual evidence now requires tester, timestamp, notes, existing screenshot/log paths, and exact app/DMG/latest.json provenance | Required for public beta |
| Release report | Consolidated verdict is `supervised_public_beta` | `scripts/release_readiness.py` | BLOCKED: signing credentials and clean-machine evidence are incomplete | Final release gate |

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
The latest Playwright sweep is `PASS`; the clean-machine signed app still needs a separate
`computer_use_instructor_sweep` entry generated from Computer Use against the packaged app.

## Current Verdict

Not public beta ready. The current anchored real-data public-proxy quality and fairness runs pass on
Mohler ASAG, Canvas app-level score/comment round-trip passes, and citations are acceptable only in
visibly experimental beta mode. Static signed-updater evidence passes, including local DMG and bundled
app signature checks. The remaining public-beta blockers are fresh signing/notarization credentials
plus clean-machine behavioral evidence: first-run model setup, crash recovery, keyboard-only review,
Computer Use instructor sweep, and updater N→N+1. Run
`uv run python scripts/signing_doctor.py --out artifacts/release-evidence/signing.json --no-fail`
to refresh the local signing diagnostic artifact before a release attempt, and generate
`clean-machine-runbook.md` with `scripts/clean_machine_evidence.py --print-manual-runbook` before
clean-Mac testing. Canvas rubric-assessment
posting is intentionally disabled until real Canvas criterion IDs can be mapped, and attachment
write-back still needs separate evidence.
