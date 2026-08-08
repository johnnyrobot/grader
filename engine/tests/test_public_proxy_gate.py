import json
from pathlib import Path

from gates.public_proxy import (
    evaluate_public_proxy_fairness,
    evaluate_public_proxy_quality,
    load_public_proxy_examples,
    load_public_proxy_manifest,
)


FIXTURE_DIR = Path("fixtures/public_proxy")


def test_public_proxy_examples_load_with_tier_and_manifest():
    manifest = load_public_proxy_manifest(FIXTURE_DIR)
    assert manifest["evidence_scope"] == "mechanics_fixture"
    assert manifest["release_gate"] is False
    examples = load_public_proxy_examples(FIXTURE_DIR)
    assert {example.tier for example in examples} == {"public_proxy"}
    assert {example.source for example in examples} == {"tiny_source_a", "tiny_source_b"}
    assert all(example.split == "test" for example in examples)


def test_public_proxy_quality_blocks_tiny_fixture_by_default():
    result = evaluate_public_proxy_quality(FIXTURE_DIR)
    assert result.status == "FAIL"
    assert "mechanics-only" in result.summary
    assert result.details["release_gate"] is False


def test_public_proxy_quality_passes_on_tiny_fixture_when_allowed_for_smoke():
    result = evaluate_public_proxy_quality(FIXTURE_DIR, allow_mechanics_fixture=True)
    assert result.status == "PASS"
    assert result.details["tier"] == "public_proxy"
    assert float(result.details["qwk"]) >= 0.70
    assert "tiny_source_a" in result.details["sources"]


