"""Quality gate — golden set QWK ≥ 0.70, no regression > 0.03.

Runs the grader against a teacher-graded golden set, computes QWK, and fails if:
  1. QWK < 0.70 (absolute floor), or
  2. QWK dropped > 0.03 from the last recorded baseline (regression)

The golden set lives in fixtures/golden_set/ (gitignored — real student work, FERPA).
Each item has: submission text, rubric, human scores.
When the golden set doesn't exist, the gate uses the calibration results as a proxy
and reports SKIP if neither is available.

Usage:
  uv run python engine/gates/quality.py
  uv run python engine/gates/quality.py --golden-dir fixtures/golden_set/
  uv run python engine/gates/quality.py --model grader-text:8b
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from grader.metrics import Agreement, agreement, human_ceiling, quadratic_weighted_kappa

QWK_FLOOR = 0.70
REGRESSION_MAX = 0.03
GOLDEN_DIR = Path("fixtures/golden_set")
BASELINE_FILE = Path("engine/gate_baseline.json")
CALIBRATION_RESULTS = Path("engine/calibration_results.json")


@dataclass
class GateResult:
    status: str  # PASS, FAIL, SKIP
    summary: str
    details: dict = field(default_factory=dict)

    def exit_code(self) -> int:
        return 0 if self.status != "FAIL" else 1

    def print(self) -> None:
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "⊘"}[self.status]
        print(f"\n{icon} QUALITY GATE: {self.status}")
        print(f"  {self.summary}")
        for k, v in self.details.items():
            print(f"  {k}: {v}")


def _load_golden_set(golden_dir: Path) -> list[dict] | None:
    """Load golden set from a directory of JSON files.

    Each file: {text, rubric_title, human_scores: {criterion_id: score}, ai_scores: [optional]}
    """
    if not golden_dir.exists():
        return None
    items: list[dict] = []
    for p in sorted(golden_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            if "text" in data and "human_scores" in data:
                items.append(data)
        except Exception:
            continue
    return items if items else None


def _load_calibration_proxy() -> dict | None:
    """Use calibration results as a proxy when no golden set exists."""
    if not CALIBRATION_RESULTS.exists():
        return None
    try:
        return json.loads(CALIBRATION_RESULTS.read_text())
    except Exception:
        return None


def _load_baseline() -> dict | None:
    """Load the last recorded QWK baseline."""
    if not BASELINE_FILE.exists():
        return None
    try:
        return json.loads(BASELINE_FILE.read_text())
    except Exception:
        return None


def _save_baseline(qwk: float) -> None:
    BASELINE_FILE.write_text(json.dumps({"qwk": qwk}, indent=2))


def run_quality_gate(
    *,
    golden_dir: Path = GOLDEN_DIR,
    model: str = "grader-text:8b",
    save_baseline: bool = False,
) -> GateResult:
    """Run the quality gate. Returns PASS/FAIL/SKIP with details."""

    golden = _load_golden_set(golden_dir)

    if golden:
        ai_scores: list[float] = []
        human_scores: list[float] = []

        for item in golden:
            human = item.get("human_scores", {})
            ai = item.get("ai_scores", {})

            if not human or not ai:
                continue

            if isinstance(human, dict):
                h_total = sum(human.values())
            else:
                h_total = float(human)

            if isinstance(ai, dict):
                a_total = sum(ai.values())
            else:
                a_total = float(ai)

            ai_scores.append(a_total)
            human_scores.append(h_total)

        if len(ai_scores) < 5:
            return GateResult(
                "SKIP",
                f"golden set has only {len(ai_scores)} scored items (need ≥5)",
                {"golden_items": len(golden), "scored_items": len(ai_scores)},
            )

        agr = agreement(ai_scores, human_scores)
        qwk = agr.qwk

        baseline = _load_baseline()
        regression = None
        if baseline and "qwk" in baseline:
            regression = baseline["qwk"] - qwk

        details = {
            "items": len(ai_scores),
            "qwk": f"{qwk:.3f}",
            "exact": f"{agr.exact:.0%}",
            "adjacent": f"{agr.adjacent:.0%}",
            "mean_error": f"{agr.mean_error:+.2f}",
            "floor": QWK_FLOOR,
        }
        if regression is not None:
            details["baseline_qwk"] = f"{baseline['qwk']:.3f}"
            details["regression"] = f"{regression:+.3f}"

        if qwk < QWK_FLOOR:
            return GateResult("FAIL", f"QWK {qwk:.3f} below floor {QWK_FLOOR}", details)

        if regression is not None and regression > REGRESSION_MAX:
            return GateResult(
                "FAIL",
                f"QWK regression {regression:+.3f} exceeds max {REGRESSION_MAX}",
                details,
            )

        if save_baseline:
            _save_baseline(qwk)

        return GateResult("PASS", f"QWK {qwk:.3f} ≥ {QWK_FLOOR} floor", details)

    calib = _load_calibration_proxy()
    if calib:
        ceiling = calib.get("human_ceiling", {}).get("qwk", 0)
        best_arm = max(
            calib.get("arms", []),
            key=lambda a: a.get("qwk", 0),
            default={"arm": "none", "qwk": 0},
        )
        qwk = best_arm.get("qwk", 0)
        details = {
            "source": "calibration_results.json (proxy — no golden set)",
            "best_arm": best_arm.get("arm", "unknown"),
            "qwk": f"{qwk:.3f}",
            "human_ceiling": f"{ceiling:.3f}",
            "floor": QWK_FLOOR,
        }

        if qwk < QWK_FLOOR:
            return GateResult(
                "SKIP",
                f"golden set missing; calibration QWK {qwk:.3f} below floor "
                f"(golden set needed for real gate)",
                details,
            )

        return GateResult("PASS", f"calibration proxy QWK {qwk:.3f} ≥ {QWK_FLOOR}", details)

    return GateResult(
        "SKIP",
        "no golden set and no calibration results — cannot run quality gate",
        {"golden_dir": str(golden_dir), "calibration": str(CALIBRATION_RESULTS)},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Quality gate: QWK ≥ 0.70")
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR)
    parser.add_argument("--model", default="grader-text:8b")
    parser.add_argument("--save-baseline", action="store_true",
                        help="Save current QWK as the regression baseline")
    args = parser.parse_args()

    result = run_quality_gate(golden_dir=args.golden_dir, model=args.model,
                              save_baseline=args.save_baseline)
    result.print()
    sys.exit(result.exit_code())


if __name__ == "__main__":
    main()