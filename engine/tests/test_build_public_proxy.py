from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2]))

import scripts.build_public_proxy as builder
from scripts.build_public_proxy import (
    GradeTimeoutError,
    _call_with_timeout,
    populate_model_scores,
    row_to_example,
    select_mohler_anchor_rows,
    select_mohler_rows,
    write_public_proxy,
)


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


def test_select_mohler_rows_prefers_score_diversity_over_size():
    rows = []
    for answer_index in range(10):
        rows.append({
            "id": f"HIGH.A{answer_index:02d}",
            "question": "Large high-score-only question?",
            "instructor_answer": "Reference high",
            "student_answer": f"High scoring answer {answer_index} with enough content",
            "score_grader_1": 5.0,
            "score_grader_2": 5.0,
            "score_avg": 5.0,
        })
    for answer_index, score in enumerate([0, 1, 2, 3, 4, 5]):
        rows.append({
            "id": f"DIVERSE.A{answer_index:02d}",
            "question": "Smaller diverse question?",
            "instructor_answer": "Reference diverse",
            "student_answer": f"Diverse answer {answer_index} with enough content",
            "score_grader_1": float(score),
            "score_grader_2": float(score),
            "score_avg": float(score),
        })
    selected = select_mohler_rows(pd.DataFrame(rows), sources=1, items_per_source=6, seed=7)

    assert {row.row["question"] for row in selected} == {"Smaller diverse question?"}


def test_select_mohler_rows_balances_sources():
    selected = select_mohler_rows(_fake_mohler(rows_per_question=8), sources=2, items_per_source=6, seed=7)

    assert len(selected) == 12
    assert {row.source_id for row in selected} == {"mohler_q01", "mohler_q02"}
    by_source = {
        source: sorted({round(float(row.row["score_avg"])) for row in selected if row.source_id == source})
        for source in {"mohler_q01", "mohler_q02"}
    }
    assert by_source == {"mohler_q01": [0, 1, 2, 3, 4, 5], "mohler_q02": [0, 1, 2, 3, 4, 5]}


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


def test_select_mohler_anchor_rows_excludes_test_rows_and_balances_sources():
    df = _fake_mohler(rows_per_question=8)
    selected = select_mohler_rows(df, sources=2, items_per_source=3, seed=7)
    anchors = select_mohler_anchor_rows(df, selected, exemplars_per_source=4, seed=7)
    selected_ids = {str(row.row["id"]) for row in selected}

    assert set(anchors) == {"mohler_q01", "mohler_q02"}
    assert all(len(rows) == 4 for rows in anchors.values())
    assert all(str(row.row["id"]) not in selected_ids for rows in anchors.values() for row in rows)


def test_write_public_proxy_records_anchor_settings(tmp_path):
    df = _fake_mohler(rows_per_question=8)
    selected = select_mohler_rows(df, sources=2, items_per_source=3, seed=7)
    anchors = select_mohler_anchor_rows(df, selected, exemplars_per_source=4, seed=7)

    manifest_path, _ = write_public_proxy(
        selected,
        tmp_path,
        grade=False,
        grade_perturbations=False,
        model="grader-text:8b",
        seed=7,
        anchor_rows_by_source=anchors,
        anchor_k=2,
        force_regrade=False,
    )

    manifest = json.loads(manifest_path.read_text())
    assert manifest["anchor_k"] == 2
    assert manifest["anchor_exemplars_per_source"] == {"mohler_q01": 4, "mohler_q02": 4}


def test_write_public_proxy_force_perturbations_preserves_ai_scores(tmp_path):
    selected = select_mohler_rows(_fake_mohler(), sources=1, items_per_source=1, seed=7)
    _, examples_path = write_public_proxy(
        selected,
        tmp_path,
        grade=False,
        grade_perturbations=False,
        model="grader-text:8b",
        seed=7,
    )
    example = json.loads(examples_path.read_text().splitlines()[0])
    example["ai_scores"] = {"correctness": 2.0}
    example["perturbed_scores"] = {"verbose": {"correctness": 1.0}}
    example["grade_error"] = "old timeout"
    examples_path.write_text(json.dumps(example) + "\n")

    _, examples_path = write_public_proxy(
        selected,
        tmp_path,
        grade=True,
        grade_perturbations=False,
        model="grader-text:8b",
        seed=7,
        force_perturbations=True,
    )

    refreshed = json.loads(examples_path.read_text().splitlines()[0])
    assert refreshed["ai_scores"] == {"correctness": 2.0}
    assert "perturbed_scores" not in refreshed
    assert "grade_error" not in refreshed


def test_populate_model_scores_resumes_partial_perturbations(monkeypatch):
    from gates.bias import PERTURBATIONS

    example = row_to_example(select_mohler_rows(_fake_mohler(), sources=1, items_per_source=1, seed=7)[0])
    first_name = next(iter(PERTURBATIONS))
    example["ai_scores"] = {"correctness": 2.0}
    example["perturbed_scores"] = {first_name: {"correctness": 2.0}}
    calls: list[str] = []

    def fake_grade_text(_example, text, _model, *, anchors=""):
        calls.append(text)
        return {"correctness": 2.0}

    monkeypatch.setattr(builder, "_grade_text", fake_grade_text)

    populate_model_scores(
        [example],
        model="grader-text:8b",
        grade_perturbations=True,
    )

    assert set(example["perturbed_scores"]) == set(PERTURBATIONS)
    assert len(calls) == len(PERTURBATIONS) - 1


def test_call_with_timeout_can_be_disabled():
    assert _call_with_timeout(lambda: "ok", seconds=0, label="unit") == "ok"


def test_call_with_timeout_raises_for_slow_call():
    import time

    try:
        _call_with_timeout(lambda: time.sleep(2), seconds=1, label="slow unit")
    except GradeTimeoutError as error:
        assert "slow unit exceeded 1s" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected timeout")
