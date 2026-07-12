import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "citations"


def test_fixtures_exist():
    fixture_file = FIXTURE_DIR / "citation_fixtures.json"
    assert fixture_file.exists(), f"fixture file not found: {fixture_file}"
    data = json.loads(fixture_file.read_text())
    assert "fixtures" in data
    assert len(data["fixtures"]) >= 20, \
        f"expected >=20 fixtures, found {len(data['fixtures'])}"


def test_fixture_format():
    fixture_file = FIXTURE_DIR / "citation_fixtures.json"
    data = json.loads(fixture_file.read_text())
    for f in data["fixtures"]:
        assert "doi" in f, f"fixture missing 'doi': {f}"
        assert "expected_resolution_layer" in f, f"fixture missing 'expected_resolution_layer': {f}"
        assert "corruption" in f, f"fixture missing 'corruption': {f}"


def test_fixture_verdicts_diverse():
    fixture_file = FIXTURE_DIR / "citation_fixtures.json"
    data = json.loads(fixture_file.read_text())
    layers = {f["expected_resolution_layer"] for f in data["fixtures"]}
    assert len(layers) >= 3, f"expected >=3 resolution layers, got {layers}"


def test_fixture_corruptions_present():
    fixture_file = FIXTURE_DIR / "citation_fixtures.json"
    data = json.loads(fixture_file.read_text())
    corruptions = {f["corruption"] for f in data["fixtures"]}
    assert "none" in corruptions, "expected 'none' corruption type"
    assert len(corruptions) >= 3, f"expected >=3 corruption types, got {corruptions}"