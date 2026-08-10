# Beta Rollout Guide

## Phases

### Private Alpha (now)
- Dev + 1 instructor
- Local files only (no Canvas)
- Goal: find showstopper bugs

### Private Beta (next)
- 3–5 instructors
- Real Canvas courses
- Weekly feedback cycle
- Goal: prove the calibration bet + surface edge cases
- Entry gate: `scripts/release_readiness.py` must report `supervised_public_beta`; current builds
  remain held while updater-channel and clean-machine updater-path evidence are blocked.

### Public v1.0
- GitHub release, signed + notarized DMG
- Calibration write-up published
- Homebrew cask

---

## Pre-Flight Checklist (before each beta tester)

- [ ] Ollama installed and running on their machine
- [ ] Models pulled: `grader-text:8b`, `nomic-embed-text`
- [ ] `./scripts/make_models.sh` run (context-capped variants)
- [ ] Grader.app launches without Gatekeeper warning
- [ ] Local Developer ID signing, stored `notarytool` profile, and updater Keychain probe pass;
      no Apple certificate export or Apple password is stored in GitHub
- [ ] N+1 version metadata is prepared in the release-prep commit:
      `uv run python scripts/bump_release_version.py --target-version <N+1> --apply`
- [ ] GitHub release publication preflight passes before tagging/publishing N+1:
      `uv run python scripts/github_release_preflight.py --candidate-tag v<N+1>`
- [ ] Locally signed assets publish through `scripts/publish_local_release.py --apply`
- [ ] Release channel preflight passes:
      `uv run python scripts/updater_channel_preflight.py --current-version <installed-version>`
- [ ] Clean-machine updater path evidence exists for in-app N→N+1 update and restart
- [ ] First-run wizard completes (token set, rubric pasted)
- [ ] One test submission graded end-to-end
- [ ] Teacher approves a grade → confirms it saved
- [ ] Egress gate run on their network (no stray connections)

## Tester Onboarding Script

```
1. Install Ollama:           brew install ollama
2. Pull models:              ollama pull qwen3:8b && ollama pull minicpm-v4.6:latest
3. Bake capped variants:     ./scripts/make_models.sh
4. Launch Grader.app
5. Complete first-run wizard (paste Canvas token + rubric)
6. Import one assignment from Canvas
7. Grade a batch of 5 submissions
8. Review the triage lanes — do the "review first" items make sense?
9. Approve 3 grades, override 1, leave 1 as draft
10. Check the feedback report export (.docx)
```

## Weekly Feedback Form

Each tester fills this out weekly during the beta:

### Identity
- Name:
- Institution:
- Course level: (intro / upper-division / grad)
- Assignment types used: (essay / problem_set / lab_report / code / handwritten)

### Usage
- Submissions graded this week:
- Time spent grading (with Grader):
- Time spent grading (without Grader, estimated):
- Approval rate (% of AI drafts you approved without changes):
- Override rate (% you changed the score):
- Did you use the feedback export? (yes / no — why?)

### Quality
- Rate the AI draft quality (1-5):
- Were the "review first" lanes accurate? (yes / no — examples):
- Any grades you strongly disagreed with? (describe):
- Did citation verification catch real issues?

### Friction
- Most annoying thing about using Grader:
- Something you wished it did that it doesn't:
- Would you use this for a full semester? (yes / no — why):

### Privacy
- Did you notice any unexpected network activity? (check Activity Monitor):
- Are you comfortable with the data flow? (local only — Canvas + model + DOI lookups):

---

## Beta Release Notes Template

```
## Grader Beta v0.X.0

### What's new
- [feature/fix]

### Known issues
- [list]

### How to update
1. Open Grader.
2. When the update banner appears, choose **Install update and restart**.
3. Confirm the relaunched app shows the new version.
4. If the in-app updater is unavailable, stop the rollout and collect updater evidence before
   instructing testers to replace the app manually.

### Calibration status
- QWK against golden set: X.XXX (target: ≥0.70)
- Human ceiling: 0.859
- Style bias delta: ±X.XX (target: ≤1.0)
```

## Rollout Decision Matrix

| Signal | Continue beta | Hold | Ship v1.0 |
|--------|--------------|------|----------|
| QWK ≥ 0.70 | ✓ | | ✓ |
| Egress gate clean | ✓ | | ✓ |
| No FERPA reports | ✓ | | ✓ |
| Testers approve ≥60% of drafts | | | ✓ |
| Testers would use full semester (≥3/5) | | | ✓ |
| Style bias ≤ 1.0 | ✓ | | ✓ |
| `Draft bias` measured on real `Approval`s (n ≥ 20) and surfaced in the app | | | ✓ |
| Any showstopper bug | | ✓ | |

## Where the matrix actually stands (2026-08-08)

`release_readiness.py` returns `supervised_public_beta`, 19/19, on fenced evidence. **That is the
Private Beta *entry* gate above, not a ship signal.** Read against the Ship-v1.0 column:

