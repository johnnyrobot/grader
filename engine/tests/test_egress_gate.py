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


def test_ip_behind_an_allowlisted_hostname_is_not_a_violation(monkeypatch):
    """lsof reports raw IPs; CDN-fronted allowlisted hosts must still be recognised.

    Reverse DNS is not authoritative — Cloudflare/AWS addresses usually have no PTR
    record pointing back at the service name, so relying on it reports an allowlisted
    host as a FERPA violation.
    """
    import socket

    egress._resolve_allowlist_ips.cache_clear()

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "api.crossref.org":
            return [(2, 1, 6, "", ("104.26.4.132", 443))]
        raise OSError("not resolvable in this test")

    def no_reverse_dns(addr):
        raise OSError("no PTR record")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyaddr", no_reverse_dns)

    assert egress._check_host_allowed("104.26.4.132") is True


def test_unknown_public_ip_is_still_a_violation(monkeypatch):
    """The gate must keep its teeth: unrecognised egress still fails."""
    import socket

    egress._resolve_allowlist_ips.cache_clear()

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "api.crossref.org":
            return [(2, 1, 6, "", ("104.26.4.132", 443))]
        raise OSError("not resolvable in this test")

    def no_reverse_dns(addr):
        raise OSError("no PTR record")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyaddr", no_reverse_dns)

    assert egress._check_host_allowed("203.0.113.7") is False


def test_total_resolution_failure_does_not_allow_unknown_hosts(monkeypatch):
    """Fail closed: if we cannot verify a host, we must not claim privacy is fine."""
    import socket

    egress._resolve_allowlist_ips.cache_clear()

    def always_fails(*args, **kwargs):
        raise OSError("DNS unavailable")

    monkeypatch.setattr(socket, "getaddrinfo", always_fails)
    monkeypatch.setattr(socket, "gethostbyaddr", always_fails)

    assert egress._check_host_allowed("203.0.113.7") is False


def test_allowlist_resolution_retries_transient_failures(monkeypatch):
    """A single DNS hiccup should not silently shrink the allowlist."""
    import socket

    egress._resolve_allowlist_ips.cache_clear()
    attempts = {"api.crossref.org": 0}

    def flaky_getaddrinfo(host, port, *args, **kwargs):
        if host == "api.crossref.org":
            attempts[host] += 1
            if attempts[host] == 1:
                raise OSError("transient DNS failure")
            return [(2, 1, 6, "", ("104.26.4.132", 443))]
        raise OSError("not resolvable in this test")

    monkeypatch.setattr(socket, "getaddrinfo", flaky_getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyaddr", lambda addr: (_ for _ in ()).throw(OSError()))

    assert egress._check_host_allowed("104.26.4.132") is True
    assert attempts["api.crossref.org"] >= 2
