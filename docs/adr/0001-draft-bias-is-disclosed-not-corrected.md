---
status: accepted
date: 2026-08-08
---

# Draft bias is disclosed, not corrected

The grading model under-scores systematically (−0.33 overall, −0.68 on four of six questions
on the public proxy), and five interventions aimed at fixing it have now been rejected on
measurement: the anchor precedence rule, the decide-once rule, sharpened rubric descriptors,
half-points, and — on q05 — anchors themselves. Everything that has worked on this model has
been mechanical: a cap, a retry, a sampling change.

We are therefore treating the bias as a **measured, disclosed property of the calibration
relationship** rather than a defect to correct in the drafting path. Grader measures
`Draft bias` per Assignment, shows it to the teacher, and uses it to withdraw a misleading
confidence cue. It never adjusts a drafted score.

This is not a fix. It is the recognition that a reliably low draft does not fail safely — it
anchors the reviewer low, which is automation bias rather than a visible error — and that the
product's obligation is to make that visible.

## The decisions

1. **`Draft bias` is assignment-level, not per-draft.** The evidence locates the bias only in
   aggregate; a per-item "this one is probably low" signal would be a number we cannot compute.
2. **It is measured over every `Approval`, including zero-delta ones.** See *The measurement
   defect* below.
3. **Grain:** the assignment-level figure drives routing; per-criterion disagreement appears in
   the calibration health panel.
4. **A harsh Assignment demotes `HIGH_CONFIDENCE` to `STANDARD`.** Scores are never adjusted.
   This mirrors the existing floor for scanned work in `triage.py` — a floor, not a bypass.
5. **The review screen carries one assignment-level line** where the score is actually read,
   not only in a panel the teacher may never open.
6. **A floor of `n ≥ 20` gates both the display and the demotion.** Below it the panel reports
   counts and no direction.
7. **Thresholds are fractions of criterion max, symmetric in both directions:** disclose at
   `0.03`, demote at `0.10`. Absolute point thresholds mean two different things on a 5-point
   criterion and a 100-point assignment.
8. **An `Approval` never becomes an `Exemplar`.** See *Approvals are evidence about the model*
   below.
9. **QWK is removed from the in-app panel** and remains a release-gate metric only.
10. **`Thresholds.auto_approve` is renamed `high_confidence`**, with back-compat on the incoming
    settings payload.
11. **The Ship-v1.0 matrix gains a row**, not the beta-entry gate: v1.0 requires `Draft bias`
    measured over real teacher approvals at `n ≥ 20` and surfaced in the app.

## The measurement defect this fixes

`compute_health` computed QWK, `mean_error` and per-criterion delta over `override_store.list(...)`
alone — items the teacher *changed*. An approval with no override was never a data point.

That inverts the metric under exactly the failure it needs to catch: a teacher who has begun
rubber-stamping low drafts generates fewer overrides, so the panel reads *better* as automation
bias gets worse. The population was the whole substance of the term, which is why `Calibration
health` in `CONTEXT.md` now names it.

**The zero-delta data point already exists.** `grade_records` carries `ai_scores_json`,
`ai_confidence` and `ai_flags_json`, written when the grade is drafted, alongside
`teacher_scores_json` and `approved_at`. An approval with no override is already a complete,
recorded comparison — nothing new needs capturing at approval time, and `approve()` does not
widen. The narrowness its docstring defends is preserved.

The cost relocates rather than disappearing: `compute_health` must read approved grade records
instead of the override store, which means a read seam onto that table, and it must resolve
each record's Rubric — by `rubric_title` and `rubric_version` — to scale bias against criterion
max. That is the same resolution `override_grade` already performs, including its fallback for
records written before `rubric_title` existed.

## Approvals are evidence *about* the model, not *for* it

An unchanged approval enters the measurement and never becomes an anchor. Its scores and
feedback are AI-authored; retrieving them as exemplars would be a self-confirming loop that
makes `Draft bias` progressively invisible, since the anchors would encode the very harshness
being reported. §1g already records a case where anchors *caused* a confident error
(`E04.Q05.A00`: 3.0 against a human 2.5 bare, 0.0 at confidence 0.95 anchored).

The same event therefore does two different jobs, and only one of them touches the exemplar
store.

## Considered and rejected

- **Adjusting drafted scores to compensate.** It corrupts the exemplar loop — the teacher's
  approval of a shifted number becomes an anchor for a bias we injected — and hides the defect
  instead of surfacing it.
- **A per-draft bias signal.** No mechanism computes it. The bias is a collapse onto 3, and
  nothing in a single draft says which threes are wrong.
- **Conditioning on the draft's own score** (the model emits 31 threes against 15 human).
  This is the option that best matches the evidence, and it is deferred rather than dismissed —
  see *Revisit when*.
- **Asymmetric thresholds** (harsh trips earlier than lenient). A lenient AI that gets
  rubber-stamped inflates grades, which fails the student in the other direction and is harder
  to defend to a department.
- **Recording zero-delta approvals as rows in the `overrides` table.** It has exactly the right
  columns and would be the shortest path, but an `Override` is *a teacher's correction* — filling
  the table with non-corrections would corrupt `override_rate` and break the term.
- **Keeping QWK in the panel.** It rounds both score vectors to integers, and §1g measured 39
  of 120 human scores as half-points — so it discards teacher precision before computing. It
  would also invite comparison against the release gate's differently-populated QWK.
- **A blocking row on the beta-entry gate.** That gate has already been read as passing, and
  this risk was explicitly settled as not a reason to hold the Private Beta.

## Consequences

- **The demotion will not fire on the evidence we have.** −0.33 on a 5-point scale is 0.066 of
  max: it discloses, it does not demote. Only the per-question −0.68 level (0.136) clears the
  demote threshold. This is intended — the assignment-level mean blends a broken mid-band with
  a bottom band the model grades correctly, and restructuring a queue on that blend looks
  responsible without being so.
- **A teacher's first assignment likely shows no direction** until it is nearly finished.
- **Disclosure may induce the opposite bias** — uniform upward nudging, including on the bottom
  band where the model is unbiased (+0.02). This is the strongest argument against showing the
  figure at all, and it is not measurable before beta.
- **A known inconsistency this surfaces:** `triage.py` defaults `HIGH_CONFIDENCE = 0.85` while
  `settings.py` defaults `auto_approve_threshold = 0.95`. Whichever path a caller takes decides
  which applies. The rename in decision 10 touches both files; the discrepancy is recorded here
  and deliberately not resolved by this ADR.

## Revisit when

- **Real teacher approvals exist.** Score-conditioned bias becomes viable once each score
  bucket can clear the `n ≥ 20` floor. It is currently blocked less by data volume than by
  provenance: the mid-band collapse was measured against a rubric we wrote
  (`scripts/build_public_proxy.py`), not a teacher's, and shipping routing built on it would
  encode an artifact of our own rubric into every teacher's queue.
- **Beta shows whether the disclosure line changes teacher behaviour**, and in which direction.