| Signal | Status |
| --- | --- |
| QWK ≥ 0.70 | ✅ 0.765 over 120 items, 6 questions |
| Egress gate clean | ✅ |
| No FERPA reports | ✅ |
| Style bias ≤ 1.0 | ✅ all five perturbations; `nonnative` −0.14 is the weakest |
| Testers approve ≥60% of drafts | ❌ **not measurable — no testers yet** |
| Testers would use a full semester (≥3/5) | ❌ **not measurable — no testers yet** |
| `Draft bias` measured on real `Approval`s (n ≥ 20) and surfaced in the app | ⚠️ surfaced; **not measurable — no teacher `Approval`s yet** |
| Any showstopper bug | ⚠️ see below |

Four of the eight cannot be evaluated without the Private Beta. The gate passing is the signal to
*start* that phase.

### The risk this matrix now has a row for

The product's safety story is that the teacher approves every grade. Three measurements strain it
(evidence in `HANDOFF.md` §1e–§1g):

- **Systematic under-scoring** — −0.33 overall, and −0.68 on four of the six questions.
- **4.8% of grades are confidently wrong** — |error| ≥ 2 at confidence ≥ 0.9, carrying no flag.
- **Anchors, the core calibration bet, are a coin flip on at least one question shape.** On q05 they
  move 9 of 20 scores: 4 closer to the human, 4 further. `E04.Q05.A00` scores 3.0 un-anchored
  against a human 2.5, and **0.0 at confidence 0.95 once anchored**.

A draft that is reliably *low* does not fail safely — it anchors the reviewer low. "The teacher
decides" is a weaker mitigation against a biased draft than against a random one, because the
failure mode is automation bias rather than an obvious error. These are real students' grades.

**This is a reason to run the supervised Private Beta, not a reason to hold it** — measuring
approval rate on real drafts is exactly how the question gets answered. It *is* a reason not to
ship v1.0 on the strength of a passing gate.

So it is a Ship-v1.0 row and not a beta-entry one (ADR-0001, decision 11). `release_readiness.py`
carries it as `draft-bias-teacher-approvals` under the `ship_v1` profile, which is a superset of
the `supervised_public_beta` profile the verdict is decided on: the row blocks v1.0 and cannot move
the beta verdict. It is satisfied by teacher-local calibration evidence
(`artifacts/release-evidence/draft-bias-teacher-local.json`, `evidence_tier: teacher_local`,
`n ≥ 20`, a non-null `draft_bias`) *together with* the app surface, which the frontend gate's
calibration screen and review-screen bias line already carry. Measured but unsurfaced does not
pass — a bias nobody is shown still anchors the reviewer low.

### Also outstanding

- Citations remain experimental (69% fixture accuracy, 0 false positives) and must stay visibly
  labelled.
- FR-3.4 comment attachments now have live evidence. `scripts/live_canvas_e2e.py --apply` uploads
  a feedback file with a graded submission and reads it back off Canvas — on that student's
  submission comment, and absent from `/courses/:c/files`, which is the shared-course-content
  defect the endpoint choice exists to avoid. It also fails the upload on purpose and checks that
  FR-3.5 queues the post with the file still to send. Both passed on course `106`, assignment
  `2841`; see `artifacts/release-evidence/canvas-live-current.json`.
- The visual sweep no longer overstates coverage: it covers 17 screens including `15-calibration`
  and 186 visible controls, and `release_readiness.py` now floors the check at `len()` of the lists
  `frontend_evidence.py` validates against, so the floor cannot drift from them again. One caveat
  remains — `artifacts/release-evidence/frontend.json` was collected before the last end-to-end
  change, so rerun `scripts/frontend_evidence.py` before treating it as final release evidence.
- Performance: the retry path is now measured rather than feared. `engine/gates/perf.py` times a
  capped 2048-token attempt — the first half of a retried grade — and reports the two-attempt worst
  case alongside the happy path: **28.8s single, 67.3s worst case, both inside the 90s budget**. The
  earlier 100.7s observation stands as a record of slower conditions (a single grade then took 77s,
  against 28.8s now), not of a budget the retry breaks. The worst case is reported, not gated:
  whether a retried item must fit the single-item budget is a product decision the gate does not
  make, and a `PASS` now says so out loud when it would not fit.

## Post-Launch

1. Publish calibration write-up (real QWK vs human ceiling) — the credibility artifact
2. Submit to Homebrew cask
3. Open GitHub Issues for backlog items
4. Start planning v1.1 from beta feedback

## Emergency Stop

If a FERPA or data-leak issue is discovered:

1. Tell all testers to stop using Grader immediately
2. File a `privacy_violation` issue (template exists)
3. Audit the egress gate logs
4. Patch + re-sign + re-notarize + re-release
5. Notify all testers with the fix
