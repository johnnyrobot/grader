#!/usr/bin/env python3
"""Build gitignored public-proxy evaluation sets from open human-scored data.

The committed ``fixtures/public_proxy`` directory is intentionally tiny and mechanics-only.
This script creates a larger local evidence directory from Mohler ASAG (CC BY 4.0) so the
release gates can be run against real human-scored answers without committing the corpus.

Default output:
    artifacts/public_proxy/mohler_asag/{manifest.json,examples.jsonl}

To create unscored evidence rows:
    uv run python scripts/build_public_proxy.py mohler-asag

To populate ``ai_scores`` with the local Grader model:
    uv run python scripts/build_public_proxy.py mohler-asag --grade

To also populate fairness perturbation scores:
    uv run python scripts/build_public_proxy.py mohler-asag --grade --grade-perturbations
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
sys.path.insert(0, str(ENGINE))

MOHLER_URL = "https://huggingface.co/datasets/nkazi/MohlerASAG/resolve/main/data/cleaned-oe-00001.parquet"
MOHLER_DATASET_URL = "https://huggingface.co/datasets/nkazi/MohlerASAG"
MOHLER_LOCAL = ROOT / "fixtures" / "corpora" / "mohler_asag.parquet"
DEFAULT_OUT = ROOT / "artifacts" / "public_proxy" / "mohler_asag"


@dataclass(frozen=True)
class SelectedMohlerRow:
    source_id: str
    row: pd.Series


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("<STOP>", "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _source_hash(rows: list[SelectedMohlerRow]) -> str:
    digest = hashlib.sha256()
    for selected in rows:
        row = selected.row
        digest.update(str(row.get("id", "")).encode())
        digest.update(b"\0")
        digest.update(_clean_text(row.get("question", "")).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def load_mohler(local_path: Path = MOHLER_LOCAL) -> pd.DataFrame:
    """Load Mohler ASAG from local cache if present, otherwise from Hugging Face parquet."""
    if local_path.exists():
        return pd.read_parquet(local_path)
    return pd.read_parquet(MOHLER_URL)


def select_mohler_rows(
    df: pd.DataFrame,
    *,
    sources: int,
    items_per_source: int,
    seed: int,
) -> list[SelectedMohlerRow]:
    """Select balanced test rows from the largest Mohler questions.

    Each selected question becomes a public-proxy "source" so source-level QWK/fairness checks
    have enough examples to be meaningful.
    """
    needed_columns = {"id", "question", "instructor_answer", "student_answer", "score_grader_1", "score_grader_2", "score_avg"}
    missing = needed_columns.difference(df.columns)
    if missing:
        raise ValueError(f"Mohler data missing required columns: {sorted(missing)}")

    clean = df.dropna(subset=list(needed_columns)).copy()
    clean = clean[clean["student_answer"].astype(str).str.replace("<STOP>", "", regex=False).str.len() > 10]
    counts = clean.groupby("question").size().sort_values(ascending=False)
    questions = [question for question, count in counts.items() if count >= items_per_source]
    if len(questions) < sources:
        raise ValueError(
            f"only {len(questions)} Mohler questions have >= {items_per_source} usable rows; need {sources}"
        )

    selected: list[SelectedMohlerRow] = []
    for source_index, question in enumerate(questions[:sources], start=1):
        source_id = f"mohler_q{source_index:02d}"
        rows = clean[clean["question"] == question].sample(
            n=items_per_source,
            random_state=seed + source_index,
        )
        selected.extend(SelectedMohlerRow(source_id=source_id, row=row) for _, row in rows.iterrows())
    return selected


def _rubric() -> dict[str, Any]:
    return {
        "title": "Mohler ASAG Short Answer Correctness",
        "criteria": [
            {
                "id": "correctness",
                "title": "Correctness & Completeness",
                "description": "How fully and accurately the student's answer captures the reference answer.",
                "max_points": 5,
                "criterion_type": "objective",
                "levels": [
                    {"score": 5, "label": "Fully correct", "descriptor": "Complete and accurate."},
                    {"score": 4, "label": "Mostly correct", "descriptor": "Substantively correct with minor omissions."},
                    {"score": 3, "label": "Partially correct", "descriptor": "Core idea present but incomplete."},
                    {"score": 2, "label": "Minimally correct", "descriptor": "Touches the topic but is largely incomplete."},
                    {"score": 1, "label": "Barely relevant", "descriptor": "Little correct content, but on-topic."},
                    {"score": 0, "label": "Incorrect", "descriptor": "Wrong, empty, or unrelated."},
                ],
            }
        ],
    }


def row_to_example(selected: SelectedMohlerRow) -> dict[str, Any]:
    row = selected.row
    question = _clean_text(row["question"])
    answer = _clean_text(row["student_answer"])
    reference = _clean_text(row["instructor_answer"])
    score_avg = float(row["score_avg"])
    row_id = str(row["id"])
    return {
        "id": f"{selected.source_id}:{row_id}",
        "tier": "public_proxy",
        "source": selected.source_id,
        "license": "CC BY 4.0",
        "prompt_id": question,
        "assignment_type": "computer_science_short_answer",
        "student_text": f"Question: {question}\n\nStudent answer: {answer}",
        "reference_answer": reference,
        "rubric": _rubric(),
        "human_scores": {"correctness": score_avg},
        "human_raters": [
            {"id": "grader_1", "scores": {"correctness": float(row["score_grader_1"])}},
            {"id": "grader_2", "scores": {"correctness": float(row["score_grader_2"])}},
        ],
        "split": "test",
    }


def _grade_text(example: dict[str, Any], text: str, model: str) -> dict[str, float]:
    from grader.agent import grade
    from grader.schema import Rubric

    rubric = Rubric.model_validate(example["rubric"])
    run = grade(
        rubric,
        text,
        model_name=model,
        mode="tool",
        materials=f"Reference answer: {example['reference_answer']}",
    )
    if not run.ok:
        raise RuntimeError(run.error or "grading failed")
    return {score.criterion_id: float(score.score) for score in run.draft.criterion_scores}


def _write_examples(path: Path, examples: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(example, sort_keys=True) for example in examples) + "\n")


def _load_existing_examples(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    existing: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        existing[str(row["id"])] = row
    return existing


def populate_model_scores(
    examples: list[dict[str, Any]],
    *,
    model: str,
    grade_perturbations: bool,
    checkpoint_path: Path | None = None,
    continue_on_error: bool = True,
) -> None:
    from gates.bias import PERTURBATIONS, perturb

    for index, example in enumerate(examples, start=1):
        if example.get("ai_scores") and (not grade_perturbations or example.get("perturbed_scores")):
            continue
        print(f"[{index:03d}/{len(examples):03d}] grading {example['id']}")
        try:
            if not example.get("ai_scores"):
                example["ai_scores"] = _grade_text(example, example["student_text"], model)
            if grade_perturbations and not example.get("perturbed_scores"):
                example["perturbed_scores"] = {}
                for name in PERTURBATIONS:
                    text = perturb(example["student_text"], name)
                    example["perturbed_scores"][name] = _grade_text(example, text, model)
            example.pop("grade_error", None)
        except Exception as error:
            example["grade_error"] = str(error)[:500]
            print(f"  WARN: {example['id']} not scored: {example['grade_error']}")
            if not continue_on_error:
                raise
        finally:
            if checkpoint_path is not None:
                _write_examples(checkpoint_path, examples)


def write_public_proxy(
    selected: list[SelectedMohlerRow],
    out_dir: Path,
    *,
    grade: bool,
    grade_perturbations: bool,
    model: str,
    seed: int,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    examples = [row_to_example(row) for row in selected]
    examples_path = out_dir / "examples.jsonl"
    existing_examples = _load_existing_examples(examples_path) if grade else {}
    if existing_examples:
        for index, example in enumerate(examples):
            existing = existing_examples.get(example["id"])
            if existing:
                examples[index] = {**example, **{
                    key: existing[key]
                    for key in ("ai_scores", "perturbed_scores", "grade_error")
                    if key in existing
                }}

    by_source: dict[str, list[SelectedMohlerRow]] = {}
    for row in selected:
        by_source.setdefault(row.source_id, []).append(row)

    manifest = {
        "tier": "public_proxy",
        "name": "mohler-asag-public-proxy",
        "evidence_scope": "open_human_scored",
        "release_gate": True,
        "license": "CC BY 4.0",
        "retrieved_at": datetime.now(timezone.utc).date().isoformat(),
        "adapter_version": "mohler-asag-v1",
        "dataset_url": MOHLER_DATASET_URL,
        "data_url": MOHLER_URL,
        "seed": seed,
        "model": model if grade else None,
        "graded": grade,
        "perturbations_graded": grade_perturbations,
        "sources": [
            {
                "id": source_id,
                "url": MOHLER_DATASET_URL,
                "license": "CC BY 4.0",
                "sha256": _source_hash(rows),
                "question": _clean_text(rows[0].row["question"]),
                "items": len(rows),
            }
            for source_id, rows in sorted(by_source.items())
        ],
    }

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    _write_examples(examples_path, examples)
    if grade:
        populate_model_scores(
            examples,
            model=model,
            grade_perturbations=grade_perturbations,
            checkpoint_path=examples_path,
        )
        manifest["scored_items"] = sum(1 for example in examples if example.get("ai_scores"))
        manifest["grade_errors"] = sum(1 for example in examples if example.get("grade_error"))
        manifest["perturbed_items"] = sum(1 for example in examples if example.get("perturbed_scores"))
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest_path, examples_path


def build_mohler_asag(args: argparse.Namespace) -> None:
    if args.grade_perturbations and not args.grade:
        raise SystemExit("--grade-perturbations requires --grade")
    df = load_mohler(args.local_parquet)
    selected = select_mohler_rows(
        df,
        sources=args.sources,
        items_per_source=args.items_per_source,
        seed=args.seed,
    )
    manifest_path, examples_path = write_public_proxy(
        selected,
        args.out,
        grade=args.grade,
        grade_perturbations=args.grade_perturbations,
        model=args.model,
        seed=args.seed,
    )
    print(f"wrote {manifest_path}")
    print(f"wrote {examples_path}")
    print(f"examples={len(selected)} sources={args.sources} graded={args.grade} perturbations={args.grade_perturbations}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build local public-proxy evidence datasets")
    sub = parser.add_subparsers(dest="command", required=True)
    mohler = sub.add_parser("mohler-asag", help="Build a Mohler ASAG public-proxy directory")
    mohler.add_argument("--out", type=Path, default=DEFAULT_OUT)
    mohler.add_argument("--local-parquet", type=Path, default=MOHLER_LOCAL)
    mohler.add_argument("--sources", type=int, default=2, help="Number of Mohler questions to include")
    mohler.add_argument("--items-per-source", type=int, default=20)
    mohler.add_argument("--seed", type=int, default=20260713)
    mohler.add_argument("--grade", action="store_true", help="Populate ai_scores with the local Grader model")
    mohler.add_argument("--grade-perturbations", action="store_true", help="Also populate perturbed_scores for fairness")
    mohler.add_argument("--model", default="grader-text:8b")
    mohler.set_defaults(func=build_mohler_asag)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
