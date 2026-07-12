"""Agreement metrics — how we know calibration is working.

Quadratic Weighted Kappa is the standard in automated-scoring research (it's the metric the
ASAP competitions were judged on) because it accounts for chance agreement and punishes
*large* disagreements far more than small ones: predicting a 5 when the teacher gave a 1 is
much worse than predicting a 4. Plain accuracy hides exactly that.

The honest bar isn't an abstract "QWK >= 0.70". It's *human-vs-human* agreement on the same
work. Two trained raters typically land around 0.7-0.8; a grader that matches the teacher as
well as another trained rater would is doing its job. The Mohler dataset ships two
independent graders per item precisely so we can compute that ceiling (docs/DATA-PLAN.md).
"""

from __future__ import annotations

from dataclasses import dataclass


def quadratic_weighted_kappa(a: list[int], b: list[int], *, min_r: int | None = None,
                             max_r: int | None = None) -> float:
    """QWK between two integer rating vectors. 1.0 = perfect, 0.0 = chance, <0 = worse."""
    if len(a) != len(b) or not a:
        raise ValueError("rating vectors must be same non-zero length")

    lo = min_r if min_r is not None else min(min(a), min(b))
    hi = max_r if max_r is not None else max(max(a), max(b))
    n = hi - lo + 1
    if n <= 1:
        return 1.0 if a == b else 0.0

    obs = [[0.0] * n for _ in range(n)]
    for x, y in zip(a, b):
        obs[x - lo][y - lo] += 1

    hist_a = [0.0] * n
    hist_b = [0.0] * n
    for x in a:
        hist_a[x - lo] += 1
    for y in b:
        hist_b[y - lo] += 1

    total = len(a)
    num = den = 0.0
    for i in range(n):
        for j in range(n):
            w = ((i - j) ** 2) / ((n - 1) ** 2)  # quadratic penalty
            exp = hist_a[i] * hist_b[j] / total
            num += w * obs[i][j]
            den += w * exp
    return 1.0 - num / den if den else 0.0


@dataclass
class Agreement:
    qwk: float
    exact: float          # fraction identical
    adjacent: float       # fraction within 1 point
    mean_error: float     # signed: +ve = AI grades HIGHER than the teacher (leniency)
    mean_abs_error: float
    n: int

    def __str__(self) -> str:
        bias = "lenient" if self.mean_error > 0.15 else "harsh" if self.mean_error < -0.15 else "neutral"
        return (f"QWK {self.qwk:.3f} | exact {self.exact:.0%} | adj {self.adjacent:.0%} | "
                f"bias {self.mean_error:+.2f} ({bias}) | n={self.n}")


def agreement(ai: list[float], human: list[float]) -> Agreement:
    """Compare AI scores to the teacher's. Rounded to ints for QWK.

    mean_error is signed on purpose. LLM judges are documented to drift lenient — they
    over-credit weak work — and a symmetric error metric would hide that. The sign is the
    part a teacher would actually want to know.
    """
    if len(ai) != len(human) or not ai:
        raise ValueError("score vectors must be same non-zero length")

    ai_i = [round(x) for x in ai]
    hu_i = [round(x) for x in human]
    diffs = [x - y for x, y in zip(ai, human)]
    n = len(ai)

    return Agreement(
        qwk=quadratic_weighted_kappa(ai_i, hu_i),
        exact=sum(x == y for x, y in zip(ai_i, hu_i)) / n,
        adjacent=sum(abs(x - y) <= 1 for x, y in zip(ai_i, hu_i)) / n,
        mean_error=sum(diffs) / n,
        mean_abs_error=sum(abs(d) for d in diffs) / n,
        n=n,
    )


def human_ceiling(rater_a: list[float], rater_b: list[float]) -> Agreement:
    """Agreement between two human raters — the bar the AI is actually chasing.

    Reporting AI QWK without this is meaningless. If two trained humans only agree at 0.68
    on a task, an AI at 0.65 is essentially at parity, not failing.
    """
    return agreement(rater_a, rater_b)
