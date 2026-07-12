#!/usr/bin/env python3
"""P0 spike: does a small local model produce reliable rubric JSON, and in which mode?

Answers the four questions P0 exists to answer (docs/PRD.md §12):
  1. Tool-Output vs Prompted-Output vs Native-Output on quantized local models
  2. Which model grades acceptably at the 16 GB tier
  3. Latency vs the PRD's <=90s/essay budget
  4. Score stability across repeats (temp 0 should be near-deterministic)

Run:  uv run python engine/bench.py
      uv run python engine/bench.py --models qwen3:8b,qwen3-vl:4b --repeats 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from grader.agent import OutputMode, grade  # noqa: E402
from grader.schema import CriterionType, Rubric, RubricCriterion, RubricLevel  # noqa: E402

# The rubric that is live in the Canvas sandbox (course 106, rubric 189): 4 analytic
# criteria x 0-5. Benchmarking against the real thing, not a toy.
LEVELS = [
    RubricLevel(score=5, label="Exemplary", descriptor="Sophisticated and fully realized."),
    RubricLevel(score=4, label="Proficient", descriptor="Solid and clearly competent, with minor gaps."),
    RubricLevel(score=3, label="Developing", descriptor="Present but uneven or underdeveloped."),
    RubricLevel(score=2, label="Beginning", descriptor="Attempted but substantially incomplete."),
    RubricLevel(score=0, label="Missing", descriptor="Absent or unrelated to the task."),
]

RUBRIC = Rubric(
    title="Essay Rubric (GRADER TEST)",
    criteria=[
        RubricCriterion(id="thesis", title="Thesis & Argument", max_points=5,
                        description="Clarity and defensibility of the central claim.",
                        criterion_type=CriterionType.ANALYTIC, levels=LEVELS),
        RubricCriterion(id="evidence", title="Use of Evidence", max_points=5,
                        description="Relevance, integration, and accuracy of supporting evidence.",
                        criterion_type=CriterionType.ANALYTIC, levels=LEVELS),
        RubricCriterion(id="organization", title="Organization", max_points=5,
                        description="Logical structure and coherence across paragraphs.",
                        criterion_type=CriterionType.ANALYTIC, levels=LEVELS),
        RubricCriterion(id="mechanics", title="Mechanics & Citation", max_points=5,
                        description="Grammar, style, and correctly formatted citations.",
                        criterion_type=CriterionType.OPEN_ENDED, levels=LEVELS),
    ],
)

# The same four quality tiers submitted to the Canvas sandbox. `expect` is a human sanity
# band, not ground truth — it asks "is the model ordering these correctly at all?"
SUBMISSIONS: list[tuple[str, str, tuple[float, float]]] = [
    ("strong", """\
The evidence for feedback timing is strongest in Hattie's synthesis, though the effect sizes vary
considerably by task complexity. I want to argue that immediacy matters less than actionability: a
delayed comment a student can act on outperforms an instant score they cannot. Three studies
complicate this view. Kluger and DeNisi (1996) found that roughly one-third of feedback
interventions actually *decreased* performance, particularly when feedback directed attention to
the self rather than the task. Shute (2008) qualifies this further, showing that elaborated
feedback helps low-achieving students more than high-achieving ones, which suggests the effect is
not uniform. Taken together, these findings imply that "more feedback" is not a coherent policy
goal; the design of the feedback is what carries the effect. I conclude that instructors should
optimize for whether a student can act on a comment, not for how quickly it arrives.""",
     (16, 20)),
    ("middling", """\
Feedback is important for learning. Many studies show that students who get feedback do better than
students who do not. Hattie says feedback has a large effect size, which means it works well. In
this essay I will explain why feedback matters and give some examples from class. First, feedback
tells students what they did wrong. Second, it tells them how to improve. Third, it can motivate
them to try harder. In our class, when the teacher gave comments on my draft, my final paper was
better. This shows feedback works. In conclusion, feedback is an important part of learning and
teachers should give more of it.""",
     (9, 15)),
    ("weak", """\
