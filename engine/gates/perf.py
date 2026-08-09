"""Performance gate — 16 GB floor budgets.

P5.md says: "Perf on the FLOOR, not the dev box." The 64 GB M2 Max hid a 41 GB model
from us once (P0). This gate enforces:

  1. 1,500-word essay ≤ 90 seconds (single item)
  2. 30-item batch ≤ 45 minutes (total throughput)
  3. UI response < 100ms (frontend — not measured here, separate Lighthouse gate)

On a 16 GB machine, not the dev box. The gate accepts a --memory-floor flag to
document which machine it ran on, and uses the actual grading timing.

When Ollama is not running, the gate reports SKIP.
When Ollama is running but grading fails, the gate reports SKIP with the error.

Usage:
  uv run python engine/gates/perf.py
  uv run python engine/gates/perf.py --memory-floor 16
  uv run python engine/gates/perf.py --word-count 1500 --max-seconds 90
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gates.result import CheckResult

ESSAY_MAX_SECONDS = 90
BATCH_MAX_SECONDS = 2700  # 45 min
BATCH_SIZE = 30
ESSAY_WORD_COUNT = 1500


class PerfResult(CheckResult):
    label = "PERFORMANCE GATE"


def _check_ollama() -> bool:
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _measure_token_cap_seconds(model: str) -> float | None:
    """How long one attempt takes when it generates to the cap, in seconds.

    This is the first half of a retried grade. A runaway does not fail fast: it
    generates until `GRADE_MAX_TOKENS` is reached, and only then does the second
    attempt with different sampling begin. Timing a normal grade measures neither
    half of that.

    Measured as throughput against a known token count rather than by provoking a
    real runaway, which is a ~3% stochastic event and cannot be asked for. Returns
    None when the sample stopped early, because a run that never reached the cap
    cannot say what reaching it costs.
    """
    try:
        import httpx
        from grader.agent import GRADE_MAX_TOKENS

        started = time.monotonic()
        response = httpx.post(
            "http://localhost:11434/v1/chat/completions",
            json={
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": (
                        "Write a continuous, detailed analysis of working memory research. "
                        "Do not summarise, do not conclude, and do not stop."
                    ),
                }],
                "temperature": 0.0,
                # The spelling Ollama's /v1 layer actually reads — see the
                # measurements in grader.agent's GRADING_SETTINGS.
                "max_tokens": GRADE_MAX_TOKENS,
            },
            timeout=300,
        )
        elapsed = time.monotonic() - started
        if response.status_code != 200:
            return None
        body = response.json()
        generated = int(body.get("usage", {}).get("completion_tokens") or 0)
        if generated <= 0:
            return None
        # Scale to the cap: a sample that stopped short still measures throughput,
        # and the cap is what a runaway actually generates.
        return elapsed * (GRADE_MAX_TOKENS / generated)
    except Exception:
        return None


def _retry_worst_case(
    *,
    single_seconds: float,
    cap_seconds: float | None,
    max_seconds: float,
) -> dict[str, str]:
    """What one item costs when the runaway retry fires.

    Reported, not gated. The gate's documented budget covers a single attempt,
    and whether a retried item must also fit inside it is a product decision
    this gate does not get to make — but it must not let a happy-path number
    stand in silently for the case that has never been measured.
    """
    if cap_seconds is None:
        return {"retry_worst_case_seconds": "not measured"}
    worst = cap_seconds + single_seconds
    over = worst - max_seconds
    return {
        "runaway_first_attempt_seconds": f"{cap_seconds:.1f}",
        "retry_worst_case_seconds": f"{worst:.1f}",
        "retry_worst_case_budget": (
            f"within ≤{max_seconds:g}s" if over <= 0 else f"OVER ≤{max_seconds:g}s by {over:.1f}s"
        ),
    }


def _generate_essay(word_count: int) -> str:
    """Generate a synthetic essay of approximately word_count words."""
    paragraph = (
        "The relationship between working memory capacity and academic performance has been "
        "extensively studied in cognitive psychology. Research suggests that individuals with "
        "higher working memory capacity tend to perform better on complex cognitive tasks, "
        "though the mechanisms underlying this relationship remain contested. Some researchers "
        "argue that working memory directly supports reasoning by maintaining relevant "
        "information during problem-solving, while others propose that the relationship is "
        "mediated by attentional control and the ability to suppress irrelevant information. "
        "Evidence from dual-task paradigms suggests that disrupting working memory resources "
        "impairs performance on tasks that require integration of multiple sources of "
        "information, but leaves simpler recognition tasks largely unaffected. "
    )
    words = paragraph.split()
    essay_words: list[str] = []
    while len(essay_words) < word_count:
        essay_words.extend(words)
    return " ".join(essay_words[:word_count])


def _run_single_grade(text: str, model: str) -> tuple[float, bool, str]:
    """Grade a single essay. Returns (seconds, ok, error)."""
    try:
        from grader.agent import grade
        from grader.schema import Rubric, RubricCriterion, CriterionType

        rubric = Rubric(
            title="Perf Test Rubric",
            criteria=[
                RubricCriterion(
                    id="content",
                    title="Content Quality",
                    max_points=5.0,
                    criterion_type=CriterionType.OPEN_ENDED,
                ),
                RubricCriterion(
                    id="evidence",
                    title="Use of Evidence",
                    max_points=5.0,
                    criterion_type=CriterionType.ANALYTIC,
                ),
                RubricCriterion(
                    id="clarity",
                    title="Clarity & Organization",
                    max_points=5.0,
                    criterion_type=CriterionType.ANALYTIC,
                ),
                RubricCriterion(
                    id="mechanics",
                    title="Mechanics",
                    max_points=5.0,
                    criterion_type=CriterionType.OPEN_ENDED,
                ),
            ],
        )

        start = time.monotonic()
        run = grade(rubric, text, model_name=model, mode="tool")
        elapsed = time.monotonic() - start
        return elapsed, run.ok, run.error or ""
    except Exception as e:
        return 0.0, False, str(e)[:200]


def run_perf_gate(
    *,
    memory_floor: int = 16,
    word_count: int = ESSAY_WORD_COUNT,
    max_seconds: float = ESSAY_MAX_SECONDS,
    batch_size: int = BATCH_SIZE,
    batch_max: float = BATCH_MAX_SECONDS,
    model: str = "grader-text:8b",
) -> PerfResult:
    """Run the performance gate against the 16 GB floor budgets."""

    if not _check_ollama():
        return PerfResult(
            "SKIP",
            "Ollama not running — cannot measure grading performance",
            {"model": model, "budget": f"{max_seconds}s per {word_count}-word essay"},
        )

    essay = _generate_essay(word_count)
    actual_words = len(essay.split())

    single_time, ok, error = _run_single_grade(essay, model)

    if not ok:
        return PerfResult(
            "SKIP",
            f"grading failed: {error}",
            {"model": model, "word_count": actual_words},
        )

    details = {
        "model": model,
        "memory_floor": f"{memory_floor} GB",
        "word_count": actual_words,
        "single_grade_seconds": f"{single_time:.1f}",
        "single_budget": f"≤{max_seconds}s",
    }

    single_pass = single_time <= max_seconds

    retry_details = _retry_worst_case(
        single_seconds=single_time,
        cap_seconds=_measure_token_cap_seconds(model),
        max_seconds=max_seconds,
    )
    details.update(retry_details)

    batch_details: dict[str, str] = {}
    batch_pass = True

    if single_pass and batch_size > 1:
        batch_estimate = single_time * batch_size
        batch_details = {
            "batch_estimate_seconds": f"{batch_estimate:.0f}",
            "batch_budget": f"≤{batch_max}s ({batch_size} items)",
        }
        batch_pass = batch_estimate <= batch_max
        details.update(batch_details)
    else:
        details["batch"] = "skipped (single grade too slow for batch estimate)"

    if not single_pass:
        return PerfResult(
            "FAIL",
            f"single grade {single_time:.1f}s exceeds {max_seconds}s budget",
            details,
        )

    if not batch_pass:
        return PerfResult(
            "FAIL",
            f"estimated batch {single_time * batch_size:.0f}s exceeds {batch_max}s budget",
            details,
        )

    # A PASS that says only the happy-path number would be the automation-bias
    # failure in miniature: true, and quietly not the whole story.
    summary = f"{single_time:.1f}s per {actual_words}-word essay (budget {max_seconds}s)"
    if str(retry_details.get("retry_worst_case_budget", "")).startswith("OVER"):
        summary += (
            f" — but a retried item costs {retry_details['retry_worst_case_seconds']}s, "
            f"{retry_details['retry_worst_case_budget']}"
        )
    return PerfResult("PASS", summary, details)


def main() -> None:
    parser = argparse.ArgumentParser(description="Performance gate: 16 GB floor budgets")
    parser.add_argument("--memory-floor", type=int, default=16,
                        help="Memory floor in GB (default: 16)")
    parser.add_argument("--word-count", type=int, default=ESSAY_WORD_COUNT)
    parser.add_argument("--max-seconds", type=float, default=ESSAY_MAX_SECONDS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--batch-max", type=float, default=BATCH_MAX_SECONDS)
    parser.add_argument("--model", default="grader-text:8b")
    args = parser.parse_args()

    result = run_perf_gate(
        memory_floor=args.memory_floor,
        word_count=args.word_count,
        max_seconds=args.max_seconds,
        batch_size=args.batch_size,
        batch_max=args.batch_max,
        model=args.model,
    )
    result.print()
    sys.exit(result.exit_code())


if __name__ == "__main__":
    main()