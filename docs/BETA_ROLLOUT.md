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
| Any showstopper bug | | ✓ | |

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
