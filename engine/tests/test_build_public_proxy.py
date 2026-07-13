from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.build_public_proxy import row_to_example, select_mohler_rows, write_public_proxy


def _fake_mohler(rows_per_question: int = 4) -> pd.DataFrame:
    rows = []
    for q_index in range(2):
        for answer_index in range(rows_per_question):
            score = float(answer_index % 6)
            rows.append({
                "id": f"E{q_index:02d}.Q01.A{answer_index:02d}",
                "question": f"Question {q_index}?",
                "instructor_answer": f"Reference answer {q_index}",
                "student_answer": f"Student answer {answer_index} with enough content",
                "score_grader_1": score,
                "score_grader_2": score,
                "score_avg": score,
            })
    return pd.DataFrame(rows)


def test_select_mohler_rows_balances_sources():
    selected = select_mohler_rows(_fake_mohler(), sources=2, items_per_source=3, seed=7)

    assert len(selected) == 6
    assert {row.source_id for row in selected} == {"mohler_q01", "mohler_q02"}


def test_row_to_example_has_public_proxy_contract():
    selected = select_mohler_rows(_fake_mohler(), sources=1, items_per_source=1, seed=7)[0]
    example = row_to_example(selected)

    assert example["tier"] == "public_proxy"
    assert example["license"] == "CC BY 4.0"
    assert example["human_scores"]["correctness"] == example["human_raters"][0]["scores"]["correctness"]
    assert "Question:" in example["student_text"]
    assert example["rubric"]["criteria"][0]["id"] == "correctness"


def test_write_public_proxy_manifest_is_release_candidate(tmp_path):
    selected = select_mohler_rows(_fake_mohler(), sources=2, items_per_source=3, seed=7)

    manifest_path, examples_path = write_public_proxy(
        selected,
        tmp_path,
        grade=False,
        grade_perturbations=False,
        model="grader-text:8b",
        seed=7,
    )

    manifest = json.loads(manifest_path.read_text())
    examples = [json.loads(line) for line in examples_path.read_text().splitlines()]
    assert manifest["tier"] == "public_proxy"
    assert manifest["evidence_scope"] == "open_human_scored"
    assert manifest["release_gate"] is True
    assert manifest["graded"] is False
    assert len(manifest["sources"]) == 2
    assert len(examples) == 6
    assert all("ai_scores" not in example for example in examples)
