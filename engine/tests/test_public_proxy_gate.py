import json
from pathlib import Path

from gates.public_proxy import evaluate_public_proxy_quality, load_public_proxy_examples


FIXTURE_DIR = Path("fixtures/public_proxy")


def test_public_proxy_examples_load_with_tier_and_manifest():
    examples = load_public_proxy_examples(FIXTURE_DIR)
    assert {example.tier for example in examples} == {"public_proxy"}
    assert {example.source for example in examples} == {"tiny_source_a", "tiny_source_b"}
    assert all(example.split == "test" for example in examples)


def test_public_proxy_quality_passes_on_tiny_fixture():
    result = evaluate_public_proxy_quality(FIXTURE_DIR)
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
    result = evaluate_public_proxy_quality(source)
    assert result.status == "FAIL"
    assert "QWK" in result.summary
