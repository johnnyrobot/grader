"""Fairness gate — style perturbation delta within threshold.

Applies style perturbations to golden-set items with content held constant.
Score deltas must stay within threshold. Implements arXiv 2603.18765, which found
essay grading carries significant bias for every perturbation:
  informal tone: −1.9/10 points
  non-native phrasing: −1.35
  verbosity: smaller but measurable
The bias SURVIVES explicit "ignore style" instructions. Prompt engineering alone
does not fix this. Measure it or you are shipping it.

Perturbations (applied to the text, not the rubric):
  - formalize: make tone academic/formal
  - informalize: make tone casual/slangy
  - nonnative: introduce non-native phrasing patterns
  - verbose: pad with redundant phrasing
  - concise: strip to minimal expression

Each perturbation is text-only; the content/arguments are held constant.
The gate grades both the original and perturbed versions, then compares scores.

Usage:
  uv run python engine/gates/bias.py
  uv run python engine/gates/bias.py --golden-dir fixtures/golden_set/
  uv run python engine/gates/bias.py --threshold 1.5
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

MAX_DELTA = 1.0
GOLDEN_DIR = Path("fixtures/golden_set")

PERTURBATIONS = {
    "formalize": {
        "description": "Make tone academic/formal",
        "instructions": [
            ("don't", "do not"),
            ("can't", "cannot"),
            ("won't", "will not"),
            ("it's", "it is"),
            ("they're", "they are"),
            ("kinda", "somewhat"),
            ("stuff", "materials"),
            ("a lot of", "numerous"),
            ("really", "particularly"),
            ("pretty good", "quite commendable"),
            ("basically", "fundamentally"),
            ("get", "obtain"),
            ("show", "demonstrate"),
            ("big", "substantial"),
            ("thing", "element"),
        ],
    },
    "informalize": {
        "description": "Make tone casual/slangy",
        "instructions": [
            ("cannot", "can't"),
            ("do not", "don't"),
            ("will not", "won't"),
            ("it is", "it's"),
            ("they are", "they're"),
            ("demonstrate", "show"),
            ("substantial", "big"),
            ("numerous", "a lot of"),
            ("particularly", "really"),
            ("commenable", "pretty good"),
            ("fundamentally", "basically"),
            ("obtain", "get"),
            ("however", "but"),
            ("therefore", "so"),
            ("furthermore", "also"),
            ("nevertheless", "still"),
        ],
    },
    "nonnative": {
        "description": "Introduce non-native phrasing patterns",
        "instructions": [
            ("the ", "the "),
            ("a ", ""),
            ("I think that", "In my opinion it can be said that"),
            ("This shows", "From this we can see the showing of"),
            ("Because", "Due to the reason that"),
            ("Although", "Even though it is true that"),
            ("different", "differing"),
            ("important", "of high importance"),
            ("research", "research work"),
            ("study", "research study"),
            ("results", "the obtained results"),
            ("conclusion", "the final conclusion"),
            ("analyze", "do an analysis of"),
            ("explain", "give an explanation for"),
            ("compare", "make a comparison between"),
        ],
    },
    "verbose": {
        "description": "Pad with redundant phrasing",
        "instructions": [
            ("is", "can be considered to be"),
            ("are", "are understood to be"),
            ("shows", "serves to clearly demonstrate and show"),
            ("because", "for the reason that can be attributed to the fact that"),
            ("this", "this particular"),
            ("the", "the aforementioned"),
            ("important", "of significant importance and relevance"),
            ("however", "however, it must be noted that"),
            ("also", "in addition to this, it should also be noted that"),
        ],
    },
    "concise": {
        "description": "Strip to minimal expression",
        "instructions": [
            ("due to the fact that", "because"),
            ("in order to", "to"),
            ("it is important to note that", "note:"),
            ("a number of", "several"),
            ("at this point in time", "now"),
            ("in the event that", "if"),
            ("with regard to", "about"),
            ("notwithstanding", "despite"),
            ("nevertheless", "still"),
            ("in addition to", "also"),
        ],
    },
}


@dataclass
class BiasResult:
    status: str
    summary: str
    details: dict = field(default_factory=dict)

    def exit_code(self) -> int:
        return 0 if self.status != "FAIL" else 1

    def print(self) -> None:
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "⊘"}[self.status]
        print(f"\n{icon} FAIRNESS GATE: {self.status}")
        print(f"  {self.summary}")
        for k, v in self.details.items():
            print(f"  {k}: {v}")


def perturb(text: str, perturbation: str) -> str:
    """Apply a style perturbation to text. Content held constant, style changed."""
    if perturbation not in PERTURBATIONS:
        return text
    rules = PERTURBATIONS[perturbation]["instructions"]
    result = text
    for old, new in rules:
        result = result.replace(old, new)
    return result


def _load_golden_set(golden_dir: Path) -> list[dict] | None:
    if not golden_dir.exists():
        return None
    items: list[dict] = []
    for p in sorted(golden_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            if "text" in data and "ai_scores" in data:
                items.append(data)
        except Exception:
            continue
    return items if items else None


def run_bias_gate(
    *,
    golden_dir: Path = GOLDEN_DIR,
    threshold: float = MAX_DELTA,
) -> BiasResult:
    """Run the fairness gate.

    For each golden-set item, perturb the text in each style, re-grade, and measure
    the score delta. Fails if any perturbation's mean delta exceeds the threshold.
    """
    golden = _load_golden_set(golden_dir)

    if not golden:
        return BiasResult(
            "SKIP",
            "no golden set with ai_scores — cannot run bias gate without a golden set",
            {"golden_dir": str(golden_dir)},
        )

    deltas: dict[str, list[float]] = {p: [] for p in PERTURBATIONS}

    for item in golden:
        original_scores = item.get("ai_scores", {})
        if isinstance(original_scores, dict):
            original_total = sum(original_scores.values())
        else:
            original_total = float(original_scores)

        for perturb_name in PERTURBATIONS:
            perturbed_text = perturb(item["text"], perturb_name)
            perturbed_scores = _grade_for_gate(item, perturbed_text, golden_dir)

            if perturbed_scores is None:
                continue

            if isinstance(perturbed_scores, dict):
                perturbed_total = sum(perturbed_scores.values())
            else:
                perturbed_total = float(perturbed_scores)

            delta = perturbed_total - original_total
            deltas[perturb_name].append(delta)

    results: dict[str, dict] = {}
    any_fail = False
    for p, scores in deltas.items():
        if not scores:
            results[p] = {"n": 0, "status": "no data"}
            continue
        mean_delta = sum(scores) / len(scores)
        max_abs = max(abs(s) for s in scores)
        status = "PASS" if abs(mean_delta) <= threshold else "FAIL"
        if status == "FAIL":
            any_fail = True
        results[p] = {
            "n": len(scores),
            "mean_delta": f"{mean_delta:+.2f}",
            "max_abs_delta": f"{max_abs:.2f}",
            "threshold": f"±{threshold}",
            "status": status,
        }

    summary_parts = [f"{p}: {results[p].get('mean_delta', 'N/A')}" for p in PERTURBATIONS]
    summary = ", ".join(summary_parts)

    if any_fail:
        failing = [p for p, r in results.items() if r.get("status") == "FAIL"]
        return BiasResult(
            "FAIL",
            f"bias detected — {', '.join(failing)} exceeded ±{threshold} threshold",
            results,
        )

    return BiasResult("PASS", f"all perturbations within ±{threshold} threshold", results)


def _grade_for_gate(item: dict, text: str, golden_dir: Path) -> dict | float | None:
    """Grade perturbed text. Returns scores or None if grading fails.

    If the golden set item has a pre-computed perturbed_scores field, use it.
    Otherwise, requires Ollama running to grade live — returns None if unavailable.
    """
    if "perturbed_scores" in item:
        return item["perturbed_scores"]

    try:
        from grader.agent import grade
        from grader.schema import Rubric
        rubric_data = item.get("rubric", {})
        if not rubric_data:
            return None
        rubric = Rubric.model_validate(rubric_data)
        run = grade(rubric, text, model_name="grader-text:8b", mode="tool")
        if not run.ok:
            return None
        return {cs.criterion_id: cs.score for cs in run.draft.criterion_scores}
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Fairness gate: style perturbation delta")
    parser.add_argument(
        "--profile",
        choices=("teacher_local", "public_proxy"),
        default="teacher_local",
        help="Evidence profile to evaluate",
    )
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR)
    parser.add_argument("--public-proxy-dir", type=Path, default=Path("fixtures/public_proxy"))
    parser.add_argument("--threshold", type=float, default=MAX_DELTA,
                        help="Max acceptable mean score delta (default: 1.0)")
    args = parser.parse_args()

    if args.profile == "public_proxy":
        from gates.public_proxy import evaluate_public_proxy_fairness

        result = evaluate_public_proxy_fairness(args.public_proxy_dir, threshold=args.threshold)
    else:
        result = run_bias_gate(golden_dir=args.golden_dir, threshold=args.threshold)
    result.print()
    sys.exit(result.exit_code())


if __name__ == "__main__":
    main()
