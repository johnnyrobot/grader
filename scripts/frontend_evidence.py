#!/usr/bin/env python3
"""Collect frontend release evidence for the Grader desktop UI.

The release report consumes a JSON artifact instead of trying to infer UI status from stale docs.
This script runs the same commands an instructor-facing release needs:

  - Vitest component tests
  - TypeScript/Vite production build
  - Playwright E2E suite with a managed Vite dev server
  - visual-sweep metadata written by the Playwright suite
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_VISUAL_SCREENS = [
    "01-onboarding-welcome",
    "02-onboarding-appearance",
    "03-onboarding-readiness",
    "04-onboarding-connect",
    "05-queue-all",
    "06-queue-controls",
    "07-review-draft",
    "08-review-edited",
    "09-review-approved",
    "10-settings-general",
    "11-settings-appearance",
    "12-settings-model-defaults",
    "13-settings-rubrics",
    "14-model-manager",
    "15-responsive-200-percent",
    "16-engine-recovery-alert",
]

REQUIRED_CONTROL_LABELS = [
    "Continue",
    "Back",
    "Skip appearance choices",
    "Start grading",
    "Settings",
    "Local models",
    "Refresh queue",
    "Grade submissions",
    "Assignment",
    "All submissions",
    "Needs review",
    "Standard review",
    "Confident drafts",
    "Student 1042",
    "Student 1177",
    "Student 1204",
    "Approve AI draft",
    "Save changes and approve",
    "Feedback for Student 1042",
    "Canvas URL",
    "Canvas token",
    "Canvas attachment directory",
    "High-confidence threshold",
    "Needs-review threshold",
    "General",
    "Appearance",
    "Model defaults",
    "Rubrics",
    "Save general settings",
    "Light",
    "Dark",
    "Comfortable",
    "Spacious",
    "Reduce interface motion",
    "Restore appearance defaults",
    "Text grading model",
    "Vision and handwriting model",
    "Embedding model",
    "Save model defaults",
    "Load into editor",
    "Rubric title",
    "Rubric text",
    "Save rubric",
    "Refresh status",
    "Install handwriting model",
    "Install exemplar finder model",
    "Restart local engine",
]


def _run(command: list[str], *, cwd: Path, timeout: int = 120) -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    return {
        "name": command[1] if len(command) > 1 else command[0],
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "command": command,
        "cwd": str(cwd),
        "duration_seconds": round(time.monotonic() - started, 2),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _wait_for_port(port: int, *, timeout: int = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.25)
    raise TimeoutError(f"server on 127.0.0.1:{port} did not become ready")


def _run_e2e(*, app_dir: Path, port: int = 5173) -> dict[str, Any]:
    started = time.monotonic()
    server = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1"],
        cwd=app_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port)
        result = _run(["npm", "run", "test:e2e"], cwd=app_dir, timeout=180)
    except Exception as exc:
        result = {
            "name": "frontend_e2e",
            "status": "FAIL",
            "returncode": 1,
            "command": ["npm", "run", "test:e2e"],
            "cwd": str(app_dir),
            "duration_seconds": round(time.monotonic() - started, 2),
            "stdout": "",
            "stderr": str(exc),
        }
    finally:
        server.terminate()
        try:
            out, err = server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            out, err = server.communicate(timeout=5)
    result["name"] = "frontend_e2e"
    result["server_stdout"] = out
    result["server_stderr"] = err
    result["duration_seconds"] = round(time.monotonic() - started, 2)
    return result


def _parse_passed_tests(output: str) -> int | None:
    matches = re.findall(r"\bTests\s+(\d+)\s+passed\b|\b(\d+)\s+passed\s+\(", output)
    if not matches:
        return None
    last = matches[-1]
    return int(last[0] or last[1])


def _label_matches(labels: list[str], required: str) -> bool:
    needle = required.lower()
    return any(needle in label.lower() for label in labels)


def validate_visual_sweep(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "name": "visual_sweep_validation",
            "status": "FAIL",
            "path": str(path),
            "failures": ["visual sweep JSON was not produced by the Playwright run"],
        }

    try:
        visual = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {
            "name": "visual_sweep_validation",
            "status": "FAIL",
            "path": str(path),
            "failures": [f"visual sweep JSON is not valid JSON: {exc}"],
        }

    failures: list[str] = []
    screens = visual.get("screens")
    if not isinstance(screens, list):
        failures.append("visual sweep does not contain a screens array")
        screens = []

    screen_names = [str(screen.get("name")) for screen in screens if isinstance(screen, dict)]
    if screen_names != REQUIRED_VISUAL_SCREENS:
        failures.append(
            "visual sweep screen sequence mismatch: "
            f"expected {REQUIRED_VISUAL_SCREENS}, got {screen_names}"
        )

    if visual.get("status") != "PASS":
        failures.append(f"visual sweep status is {visual.get('status')!r}, expected 'PASS'")
    if visual.get("screen_count") != len(REQUIRED_VISUAL_SCREENS):
        failures.append(
            f"visual sweep screen_count is {visual.get('screen_count')!r}, "
            f"expected {len(REQUIRED_VISUAL_SCREENS)}"
        )

    labels: list[str] = []
    total_visible_controls = 0
    missing_screenshots: list[str] = []
    empty_screens: list[str] = []
    for screen in screens:
        if not isinstance(screen, dict):
            continue
        name = str(screen.get("name"))
        visible_count = int(screen.get("visible_control_count") or 0)
        total_visible_controls += visible_count
        if visible_count <= 0:
            empty_screens.append(name)
        screenshot = screen.get("screenshot")
        if not isinstance(screenshot, str) or not Path(screenshot).exists():
            missing_screenshots.append(name)
        controls = screen.get("controls", [])
        if isinstance(controls, list):
            for control in controls:
                if isinstance(control, dict) and control.get("visible") is True:
                    label = control.get("label")
                    if isinstance(label, str) and label.strip():
                        labels.append(label.strip())

    if empty_screens:
        failures.append("screens without visible controls: " + ", ".join(empty_screens))
    if missing_screenshots:
        failures.append("screens with missing screenshots: " + ", ".join(missing_screenshots))
    if visual.get("total_visible_controls") != total_visible_controls:
        failures.append(
            f"visual sweep total_visible_controls is {visual.get('total_visible_controls')!r}, "
            f"but screen inventory sums to {total_visible_controls}"
        )

    missing_labels = [
        required for required in REQUIRED_CONTROL_LABELS if not _label_matches(labels, required)
    ]
    if missing_labels:
        failures.append("required instructor controls missing from visual sweep: " + ", ".join(missing_labels))

    return {
        "name": "visual_sweep_validation",
        "status": "FAIL" if failures else "PASS",
        "path": str(path),
        "required_screens": REQUIRED_VISUAL_SCREENS,
        "required_control_labels": REQUIRED_CONTROL_LABELS,
        "screen_count": len(screens),
        "total_visible_controls": total_visible_controls,
        "unique_visible_labels": sorted(set(labels)),
        "failures": failures,
    }


def collect(app_dir: Path, visual_sweep: Path) -> dict[str, Any]:
    if visual_sweep.exists():
        visual_sweep.unlink()

    component = _run(["npm", "test", "--", "--run"], cwd=app_dir, timeout=120)
    build = _run(["npm", "run", "build"], cwd=app_dir, timeout=120)
    e2e = _run_e2e(app_dir=app_dir)

    visual: dict[str, Any] = {}
    if visual_sweep.exists():
        visual = json.loads(visual_sweep.read_text())
    visual_validation = validate_visual_sweep(visual_sweep)

    component_count = _parse_passed_tests(component["stdout"])
    e2e_count = _parse_passed_tests(e2e["stdout"])
    results = [component, build, e2e, visual_validation]
    status = "PASS" if all(result["status"] == "PASS" for result in results) else "FAIL"
    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "component_tests": component_count,
            "playwright_e2e_tests": e2e_count,
            "visual_sweep": str(visual_sweep),
            "visual_sweep_screens": visual.get("screen_count"),
            "visual_sweep_visible_controls": visual.get("total_visible_controls"),
            "visual_sweep_required_screens": len(REQUIRED_VISUAL_SCREENS),
            "visual_sweep_required_controls": len(REQUIRED_CONTROL_LABELS),
            "visual_sweep_validation": visual_validation["status"],
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect frontend release evidence")
    parser.add_argument("--app-dir", type=Path, default=Path("app"))
    parser.add_argument("--visual-sweep", type=Path, default=Path("artifacts/release-evidence/visual-sweep.json"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/release-evidence/frontend.json"))
    args = parser.parse_args()

    evidence = collect(args.app_dir, args.visual_sweep)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"status={evidence['status']}")
    print(f"json={args.out}")


if __name__ == "__main__":
    main()
