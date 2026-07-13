# Public Beta Release Gates Design

## Goal

Make Grader's public beta decision evidence-based, FERPA-safe, and repeatable. The app may move
from developer-assisted alpha to supervised public beta only when local release gates pass and the
release report clearly states what was validated, what was not validated, and what is still
experimental.

## Release Evidence Tiers

The product uses three explicit evidence tiers:

1. `public_proxy`: Open, human-scored, redistributable or locally cached datasets used to validate
   grading machinery without private student records. Passing this tier can support a supervised
   public beta claim.
2. `teacher_local`: Instructor-owned historical grading used to validate "grades like this
   teacher." This tier remains unvalidated until an instructor supplies authorized local data.
3. `synthetic_fixture`: Synthetic examples used only for mechanics, regression, UI, Canvas sandbox,
   and crash/update flows. Synthetic data is never used as accuracy evidence.

No release report may collapse these tiers. Public beta copy must say "validated on open
human-scored public proxy sets" until a teacher-local set passes.

## Public Quality And Fairness Gate

### Data Sources

The public proxy corpus is downloaded into a gitignored local cache, not committed. Each imported
source writes a manifest with source URL, license text or license identifier, retrieval date,
adapter version, file checksums, number of examples, scoring scale, rubric shape, and any exclusions.

Primary sources:

- Mendeley Data `sf3kvjwknt/1`: university Data Science exam answers, answer key, teacher-corrected
  PDFs, item scores, anonymized/redacted, CC BY 4.0, published 2026-04-14.
- Hugging Face dataset `nkazi/MohlerASAG`: Mohler short-answer grading data, CC BY 4.0, with
  independent human grades suitable for human-ceiling checks.

Optional sources may be added only when the importer verifies their license and stores provenance in
the manifest. Non-commercial corpora such as PERSUADE 2.0, ELLIPSE, AAC&U VALUE, and IAM may be
used locally for research but must not be committed, bundled, or required for public beta.

### Normalized Example Contract

Adapters normalize each public source into JSONL examples under a gitignored cache directory:

```json
{
  "id": "source:stable-id",
  "tier": "public_proxy",
  "source": "mendeley_sf3kvjwknt_v1",
  "license": "CC BY 4.0",
  "prompt_id": "exam-item-3",
  "assignment_type": "short_answer",
  "student_text": "answer text extracted locally",
  "rubric": {
    "title": "Data Science Exam Item 3",
    "criteria": [
      {"id": "content", "description": "Correctness against answer key", "max_points": 5}
    ]
  },
  "human_scores": {"content": 4.0},
  "human_raters": [{"id": "teacher", "scores": {"content": 4.0}}],
  "split": "test"
}
```

Adapters must redact direct identifiers if present, must reject examples without human scores, and
must preserve enough rubric/answer-key context for the grading prompt to be meaningful. Original
downloaded files remain in the local cache; only manifests, schemas, and tests are committed.

### Splits And Leakage Controls

The public proxy gate uses deterministic splits by source, prompt, and student/example id. An
example cannot appear as both an exemplar and a test case. Prompt-level leakage is reported in the
manifest. When a source is too small for a strict prompt-holdout split, the release report must say
so and mark the source as a limited proxy rather than silently pooling it.

### Quality Pass Criteria

The gate computes QWK on total score and, where the source supports it, per-criterion QWK. It also
reports exact agreement, adjacent agreement, signed mean error, item count, assignment type count,
source breakdown, and human ceiling when more than one human rater exists.

Public proxy quality passes only when all of these are true:

- Aggregate public proxy QWK is at least `0.70`, or at least `95%` of the measured human ceiling
  when the human ceiling is below `0.70`.
- No primary source with at least `20` scored test examples is below `0.60` QWK.
- Signed mean error is between `-0.50` and `+0.50` points on the normalized 0-10 scale.
- At least two assignment/source families are represented, or the release report marks the beta as
  limited to the represented task family.
- Regression from the saved public proxy baseline is not worse than `0.03` QWK.

If public proxy quality fails, the app remains alpha. If public proxy passes but teacher-local is
missing, the app can be supervised public beta but must not claim teacher-specific calibration.

### Fairness Pass Criteria

The fairness gate measures score stability under controlled style perturbations, not demographic
population parity. Perturbation families are informal tone, formal tone, non-native phrasing,
verbosity, concision, spelling/grammar noise that preserves meaning, and formatting changes. Each
perturbation must keep content constant.

Fairness passes only when all of these are true:

- Mean absolute score delta for each perturbation family is at most `1.0` point on the normalized
  0-10 scale.
- No source-family mean delta exceeds `1.25`.
- Directional signed deltas are reported, especially AI-harsh behavior for non-native phrasing,
  informal phrasing, and mechanical errors.
- At least `90%` of perturbed examples remain within one rubric score band of the original AI score.
- If any fairness source is synthetic, the report labels it `synthetic_fixture` and does not describe
  it as population fairness evidence.

## Citation Verification Gate

Citation verification is release-critical only if the beta exposes it as a normal confidence signal.
The beta may ship with citations enabled or disabled, but it may not ship with ambiguous behavior.

### Enabled Mode

Enabled citation verification passes only when `uv run python engine/verify_citations.py --fixtures
fixtures/citations/citation_fixtures.json --output <artifact>` reports:

- At least `90%` fixture accuracy.
- Zero false positives where a real citation is called fabricated.
- `3/3` retraction fixtures caught.
- A JSON artifact with per-fixture verdicts, flags, confidence, resolution layer, and error.

