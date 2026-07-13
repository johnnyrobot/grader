import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.frontend_evidence import (
    REQUIRED_CONTROL_LABELS,
    REQUIRED_VISUAL_SCREENS,
    validate_visual_sweep,
)


def _write_visual_sweep(tmp_path: Path, *, labels: list[str] | None = None) -> Path:
    labels = labels or REQUIRED_CONTROL_LABELS
    screens = []
    for index, screen_name in enumerate(REQUIRED_VISUAL_SCREENS):
        screenshot = tmp_path / f"{screen_name}.png"
        screenshot.write_bytes(b"fake png")
        screen_labels = labels if index == 0 else ["Continue"]
        screens.append({
            "name": screen_name,
            "screenshot": str(screenshot),
            "control_count": len(screen_labels),
            "visible_control_count": len(screen_labels),
            "controls": [
                {
                    "index": control_index,
                    "tag": "button",
                    "type": "button",
                    "label": label,
                    "visible": True,
                }
                for control_index, label in enumerate(screen_labels)
            ],
        })
    path = tmp_path / "visual-sweep.json"
    path.write_text(json.dumps({
        "status": "PASS",
        "screen_count": len(REQUIRED_VISUAL_SCREENS),
        "total_visible_controls": sum(screen["visible_control_count"] for screen in screens),
        "screens": screens,
    }))
    return path


def test_validate_visual_sweep_accepts_complete_instructor_inventory(tmp_path):
    path = _write_visual_sweep(tmp_path)

    result = validate_visual_sweep(path)

    assert result["status"] == "PASS"
    assert result["failures"] == []
    assert result["screen_count"] == len(REQUIRED_VISUAL_SCREENS)
    assert result["total_visible_controls"] > len(REQUIRED_CONTROL_LABELS)


def test_validate_visual_sweep_fails_when_artifact_missing(tmp_path):
    result = validate_visual_sweep(tmp_path / "missing.json")

    assert result["status"] == "FAIL"
    assert "not produced" in result["failures"][0]


def test_validate_visual_sweep_fails_when_required_screen_missing(tmp_path):
    path = _write_visual_sweep(tmp_path)
    data = json.loads(path.read_text())
    data["screens"] = data["screens"][:-1]
    data["screen_count"] = len(data["screens"])
    path.write_text(json.dumps(data))

    result = validate_visual_sweep(path)

    assert result["status"] == "FAIL"
    assert any("screen sequence mismatch" in failure for failure in result["failures"])


def test_validate_visual_sweep_fails_when_required_control_missing(tmp_path):
    labels = [label for label in REQUIRED_CONTROL_LABELS if label != "Restart local engine"]
    path = _write_visual_sweep(tmp_path, labels=labels)

    result = validate_visual_sweep(path)

    assert result["status"] == "FAIL"
    assert any("Restart local engine" in failure for failure in result["failures"])


def test_validate_visual_sweep_fails_when_screenshot_missing(tmp_path):
    path = _write_visual_sweep(tmp_path)
    data = json.loads(path.read_text())
    Path(data["screens"][0]["screenshot"]).unlink()
    path.write_text(json.dumps(data))

    result = validate_visual_sweep(path)

    assert result["status"] == "FAIL"
    assert any("missing screenshots" in failure for failure in result["failures"])