Feedback helps students learn. Teachers should give feedback. It is good because it helps students.
Studies prove feedback always works for every student in every situation. Feedback is important and
that is why teachers use it.""",
     (2, 8)),
    ("barely", """\
i didnt really understand the prompt but i think feedback is when the teacher tells you what you did
wrong""",
     (0, 5)),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qwen3:8b,qwen3-vl:4b")
    ap.add_argument("--modes", default="tool,prompted,native")
    ap.add_argument("--repeats", type=int, default=2, help="repeats per case (stability check)")
    ap.add_argument("--out", type=Path, default=Path("engine/bench_results.json"))
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    modes: list[OutputMode] = [m.strip() for m in args.modes.split(",") if m.strip()]  # type: ignore[assignment]

    print(f"Rubric: {RUBRIC.title} ({RUBRIC.total_points:g} pts, {len(RUBRIC.criteria)} criteria)")
    print(f"Models: {models}\nModes:  {modes}\nRepeats: {args.repeats}\n")

    rows: list[dict] = []
    for model in models:
        for mode in modes:
            print(f"=== {model} / {mode} ".ljust(66, "="))
            oks = 0
            attempts = 0
            times: list[float] = []
            totals_by_tier: dict[str, list[float]] = {}

            for tier, text, (lo, hi) in SUBMISSIONS:
                for rep in range(args.repeats):
                    attempts += 1
                    run = grade(RUBRIC, text, model_name=model, mode=mode)
                    times.append(run.seconds)
                    if not run.ok:
                        print(f"  {tier:9s} rep{rep}  FAIL  {run.seconds:5.1f}s  {run.error[:64]}")
                        continue
                    oks += 1
                    total = run.draft.total
                    totals_by_tier.setdefault(tier, []).append(total)
                    band = "ok " if lo <= total <= hi else "OUT"
                    print(f"  {tier:9s} rep{rep}  {band}  {run.seconds:5.1f}s  "
                          f"total={total:4.1f}/20 (expect {lo:g}-{hi:g})  "
                          f"conf={run.draft.confidence:.2f}  retries={run.retries}")

            # Rank ordering is the real test: can the model tell these four apart?
            means = {t: statistics.mean(v) for t, v in totals_by_tier.items() if v}
            order_ok = None
            if len(means) == 4:
                seq = [means.get(t, -1) for t, _, _ in SUBMISSIONS]  # strong..barely
                order_ok = all(a >= b for a, b in zip(seq, seq[1:]))

            # At temp 0, repeats should agree. Spread here is a reliability red flag.
            spreads = [max(v) - min(v) for v in totals_by_tier.values() if len(v) > 1]
            row = {
                "model": model,
                "mode": mode,
                "success_rate": oks / attempts if attempts else 0.0,
                "median_seconds": statistics.median(times) if times else None,
                "mean_totals": means,
                "rank_order_correct": order_ok,
                "max_spread_at_temp0": max(spreads) if spreads else 0.0,
            }
            rows.append(row)
            print(f"  -> schema success {oks}/{attempts} | median {row['median_seconds']:.1f}s "
                  f"| rank-order {'OK' if order_ok else 'WRONG' if order_ok is False else 'n/a'} "
                  f"| temp0 spread {row['max_spread_at_temp0']:.1f}\n")

    args.out.write_text(json.dumps(rows, indent=2))

    print("=" * 66)
    print(f"{'model':16s} {'mode':9s} {'schema':>7s} {'median':>7s} {'order':>6s} {'spread':>7s}")
    for r in rows:
        med = f"{r['median_seconds']:.1f}s" if r["median_seconds"] else "-"
        order = "OK" if r["rank_order_correct"] else ("WRONG" if r["rank_order_correct"] is False else "-")
        print(f"{r['model']:16s} {r['mode']:9s} {r['success_rate']*100:6.0f}% {med:>7s} "
              f"{order:>6s} {r['max_spread_at_temp0']:6.1f}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
