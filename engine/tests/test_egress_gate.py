from __future__ import annotations

import os
import subprocess

from gates import egress


def test_capture_connections_ignores_unrelated_python_processes(monkeypatch):
    output = """COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
Python 123 user 1u IPv4 dev 0t0 TCP 127.0.0.1:5000->api.crossref.org:443
Python 999 user 1u IPv4 dev 0t0 TCP 127.0.0.1:5001->104.26.5.132:443
"""

    monkeypatch.setattr(os, "getpid", lambda: 123)
    monotonic_values = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(egress.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(egress.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        egress.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["lsof"],
            returncode=0,
            stdout=output,
            stderr="",
        ),
    )

    connections = egress._capture_connections(duration=0.1)

    assert connections == [
        {
            "process": "Python",
            "pid": 123,
            "destination": "api.crossref.org:443",
        }
    ]