The UI and API may show citation checks as normal only after the enabled-mode gate passes.

### Experimental/Disabled Mode

If the enabled-mode gate fails, the release must take one of these explicit paths:

- Disable citation verification in the default beta settings and hide confidence-affecting citation
  badges.
- Or show citation verification behind an "Experimental" label, exclude it from grade confidence,
  and display conservative copy: "Citation checks are experimental and require teacher review."

In both paths, `citation_issues` may route a submission to review but must not lower or approve a
grade automatically.

## Live Canvas App Round Trip

The live Canvas gate validates the app workflow, not only the helper script.

### Safety Boundary

The only permitted live course name prefix is `ZZ-GRADER-TEST`. Live writes require `--apply` or an
equivalent explicit opt-in, and no tool may post to an unmarked course. Logs must redact tokens,
base URLs with embedded credentials, student names, emails, and submission text.

### Required Flow

The app-level test must perform the complete path:

1. Import submissions from Canvas through the app or authenticated app API.
2. Create local queue records for imported submissions.
3. Draft grades locally.
4. Require teacher approval in the data layer.
5. Post approved grades to Canvas.
6. Poll the Canvas Progress object until it reaches `completed`.
7. Read the Canvas submission back and verify posted score, rubric assessment, teacher-visible
   comment, and any annotation attachment expected by the test.

The test fails if any grade posts before approval, if Canvas progress is not complete, if the
read-back value differs from the approved local value, or if the app reports success after a partial
failure.

## Clean-Machine macOS Release Gate

The packaging gate validates a real signed/notarized app experience on a clean supported Mac or
clean macOS VM.

### Build And Signing

The release script must preflight the Apple Developer ID certificate, notary credentials, Tauri
updater signing key, bundle id, version, hardened runtime, and entitlements without printing secret
values. A release artifact passes only after:

- `codesign --verify --deep --strict` succeeds.
- `xcrun stapler validate` succeeds for the built app or DMG.
- `spctl --assess --type execute` accepts the app.
- The generated updater manifest references the signed artifact and matching version.

If credentials are unavailable, the release report records `BLOCKED_SIGNING_CREDENTIALS` rather than
simulating a pass.

### Clean-Machine Test Matrix

The clean-machine test must cover:

- First launch with no existing Grader data directory.
- First-run model detection and install guidance for `grader-text:8b` and `grader-vision:1.3b`.
- Engine crash recovery: kill the engine process, confirm the app detects it, restarts it, and
  preserves the queue/audit state.
- Keyboard-only review: navigate queue, open a submission, edit score/comment, approve, and return
  without a pointer.
- Updater path from version `N` to `N+1`, including signature verification and retained settings.
- No original student file mutation; annotated outputs are written as copies.

Failures in this matrix block public beta unless the affected feature is disabled and the release
report names the limitation.

## Consolidated Release Report

The release command writes both machine-readable JSON and human-readable Markdown. The report
contains:

- Git commit, dirty tree summary, app version, engine version, macOS version, hardware, Python,
  Node, Rust, Ollama, and model tags.
- Quality results by tier/source and aggregate.
- Fairness results by perturbation/source.
- Citation mode and citation gate artifact.
- Canvas course id, assignment id, anonymized test student ids, post count, read-back checks, and
  teardown status.
- Signing/notarization/updater checks.
- Clean-machine matrix results.
- Final verdict: `alpha`, `supervised_public_beta`, or `blocked`.

The report declares `supervised_public_beta` only when public proxy quality, fairness, citation mode,
Canvas app round trip, privacy/performance gates, frontend/Rust/Python tests, signing/notarization,
and clean-machine matrix all pass or are explicitly disabled with honest product labeling.

## Implementation Boundaries

- Use `uv run` for Python commands.
- Do not commit downloaded datasets, generated golden sets, private Canvas exports, model weights,
  credentials, app bundles, DMGs, notarization tickets, or clean-machine logs with secrets.
- Keep Canvas logic in Python, frontend engine calls in `app/src/api.ts`, and release gate
  orchestration in `engine/gates/` or `scripts/`.
- Keep teacher approval enforced in the data layer before Canvas posting.
- Keep original files immutable.
- Do not change grading thresholds without updating `RESEARCH.md` or a phase document with evidence.

## Test Strategy

Unit tests cover adapters, manifests, split determinism, QWK and fairness aggregation, citation-mode
labeling, release-report verdict selection, and secret redaction. Integration tests cover public
proxy gate runs on tiny committed fixtures, mocked Canvas import/approve/post/read-back behavior,
and packaging preflight without real credentials. Live tests are opt-in and guarded by the
`ZZ-GRADER-TEST` boundary.

The final verification suite for a public beta candidate is:

```bash
uv run pytest engine/tests/ -v
uv run python engine/gates/quality.py --profile public_proxy
uv run python engine/gates/bias.py --profile public_proxy
uv run python engine/gates/egress.py --capture-duration 15
uv run python engine/gates/perf.py --memory-floor 16
uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json --output artifacts/citation-results.json
cd app && npm run build
cd app && npm test -- --run
cd app/src-tauri && cargo check
uv run python scripts/live_canvas_e2e.py --course-prefix ZZ-GRADER-TEST --apply
uv run python scripts/release_readiness.py --profile public_beta --out artifacts/release-readiness
```

The live Canvas, signing/notarization, and clean-machine commands may return blocked statuses when
credentials or clean hardware are absent. Blocked is acceptable for local development, but blocked is
not a public beta pass.
