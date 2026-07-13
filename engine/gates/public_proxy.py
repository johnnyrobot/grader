"""Public-proxy release gate helpers.

Public proxy examples are open, human-scored data used to validate the grading
machinery without private student records. They are not teacher-local
calibration evidence, and synthetic fixtures are only mechanics tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grader.metrics import agreement, human_ceiling, quadratic_weighted_kappa

QWK_FLOOR = 0.70
SOURCE_QWK_FLOOR = 0.60
SOURCE_FLOOR_MIN_ITEMS = 20
NORMALIZED_MEAN_ERROR_MAX = 0.50
REGRESSION_MAX = 0.03


@dataclass
class GateResult:
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def exit_code(self) -> int:
        return 0 if self.status != "FAIL" else 1

    def print(self) -> None:
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "⊘"}[self.status]
        print(f"\n{icon} PUBLIC PROXY QUALITY GATE: {self.status}")
        print(f"  {self.summary}")
        for key, value in self.details.items():
            print(f"  {key}: {value}")


@dataclass(frozen=True)
class PublicProxyExample:
    id: str
    tier: str
    source: str
    license: str
    prompt_id: str
    assignment_type: str
    student_text: str
    rubric: dict[str, Any]
    human_scores: dict[str, float] | float
    ai_scores: dict[str, float] | float | None
    split: str
    human_raters: list[dict[str, Any]] = field(default_factory=list)
    perturbed_scores: dict[str, dict[str, float] | float] = field(default_factory=dict)

    @property
    def human_total(self) -> float:
        return _score_total(self.human_scores)

    @property
    def ai_total(self) -> float | None:
        if self.ai_scores is None:
            return None
        return _score_total(self.ai_scores)


def _score_total(scores: dict[str, float] | float | int) -> float:
    if isinstance(scores, dict):
        return float(sum(float(value) for value in scores.values()))
    return float(scores)


def _manifest_path(path: Path) -> Path:
    for name in ("manifest.json", "public_proxy_manifest.json"):
        candidate = path / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"no public proxy manifest found in {path}")


def _examples_path(path: Path) -> Path:
    for name in ("examples.jsonl", "public_proxy_examples.jsonl"):
        candidate = path / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"no public proxy examples JSONL found in {path}")


def load_public_proxy_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(_manifest_path(path).read_text())
    if manifest.get("tier") != "public_proxy":
        raise ValueError("public proxy manifest must declare tier='public_proxy'")
    return manifest


def load_public_proxy_examples(path: Path) -> list[PublicProxyExample]:
    """Load normalized public-proxy examples from a directory."""
    manifest = load_public_proxy_manifest(path)
    examples: list[PublicProxyExample] = []
    for line_no, line in enumerate(_examples_path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        tier = row.get("tier", manifest.get("tier"))
        if tier != "public_proxy":
            raise ValueError(f"line {line_no}: expected tier public_proxy, got {tier!r}")
        if not row.get("human_scores"):
            raise ValueError(f"line {line_no}: missing human_scores")
        if not row.get("student_text"):
            raise ValueError(f"line {line_no}: missing student_text")
        examples.append(
            PublicProxyExample(
                id=str(row["id"]),
                tier=tier,
                source=str(row["source"]),
                license=str(row.get("license", manifest.get("license", ""))),
                prompt_id=str(row.get("prompt_id", "")),
                assignment_type=str(row.get("assignment_type", "")),
                student_text=str(row["student_text"]),
                rubric=dict(row.get("rubric", {})),
                human_scores=row["human_scores"],
                ai_scores=row.get("ai_scores"),
                split=str(row.get("split", "test")),
                human_raters=list(row.get("human_raters", [])),
                perturbed_scores=dict(row.get("perturbed_scores", {})),
            )
        )
    return examples


def _source_breakdown(examples: list[PublicProxyExample]) -> dict[str, dict[str, Any]]:
    sources: dict[str, list[PublicProxyExample]] = {}
    for example in examples:
        sources.setdefault(example.source, []).append(example)

    breakdown: dict[str, dict[str, Any]] = {}
    for source, rows in sorted(sources.items()):
        scored = [row for row in rows if row.ai_total is not None]
        if len(scored) < 2:
            breakdown[source] = {"items": len(scored), "status": "insufficient"}
            continue
        ai_scores = [row.ai_total for row in scored if row.ai_total is not None]
        human_scores = [row.human_total for row in scored]
        agr = agreement(ai_scores, human_scores)
        breakdown[source] = {
            "items": len(scored),
            "qwk": f"{agr.qwk:.3f}",
            "exact": f"{agr.exact:.0%}",
            "adjacent": f"{agr.adjacent:.0%}",
            "mean_error": f"{agr.mean_error:+.2f}",
            "status": "PASS"
            if len(scored) < SOURCE_FLOOR_MIN_ITEMS or agr.qwk >= SOURCE_QWK_FLOOR
            else "FAIL",
        }
    return breakdown


def _human_ceiling(examples: list[PublicProxyExample]) -> float | None:
    rater_a: list[float] = []
    rater_b: list[float] = []
    for example in examples:
        if len(example.human_raters) < 2:
            continue
        first = example.human_raters[0].get("scores")
        second = example.human_raters[1].get("scores")
        if first is None or second is None:
            continue
        rater_a.append(_score_total(first))
        rater_b.append(_score_total(second))
    if len(rater_a) < 2:
        return None
    return human_ceiling(rater_a, rater_b).qwk


def _load_baseline_qwk(baseline_path: Path | None) -> float | None:
    if baseline_path is None or not baseline_path.exists():
        return None
    try:
        data = json.loads(baseline_path.read_text())
    except json.JSONDecodeError:
        return None
    value = data.get("public_proxy_qwk", data.get("qwk"))
    return float(value) if value is not None else None


def evaluate_public_proxy_quality(
    path: Path,
    baseline_path: Path | None = None,
) -> GateResult:
    examples = [example for example in load_public_proxy_examples(path) if example.split == "test"]
    scored = [example for example in examples if example.ai_total is not None]
    if len(scored) < 5:
        return GateResult(
            "SKIP",
            f"public proxy set has only {len(scored)} scored test items (need >=5)",
            {"tier": "public_proxy", "items": len(scored)},
        )

    ai_scores = [example.ai_total for example in scored if example.ai_total is not None]
    human_scores = [example.human_total for example in scored]
    agr = agreement(ai_scores, human_scores)
    source_details = _source_breakdown(scored)
    ceiling = _human_ceiling(scored)
    floor = QWK_FLOOR
    if ceiling is not None and ceiling < QWK_FLOOR:
        floor = ceiling * 0.95
    assignment_types = sorted({example.assignment_type for example in scored})
    baseline_qwk = _load_baseline_qwk(baseline_path)
    regression = baseline_qwk - agr.qwk if baseline_qwk is not None else None

    details: dict[str, Any] = {
        "tier": "public_proxy",
        "items": len(scored),
        "assignment_types": assignment_types,
        "qwk": f"{agr.qwk:.3f}",
        "floor": f"{floor:.3f}",
        "exact": f"{agr.exact:.0%}",
        "adjacent": f"{agr.adjacent:.0%}",
        "mean_error": f"{agr.mean_error:+.2f}",
        "sources": source_details,
    }
    if ceiling is not None:
        details["human_ceiling"] = f"{ceiling:.3f}"
    if regression is not None:
        details["baseline_qwk"] = f"{baseline_qwk:.3f}"
        details["regression"] = f"{regression:+.3f}"

    failures: list[str] = []
    if agr.qwk < floor:
        failures.append(f"QWK {agr.qwk:.3f} below floor {floor:.3f}")
    if abs(agr.mean_error) > NORMALIZED_MEAN_ERROR_MAX:
        failures.append(
            f"mean_error {agr.mean_error:+.2f} outside +/-{NORMALIZED_MEAN_ERROR_MAX:.2f}"
        )
    failing_sources = [
        source for source, result in source_details.items() if result.get("status") == "FAIL"
    ]
    if failing_sources:
        failures.append(f"source QWK below floor: {', '.join(failing_sources)}")
    if regression is not None and regression > REGRESSION_MAX:
        failures.append(f"QWK regression {regression:+.3f} exceeds {REGRESSION_MAX:.3f}")

    if failures:
        return GateResult("FAIL", "; ".join(failures), details)
    return GateResult("PASS", f"QWK {agr.qwk:.3f} >= floor {floor:.3f}", details)
