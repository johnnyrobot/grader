from __future__ import annotations

import json
import plistlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.computer_use_sweep_evidence import REQUIRED_TEXT_MARKERS, validate_sweep


def _write_capture(root: Path, stem: str, text: str) -> None:
    (root / f"{stem}.ax.txt").write_text(text)
    (root / f"{stem}.png").write_text("fake png")


def _write_app(root: Path, *, version: str = "0.1.0", bundle_id: str = "com.grader.app") -> Path:
    app = root / "Grader.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": bundle_id,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
    }))
    (contents / "MacOS").mkdir()
    (contents / "MacOS" / "Grader").write_text("binary")
    return app


def test_validate_sweep_passes_complete_clean_capture_set(tmp_path):
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    all_markers = "\n".join(REQUIRED_TEXT_MARKERS)
    _write_capture(capture_dir, "01-complete", all_markers)
    (capture_dir / "computer-use-signed-local-evidence.json").write_text(json.dumps({"status": "PASS"}))

    evidence = validate_sweep(capture_dir=capture_dir, min_captures=1)

    assert evidence["status"] == "PASS"
    assert evidence["failures"] == []
    assert evidence["details"]["accessibility_capture_count"] == 1


def test_validate_sweep_records_matching_app_bundle_metadata(tmp_path):
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    all_markers = "\n".join(REQUIRED_TEXT_MARKERS)
    _write_capture(capture_dir, "01-complete", all_markers)
    app = _write_app(tmp_path, version="0.1.1")

    evidence = validate_sweep(
        capture_dir=capture_dir,
        min_captures=1,
        app_path=app,
        expected_version="0.1.1",
    )

    assert evidence["status"] == "PASS"
    assert evidence["details"]["app"]["bundle_id"] == "com.grader.app"
    assert evidence["details"]["app"]["short_version"] == "0.1.1"
    assert evidence["details"]["app"]["bundle_version"] == "0.1.1"
    assert evidence["details"]["app"]["sha256_tree"]


def test_validate_sweep_fails_wrong_app_version(tmp_path):
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    all_markers = "\n".join(REQUIRED_TEXT_MARKERS)
    _write_capture(capture_dir, "01-complete", all_markers)
    app = _write_app(tmp_path, version="0.1.0")

    evidence = validate_sweep(
        capture_dir=capture_dir,
        min_captures=1,
        app_path=app,
        expected_version="0.1.1",
    )

    assert evidence["status"] == "FAIL_COMPUTER_USE_SWEEP"
    assert any("app version mismatch" in failure for failure in evidence["failures"])


def test_validate_sweep_fails_approved_and_unsaved_contradiction(tmp_path):
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    all_markers = "\n".join(REQUIRED_TEXT_MARKERS)
    _write_capture(
        capture_dir,
        "01-stale",
        all_markers + "\nYour changes are approved.\nTeacher changes not saved yet.",
    )

    evidence = validate_sweep(capture_dir=capture_dir, min_captures=1)

    assert evidence["status"] == "FAIL_COMPUTER_USE_SWEEP"
    assert "stale or contradictory review approval state found" in evidence["failures"]
    assert evidence["details"]["stale_state_failures"][0]["pattern"] == "approved_and_unsaved"


def test_validate_sweep_fails_noop_score_update_message(tmp_path):
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    all_markers = "\n".join(REQUIRED_TEXT_MARKERS)
    _write_capture(capture_dir, "01-noop", all_markers + "\nScore updated from 15 to 15.")

    evidence = validate_sweep(capture_dir=capture_dir, min_captures=1)

    assert evidence["status"] == "FAIL_COMPUTER_USE_SWEEP"
    assert evidence["details"]["stale_state_failures"][0]["pattern"] == "noop_score_update"


def test_validate_sweep_fails_existing_non_pass_summary(tmp_path):
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    all_markers = "\n".join(REQUIRED_TEXT_MARKERS)
    _write_capture(capture_dir, "01-complete", all_markers)
    (capture_dir / "computer-use-signed-local-evidence.json").write_text(json.dumps({"status": "FAIL_STALE_ARTIFACT"}))

    evidence = validate_sweep(capture_dir=capture_dir, min_captures=1)

    assert evidence["status"] == "FAIL_COMPUTER_USE_SWEEP"
    assert "existing sweep summary status is FAIL_STALE_ARTIFACT" in evidence["failures"]
