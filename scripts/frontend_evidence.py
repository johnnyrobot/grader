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


def collect(app_dir: Path, visual_sweep: Path) -> dict[str, Any]:
    component = _run(["npm", "test", "--", "--run"], cwd=app_dir, timeout=120)
    build = _run(["npm", "run", "build"], cwd=app_dir, timeout=120)
    e2e = _run_e2e(app_dir=app_dir)

    visual: dict[str, Any] = {}
    if visual_sweep.exists():
        visual = json.loads(visual_sweep.read_text())

    component_count = _parse_passed_tests(component["stdout"])
    e2e_count = _parse_passed_tests(e2e["stdout"])
    results = [component, build, e2e]
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