def test_public_proxy_quality_fails_source_floor(tmp_path):
    source = tmp_path / "fixture"
    source.mkdir()
    (source / "manifest.json").write_text((FIXTURE_DIR / "public_proxy_manifest.json").read_text())
    rows = []
    for line in (FIXTURE_DIR / "public_proxy_examples.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row["ai_scores"]["content"] == 4:
            row["ai_scores"]["content"] = 0
        rows.append(json.dumps(row))
    (source / "examples.jsonl").write_text("\n".join(rows) + "\n")
    result = evaluate_public_proxy_quality(source, allow_mechanics_fixture=True)
    assert result.status == "FAIL"
    assert "QWK" in result.summary


def test_public_proxy_fairness_blocks_tiny_fixture_by_default():
    result = evaluate_public_proxy_fairness(FIXTURE_DIR)
    assert result.status == "FAIL"
    assert "mechanics-only" in result.summary
    assert result.details["release_gate"] is False


def test_public_proxy_fairness_passes_on_controlled_fixture_when_allowed_for_smoke():
    result = evaluate_public_proxy_fairness(FIXTURE_DIR, allow_mechanics_fixture=True)
    assert result.status == "PASS"
    assert result.details["tier"] == "public_proxy"
    assert result.details["perturbations"]["informal"]["status"] == "PASS"


def test_public_proxy_fairness_fails_large_delta(tmp_path):
    source = tmp_path / "fixture"
    source.mkdir()
    (source / "manifest.json").write_text((FIXTURE_DIR / "public_proxy_manifest.json").read_text())
    bad_rows = []
    for line in (FIXTURE_DIR / "public_proxy_examples.jsonl").read_text().splitlines():
        row = json.loads(line)
        row["perturbed_scores"]["informal"]["content"] = 0
        bad_rows.append(json.dumps(row))
    (source / "examples.jsonl").write_text("\n".join(bad_rows) + "\n")
    result = evaluate_public_proxy_fairness(source, allow_mechanics_fixture=True)
    assert result.status == "FAIL"
    assert "informal" in result.summary


def test_public_proxy_fairness_fails_without_perturbation_scores(tmp_path):
    source = tmp_path / "fixture"
    source.mkdir()
    (source / "manifest.json").write_text((FIXTURE_DIR / "public_proxy_manifest.json").read_text())
    rows = []
    for line in (FIXTURE_DIR / "public_proxy_examples.jsonl").read_text().splitlines():
        row = json.loads(line)
        row.pop("perturbed_scores", None)
        rows.append(json.dumps(row))
    (source / "examples.jsonl").write_text("\n".join(rows) + "\n")

    result = evaluate_public_proxy_fairness(source, allow_mechanics_fixture=True)

    assert result.status == "FAIL"
    assert "no perturbed_scores" in result.summary


def test_public_proxy_fairness_fails_incomplete_perturbation_scores(tmp_path):
    source = tmp_path / "fixture"
    source.mkdir()
    (source / "manifest.json").write_text((FIXTURE_DIR / "public_proxy_manifest.json").read_text())
    rows = []
    for index, line in enumerate((FIXTURE_DIR / "public_proxy_examples.jsonl").read_text().splitlines()):
        row = json.loads(line)
        if index == 0:
            row["perturbed_scores"].pop("informal")
        rows.append(json.dumps(row))
    (source / "examples.jsonl").write_text("\n".join(rows) + "\n")

    result = evaluate_public_proxy_fairness(source, allow_mechanics_fixture=True)

    assert result.status == "FAIL"
    assert "incomplete perturbed_scores" in result.summary
    assert result.details["incomplete_items"] == 1


def _source_status(breakdown: dict, source: str) -> str:
    return breakdown[source]["status"]


def test_a_source_that_lost_items_to_grading_errors_is_still_held_to_its_floor(tmp_path):
    """An ungraded item must never make a gate easier to pass.

    The per-source floor is waived below SOURCE_FLOOR_MIN_ITEMS so a source that
    is small *by design* is not judged on a handful of rows. It used to count
    only the *scored* rows, so a source built with enough items that lost some
    to grading timeouts fell under the threshold and was waived — the failure
    to grade bought the exemption. Observed live: mohler_q05 scored QWK 0.599
    against a 0.60 floor and reported PASS on 19 of its 20 items.
    """
    from gates.public_proxy import (
        SOURCE_FLOOR_MIN_ITEMS,
        PublicProxyExample,
        _source_breakdown,
    )

    def example(index: int, human: float, ai: float | None) -> PublicProxyExample:
        return PublicProxyExample(
            id=f"s:{index}", tier="public_proxy", source="s", license="CC BY 4.0",
            prompt_id="p", assignment_type="t", student_text="x",
            rubric={}, human_scores={"c": human},
            ai_scores=None if ai is None else {"c": ai}, split="test",
        )

    # A source sized at the floor, graded badly, with one item ungraded.
    rows = [example(i, human=float(i % 5), ai=float((i + 3) % 5))
            for i in range(SOURCE_FLOOR_MIN_ITEMS - 1)]
    rows.append(example(99, human=4.0, ai=None))
    assert len(rows) == SOURCE_FLOOR_MIN_ITEMS

    breakdown = _source_breakdown(rows)

    assert breakdown["s"]["items"] == SOURCE_FLOOR_MIN_ITEMS - 1
    assert _source_status(breakdown, "s") == "FAIL", (
        "an ungraded item bought an exemption from the source floor"
    )


def test_a_source_that_is_genuinely_small_keeps_its_exemption(tmp_path):
    """The waiver's real purpose survives: a short source is not judged harshly."""
    from gates.public_proxy import PublicProxyExample, _source_breakdown

    def example(index: int, human: float, ai: float) -> PublicProxyExample:
        return PublicProxyExample(
            id=f"s:{index}", tier="public_proxy", source="s", license="CC BY 4.0",
            prompt_id="p", assignment_type="t", student_text="x",
            rubric={}, human_scores={"c": human}, ai_scores={"c": ai}, split="test",
        )

    rows = [example(i, human=float(i % 5), ai=float((i + 3) % 5)) for i in range(4)]

    breakdown = _source_breakdown(rows)

    assert breakdown["s"]["items"] == 4
    assert _source_status(breakdown, "s") == "PASS"
