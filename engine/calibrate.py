#!/usr/bin/env python3
"""P1: does teacher-anchored retrieval actually make the model grade like the teacher?

The experiment, on real human-graded data (Mohler ASAG, CC BY 4.0, 2273 short answers each
scored 0-5 by TWO independent human graders):

    baseline   grade with rubric + reference answer only
    anchored   grade with rubric + reference answer + k retrieved exemplars the
               "teacher" already graded (similarity-matched, score-band balanced)

Scored against the humans' average, and — the part that makes the number mean anything —
against the HUMAN CEILING: how well the two human graders agree with each other on the very
same items. An AI that matches the teacher as closely as another trained rater does has
done its job; chasing QWK 1.0 against a noisy human target is chasing noise.

Run:  uv run python engine/calibrate.py --n-test 20 --n-exemplars 60
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from grader.agent import grade  # noqa: E402
from grader.exemplars import Exemplar, ExemplarStore, render_anchors  # noqa: E402
from grader.metrics import agreement, human_ceiling  # noqa: E402
from grader.schema import CriterionType, Rubric, RubricCriterion, RubricLevel  # noqa: E402

MOHLER = "https://huggingface.co/datasets/nkazi/MohlerASAG/resolve/main/data/cleaned-oe-00001.parquet"
MOHLER_LOCAL = Path(__file__).parent.parent / "fixtures" / "corpora" / "mohler_asag.parquet"


def _load_mohler() -> pd.DataFrame:
    """Load Mohler ASAG, falling back to a local copy if HuggingFace is unreachable."""
    if MOHLER_LOCAL.exists():
        return pd.read_parquet(MOHLER_LOCAL)
    try:
        return pd.read_parquet(MOHLER)
    except Exception:
        if MOHLER_LOCAL.exists():
            return pd.read_parquet(MOHLER_LOCAL)
        raise

# Mohler is a single-criterion 0-5 correctness judgment against a reference answer. Modelled
# as a one-criterion rubric so the same grading path serves it — no special-case code.
RUBRIC = Rubric(
    title="Short Answer Correctness (Computer Science)",
    criteria=[
        RubricCriterion(
            id="correctness",
            title="Correctness & Completeness",
            description="How fully and accurately the student's answer captures the reference answer.",
            max_points=5,
            criterion_type=CriterionType.OBJECTIVE,
            levels=[
                RubricLevel(score=5, label="Fully correct", descriptor="Captures the reference answer completely and accurately."),
                RubricLevel(score=4, label="Mostly correct", descriptor="Substantively correct; a minor element is missing or imprecise."),
                RubricLevel(score=3, label="Partially correct", descriptor="Core idea present but incomplete or partly wrong."),
                RubricLevel(score=2, label="Minimally correct", descriptor="Touches the topic; largely incomplete or confused."),
                RubricLevel(score=1, label="Barely relevant", descriptor="Little correct content, but on-topic."),
                RubricLevel(score=0, label="Incorrect", descriptor="Wrong, empty, or unrelated."),
            ],
        )
    ],
)


def load(n_needed: int, seed: int, *, question_prefix: str | None = None) -> pd.DataFrame:
    df = _load_mohler()
    df = df.dropna(subset=["student_answer", "score_grader_1", "score_grader_2", "score_avg"])
    df = df[df.student_answer.str.len() > 10]
    counts = df.groupby("question").size().sort_values(ascending=False)

    if question_prefix:
        matches = [q for q in counts.index if question_prefix.lower() in q.lower()]
        if not matches:
            print(f"  WARNING: no question matches '{question_prefix}', using default")
        else:
            best = matches[0]
            sub = df[df.question == best].copy()
            if len(sub) < n_needed:
                print(f"  WARNING: '{question_prefix}' has only {len(sub)} items, need {n_needed}, pooling")
                sub = df[df.question.isin(matches[:6])].copy()
            return sub.sample(frac=1.0, random_state=seed).reset_index(drop=True)

    best = counts.index[0]
    sub = df[df.question == best].copy()
    if len(sub) < n_needed:
        sub = df[df.question.isin(counts.index[:6])].copy()
    return sub.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def as_submission(row: pd.Series) -> str:
    return (
        f"Question: {row.question.replace('<STOP>','').strip()}\n\n"
        f"Student answer: {row.student_answer.replace('<STOP>','').strip()}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="grader-text:8b")
    ap.add_argument("--n-test", type=int, default=20)
    ap.add_argument("--n-exemplars", type=int, default=60)
    ap.add_argument("--k", type=int, default=6, help="anchors retrieved per grading call")
    ap.add_argument("--seed", type=int, default=20260711)
    ap.add_argument("--question", default=None, help="substring to select a specific Mohler question")
    ap.add_argument("--out", type=Path, default=Path("engine/calibration_results.json"))
    args = ap.parse_args()

    random.seed(args.seed)
    df = load(args.n_test + args.n_exemplars, args.seed, question_prefix=args.question)
    test = df.iloc[: args.n_test]
    pool = df.iloc[args.n_test : args.n_test + args.n_exemplars]

    print(f"Mohler ASAG — {len(df)} answers to: {df.question.iloc[0][:70].replace(chr(10),' ')}...")
    print(f"  test items:      {len(test)}")
    print(f"  exemplar pool:   {len(pool)}  (the 'teacher's' previously graded work)")
    print(f"  anchors per call: {args.k}\n")

    # --- The ceiling. Compute this FIRST; it defines what "good" means. ----------------
    ceiling = human_ceiling(
        test.score_grader_1.astype(float).tolist(),
        test.score_grader_2.astype(float).tolist(),
    )
    print(f"HUMAN CEILING (grader 1 vs grader 2, same items)\n  {ceiling}\n")

    # --- Load the teacher's graded work into the exemplar store ------------------------
    store_path = Path("fixtures/exemplars_mohler.db")
    store_path.unlink(missing_ok=True)
    store = ExemplarStore(store_path)
    t0 = time.perf_counter()
    store.add_many([
        Exemplar(
            id=None, assignment_id="mohler", text=as_submission(r),
            total=float(r.score_avg), max_total=5.0,
            scores={"correctness": float(r.score_avg)},
            comment="", source="imported",
        )
        for _, r in pool.iterrows()
    ])
    print(f"embedded {store.count('mohler')} exemplars in {time.perf_counter()-t0:.1f}s\n")

    reference = f"Reference answer: {test.instructor_answer.iloc[0].replace('<STOP>','').strip()}"

    rows: list[dict] = []
    for arm in ("baseline", "anchored"):
        print(f"=== {arm} ".ljust(64, "="))
        ai: list[float] = []
        human: list[float] = []
        for i, (_, r) in enumerate(test.iterrows()):
            sub = as_submission(r)
            anchors_txt = ""
            if arm == "anchored":
                hits = store.retrieve("mohler", sub, k=args.k, balance=True)
                anchors_txt = render_anchors(hits, ["correctness"])

            run = grade(RUBRIC, sub, model_name=args.model, mode="tool",
                        anchors=anchors_txt, materials=reference)
            if not run.ok:
                print(f"  [{i:02d}] FAIL {run.error[:60]}")
                continue
            score = run.draft.total
            truth = float(r.score_avg)
            ai.append(score)
            human.append(truth)
            mark = "  " if abs(score - truth) <= 1 else "!!"
            print(f"  [{i:02d}]{mark} ai={score:.1f} teacher={truth:.1f} "
                  f"(g1={r.score_grader_1:.0f} g2={r.score_grader_2:.0f}) conf={run.draft.confidence:.2f}")

        agr = agreement(ai, human)
        print(f"\n  {arm.upper():9s} {agr}\n")
        rows.append({"arm": arm, "n": agr.n, "qwk": agr.qwk, "exact": agr.exact,
                     "adjacent": agr.adjacent, "mean_error": agr.mean_error,
                     "mean_abs_error": agr.mean_abs_error, "ai": ai, "human": human})

    base, anch = rows[0], rows[1]
    print("=" * 64)
    print(f"{'':10s} {'QWK':>7s} {'exact':>7s} {'adj':>7s} {'bias':>7s}")
    print(f"{'human':10s} {ceiling.qwk:7.3f} {ceiling.exact:6.0%} {ceiling.adjacent:6.0%} {ceiling.mean_error:+7.2f}")
    print(f"{'baseline':10s} {base['qwk']:7.3f} {base['exact']:6.0%} {base['adjacent']:6.0%} {base['mean_error']:+7.2f}")
    print(f"{'anchored':10s} {anch['qwk']:7.3f} {anch['exact']:6.0%} {anch['adjacent']:6.0%} {anch['mean_error']:+7.2f}")

    lift = anch["qwk"] - base["qwk"]
    pct_of_ceiling = (anch["qwk"] / ceiling.qwk * 100) if ceiling.qwk > 0 else float("nan")
    print(f"\n  anchor lift: {lift:+.3f} QWK")
    print(f"  anchored reaches {pct_of_ceiling:.0f}% of the human-vs-human ceiling")
    verdict = "ANCHORS HELP" if lift > 0.02 else "NO LIFT — investigate" if lift > -0.02 else "ANCHORS HURT"
    print(f"  verdict: {verdict}")

    args.out.write_text(json.dumps(
        {"model": args.model, "k": args.k, "n_test": len(test), "n_exemplars": len(pool),
         "human_ceiling": {"qwk": ceiling.qwk, "exact": ceiling.exact, "mean_error": ceiling.mean_error},
         "arms": rows, "anchor_lift_qwk": lift, "pct_of_human_ceiling": pct_of_ceiling},
        indent=2))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
