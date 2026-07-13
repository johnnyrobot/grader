from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.dev_mock_engine import make_server


def _request(url: str, *, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_dev_mock_engine_serves_ui_inspection_contract():
    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert _request(f"{base}/health") == {"ok": True}
        queue = _request(f"{base}/api/queue")
        assert queue["stats"]["total"] == 3
        assert queue["items"][0]["submission_id"] == "Student 1042"
        assert _request(f"{base}/api/settings")["canvas_url"] == "https://canvas.example.invalid"
        assert _request(f"{base}/api/models")["ollama_running"] is True
        assert _request(f"{base}/api/grade/batch", method="POST", body={"assignment_id": "default"})["total"] == 3
        assert _request(f"{base}/api/grades/grade-1/approve", method="POST") == {"success": True}
        override = _request(
            f"{base}/api/grades/grade-1/override",
            method="POST",
            body={"teacher_scores": {"Thesis": 5, "Evidence": 3}},
        )
        assert override["teacher_total"] == 8
    finally:
        server.shutdown()
        server.server_close()
