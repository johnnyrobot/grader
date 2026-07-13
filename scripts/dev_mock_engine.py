#!/usr/bin/env python3
"""Run a tiny synthetic Grader engine for local UI inspection.

This server is intentionally fake and local-only. It exists so a developer or
Computer Use run can inspect onboarding, queue, review, settings, and model
manager screens without live Canvas, real student data, or a running grading
model. It should never be used as release-quality evidence by itself.
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = PROJECT_ROOT / "app" / "e2e" / "fixtures" / "instructor-beta.json"


def load_fixture(path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    return json.loads(path.read_text())


class DevMockEngineHandler(BaseHTTPRequestHandler):
    fixture: dict[str, Any] = {}

    server_version = "GraderDevMockEngine/0.1"

    def log_message(self, format: str, *args: object) -> None:
        # Keep the tool output quiet during manual inspection.
        return

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self) -> None:
        self._send_json({})

    def do_GET(self) -> None:
        fixture = self.fixture
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_json({"ok": True})
        elif path == "/api/queue":
            self._send_json(fixture["queue"])
        elif path == "/api/updates/check":
            self._send_json({"update_available": False, "current_version": "0.1.1"})
        elif path == "/api/settings":
            self._send_json(fixture["settings"])
        elif path == "/api/rubrics":
            self._send_json(fixture["rubrics"])
        elif path.startswith("/api/rubrics/"):
            title = unquote(path.removeprefix("/api/rubrics/"))
            detail = dict(fixture["rubricDetail"])
            detail["title"] = title or detail.get("title")
            self._send_json(detail)
        elif path == "/api/models":
            self._send_json(fixture["models"])
        else:
            self._send_json({"error": f"unknown route {path}"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()
        if path == "/api/grade/batch":
            self._send_json({"job_id": "dev-mock-job-1", "total": len(self.fixture["queue"]["items"])})
        elif path == "/api/models/install":
            self._send_json({"success": True, "model_name": body.get("model_name")})
        elif path == "/api/rubrics":
            self._send_json({"id": 12})
        elif path.endswith("/approve"):
            self._send_json({"success": True})
        elif path.endswith("/override"):
            teacher_scores = body.get("teacher_scores") or {}
            self._send_json({
                "ai_total": 7,
                "teacher_total": sum(float(value) for value in teacher_scores.values()),
                "refinement_proposals": [],
            })
        else:
            self._send_json({"error": f"unknown route {path}"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        if urlparse(self.path).path == "/api/settings":
            self._read_body()
            self._send_json({"success": True})
        else:
            self._send_json({"error": f"unknown route {self.path}"}, HTTPStatus.NOT_FOUND)


def make_server(host: str, port: int, fixture_path: Path = DEFAULT_FIXTURE) -> ThreadingHTTPServer:
    class Handler(DevMockEngineHandler):
        fixture = load_fixture(fixture_path)

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a synthetic local Grader engine for UI inspection")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8756)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()

    server = make_server(args.host, args.port, args.fixture)
    print(f"dev mock engine listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
