# Rubric Authoring Guide

A good rubric is the single most important factor in AI grading quality. This guide
helps you write rubrics that the AI can apply reliably and that produce grades you trust.

## The three things that matter most

### 1. 3-5 anchored levels per criterion

Research (arXiv 2601.03444) finds that 3-5 anchored levels on a 0-5 scale produce the
strongest LLM-human agreement. 0-10 scales are consistently the weakest — the model
can't distinguish between a 7 and an 8 the way a human can.

**Good:**
```
## Evidence (5 pts, analytic)
- 5: Excellent — cites multiple relevant sources with correct attribution and integration
- 4: Good — cites relevant sources but integration is surface-level
- 3: Adequate — cites at least one source but lacks integration or analysis
- 2: Limited — cites sources but with errors or irrelevance
- 1: Insufficient — mentions sources but does not cite properly
- 0: Missing — no sources cited
```

**Bad:**
```
## Evidence (10 pts)
Student uses evidence effectively.
```

### 2. Descriptors that describe, not judge

Each level descriptor should describe what the work *looks like*, not just rate it.
"Excellent" tells the AI nothing; "cites multiple relevant sources with correct
attribution and integration" tells the AI exactly what to look for.

**Good:** "cites at least one source but lacks integration or analysis"
**Bad:** "pretty good but could be better"

### 3. Criterion types matter

Grader uses three criterion types that affect how aggressively the AI's work is
reviewed:

| Type | Use for | Review bias |
|---|---|---|
| `objective` | Math, code, factual recall | Light review — right or wrong |
| `analytic` | Structured judgment against explicit descriptors | Standard review |
| `open_ended` | Holistic prose judgment (essays, reflections) | Heavy review — style bias is real |

Research (arXiv 2603.18765) finds LLM graders carry significant style bias on open-ended
work — informal tone costs ~1.9/10 points, non-native phrasing ~1.35 — and this bias
**survives explicit "ignore style" instructions**. Grader routes open-ended criteria
to heavier human review to compensate.

## How to get rubrics into Grader

### Paste from Word/Canvas/syllabus

The parser accepts:
- Markdown tables: `Criterion | Max | Type | Levels`
- Heading + bullet format:
  ```
  ## Criterion Name (5 pts, analytic)
  - 5: Excellent — descriptor
  - 4: Good — descriptor
  ```
- Plain text with criterion headers

The parser is intentionally forgiving — you should get something usable without
formatting lectures.

### Import from Canvas

If your assignment already has a Canvas rubric:
```bash
POST /api/rubrics  {"title": "Canvas Import", "rubric_text": null}
```
Or use the Canvas import endpoint, which pulls the rubric via canvas-pp-cli.

## Calibration: how anchors work

Grader doesn't grade in a vacuum. After you grade a few papers, it embeds them and
uses them as anchor examples for future drafts:

> *"I scored this a 4 on Evidence because it resembles these three you scored 4 —
> same pattern of citing a source without integrating it."*

This works from your first graded paper and improves every time you grade one more.
The key finding from P1 calibration: anchors at k=6 produce +0.135 QWK lift, but
k≥8 *hurts* — too many examples dilute the signal.

## Rubric amendment workflow

When you override an AI grade, Grader detects patterns in your overrides:
- "You've lowered 'Evidence' by 1-2 points on 5 papers — the criterion description
  may be too generous"
- It proposes amended descriptor text for your review

You accept or reject the amendment. If accepted, a new rubric version is created
(the old version is preserved — every grade references its rubric version for audit).

## Tips for different assignment types

### Essays (open_ended)
- Use 4 criteria, not more. More criteria = more noise.
- One criterion should be `open_ended` (holistic quality); others `analytic`.
- Keep max points at 5 per criterion. 0-10 scores poorly.

### Problem sets (objective)
- One criterion per problem, or group by problem type.
- Use `objective` type — the AI is reliable here.
- Levels can be simpler: full marks / partial / no marks.

### Lab reports (analytic)
- Criteria: hypothesis, methods, results, interpretation, presentation.
- Use `analytic` type with 4-5 anchored levels.
- Include a `mechanics` criterion for formatting/citations.

### Code (objective)
- Criteria: correctness, style, efficiency, documentation.
- `objective` for correctness, `analytic` for the rest.
- Provide test-case-style descriptors: "passes all edge cases" vs "passes basic tests".

## Common mistakes

1. **Too many criteria** — 4 is optimal. 6+ dilutes each criterion's signal.
2. **0-10 scales** — use 0-5. The AI can't reliably distinguish 7 from 8.
3. **Vague descriptors** — "good analysis" is useless. "identifies the key pattern
   and explains why it matters" is actionable.
4. **No anchor levels** — even 2 levels (full marks / no marks) is better than none.
   3-5 is the sweet spot.
5. **Forgetting criterion types** — `open_ended` triggers heavier review. Use it
   for holistic prose, not for everything.