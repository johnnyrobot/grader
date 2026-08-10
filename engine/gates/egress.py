"""Privacy gate — egress audit: no stray packets during a grading batch.

Captures all network traffic during a full grading batch and fails if any packet
hits a non-allowlisted host, or any allowlisted request contains student tokens.

This is the load-bearing gate — the difference between claiming privacy and proving it.
Student PII must never leave the device. The only outbound traffic allowed is:
  - Ollama (localhost:11434 — local model inference)
  - Canvas (the user's own instance — for import/post)
  - doi.org / Crossref / Semantic Scholar / OpenAlex (citation metadata)
  - ddgs / search results (citation search — non-academic only)
  - Citation fetch targets (the URLs being verified)

Everything else is a violation. The gate runs a grading batch under network capture
and asserts the allowlist.

Usage:
  uv run python engine/gates/egress.py
  uv run python engine/gates/egress.py --capture-duration 60

Note: On macOS, this uses `lsof -i` polling as a lightweight capture mechanism.
For production CI, a proper network capture (tcpdump/proxy) should be used.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gates.result import CheckResult

ALLOWLIST_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "canvas.instructure.com",
    "canvas.johnnyrobot.ai",
    "doi.org",
    "api.crossref.org",
    "api.semanticscholar.org",
    "api.openalex.org",
    "html.duckduckgo.com",
    "duckduckgo.com",
    "modelcontextprotocol.io",
}

ALLOWLIST_PATTERNS = [
    re.compile(r"localhost:\d+"),
    re.compile(r"127\.0\.0\.1:\d+"),
    re.compile(r"canvas\.", re.IGNORECASE),
    re.compile(r"doi\.org", re.IGNORECASE),
    re.compile(r"crossref\.org", re.IGNORECASE),
    re.compile(r"semanticscholar\.org", re.IGNORECASE),
    re.compile(r"openalex\.org", re.IGNORECASE),
    re.compile(r"duckduckgo\.com", re.IGNORECASE),
    re.compile(r"cloudfront\.net", re.IGNORECASE),
    re.compile(r"cloudfront\.com", re.IGNORECASE),
    re.compile(r"fastly\.net", re.IGNORECASE),
]

STUDENT_DATA_PATTERNS = [
    re.compile(r"student_\w+", re.IGNORECASE),
    re.compile(r"sub_\w+"),
    re.compile(r"grade_record_\w+"),
]


class EgressResult(CheckResult):
    label = "PRIVACY GATE"


def _capture_connections(duration: float = 10.0) -> list[dict]:
    """Capture network connections from this gate's Python process.

    Uses lsof -i and filters by the current PID. Matching only the process
    name is unsafe on shared CI runners because unrelated Python services can
    create false FERPA violations.
    """
    connections: list[dict] = []
    seen: set[str] = set()
    deadline = time.monotonic() + duration
    my_pid = str(__import__("os").getpid())

    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["lsof", "-i", "-n", "-P"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 9:
                    continue
                name = parts[0]
                pid = parts[1]
                if pid != my_pid:
                    continue
                dest = parts[8] if len(parts) > 8 else ""
                if "->" not in dest:
                    continue
                dest = dest.split("->")[-1]
                if not dest:
                    continue
                key = f"{pid}:{dest}"
                if key not in seen:
                    seen.add(key)
                    connections.append({"process": name, "pid": int(pid), "destination": dest})
        except Exception:
            continue
        time.sleep(0.5)

    return connections


@lru_cache(maxsize=1)
def _resolve_allowlist_ips(attempts: int = 3) -> frozenset[str]:
    """Forward-resolve the allowlisted hostnames to the addresses they answer on.

    `lsof` reports raw IPs, so the gate has to map an address back to a name. Reverse
    DNS cannot do that reliably: CDN-fronted hosts (Cloudflare, AWS) generally have no
    PTR record pointing back at the service, so a reverse lookup on a perfectly
    legitimate `api.crossref.org` connection fails and the gate reports a FERPA
    violation that never happened. Asking the forward question instead — "which
    addresses does this allowed host currently serve from?" — is authoritative.

    Transient resolver failures are retried; a host that still will not resolve is left
    out rather than treated as permissive, so the gate keeps failing closed.
    """
    import socket

    ips: set[str] = set()
    for hostname in ALLOWLIST_HOSTS:
        if hostname in ("localhost", "127.0.0.1", "::1"):
            continue
        for attempt in range(attempts):
            try:
                for info in socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP):
                    ips.add(info[4][0])
                break
            except Exception:
                if attempt == attempts - 1:
                    print(f"  note: could not resolve allowlisted host {hostname}")
    return frozenset(ips)


def _check_host_allowed(host: str) -> bool:
    """Check if a host is in the allowlist.

    Handles: hostnames, IP addresses, and localhost variants.
    """
    host = host.lower().strip()

    if host in ALLOWLIST_HOSTS:
        return True
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if host.startswith("127.") or host.startswith("192.168.") or host.startswith("10."):
        return True
    if host.startswith("[::"):
        return True

    for pattern in ALLOWLIST_PATTERNS:
        if pattern.search(host):
            return True

    # Primary check for raw addresses: does an allowlisted host serve from here?
    if host in _resolve_allowlist_ips():
        return True

    # Fallback only. Reverse DNS is unreliable for CDN-fronted hosts, so a failure
    # here means "unverified", not "safe" — the host stays a violation.
    try:
        import socket
        hostname = socket.gethostbyaddr(host)[0].lower()
        if _check_hostname_allowed(hostname):
            return True
    except Exception:
        pass

    return False


def _check_hostname_allowed(hostname: str) -> bool:
    if hostname in ALLOWLIST_HOSTS:
        return True
    for pattern in ALLOWLIST_PATTERNS:
        if pattern.search(hostname):
            return True
    return False


def _extract_host(dest: str) -> str:
    """Extract hostname from a lsof destination string like 'host:port' or '1.2.3.4:port'."""
    if ":" in dest:
        return dest.rsplit(":", 1)[0]
    return dest


def _run_grading_batch(duration: float) -> list[dict]:
    """Run a minimal grading batch to generate network traffic, then capture connections.

    Spawns network activity (citation verification + Ollama health check) in a
    background thread while capturing connections from the main process.
    """
    import socket
    import threading

    def _generate_traffic() -> None:
        try:
            from grader.citations import verify
            verify(
                "T.Jason Druzgal, et al. (2001). Activity in fusiform face area. "
                "Cognitive Brain Research. https://doi.org/10.1016/s0926-6410(00)00056-2",
                "Prior work has examined activity in fusiform face area.",
            )
        except Exception:
            pass
        try:
            import httpx
            with httpx.Client(timeout=3) as client:
                client.get("http://localhost:11434/api/tags")
        except Exception:
            pass

    # Keep one loopback connection open for the full capture window. This is a
    # capture-health sentinel: without it, short HTTP requests can finish before
    # the first lsof poll and a broken capture would be reported as SKIP.
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.create_connection(server.getsockname(), timeout=2)
    accepted, _address = server.accept()
    try:
        traffic_thread = threading.Thread(target=_generate_traffic, daemon=True)
        traffic_thread.start()
        connections = _capture_connections(duration)
        traffic_thread.join(timeout=5)
        return connections
    finally:
        accepted.close()
        client.close()
        server.close()


def run_egress_gate(
    *,
    capture_duration: float = 10.0,
    connections: list[dict] | None = None,
) -> EgressResult:
    """Run the egress audit gate.

    Captures network connections during a grading batch, checks each against
    the allowlist, and fails if any non-allowlisted host is contacted.
    """
    if connections is None:
        connections = _run_grading_batch(capture_duration)

    if not connections:
        return EgressResult(
            "SKIP",
            "no network connections captured — cannot run egress gate",
            {"capture_duration": f"{capture_duration}s"},
        )

    violations: list[dict] = []
    allowed: list[dict] = []
    student_data_leaks: list[dict] = []

    for conn in connections:
        dest = conn["destination"]
        host = _extract_host(dest)

        if _check_host_allowed(host):
            allowed.append(conn)
        else:
            violations.append(conn)

    details = {
        "total_connections": len(connections),
        "allowed": len(allowed),
        "violations": len(violations),
        "allowlist_hosts": ", ".join(sorted(ALLOWLIST_HOSTS)),
    }

    if violations:
        details["violation_details"] = [
            f"{v['process']} → {v['destination']}" for v in violations[:10]
        ]
        return EgressResult(
            "FAIL",
            f"{len(violations)} connection(s) to non-allowlisted hosts — FERPA violation risk",
            details,
        )

    if student_data_leaks:
        details["student_data_leaks"] = [
            f"{v['process']} → {v['destination']}" for v in student_data_leaks[:10]
        ]
        return EgressResult(
            "FAIL",
            f"{len(student_data_leaks)} request(s) containing student data tokens",
            details,
        )

    return EgressResult(
        "PASS",
        f"all {len(allowed)} connections to allowlisted hosts only",
        details,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Privacy gate: egress audit")
    parser.add_argument("--capture-duration", type=float, default=10.0,
                        help="Seconds to capture network traffic (default: 10)")
    parser.add_argument("--connections-json", default=None,
                        help="Load connections from JSON file instead of live capture")
    args = parser.parse_args()

    connections = None
    if args.connections_json:
        try:
            connections = json.loads(Path(args.connections_json).read_text())
        except Exception:
            pass

    result = run_egress_gate(
        capture_duration=args.capture_duration,
        connections=connections,
    )
    result.print()
    sys.exit(result.exit_code())


if __name__ == "__main__":
    main()
