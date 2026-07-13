import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.release_readiness import decide_verdict, redact_secrets, write_reports


def test_decide_verdict_supervised_public_beta_when_required_gates_pass():
    evidence = {
        "quality": {"status": "PASS"},
        "fairness": {"status": "PASS"},
        "citations": {"status": "PASS", "mode": "enabled"},
        "canvas": {"status": "PASS"},
        "privacy": {"status": "PASS"},
        "performance": {"status": "PASS"},
        "frontend": {"status": "PASS"},
        "rust": {"status": "PASS"},
        "signing": {"status": "PASS"},
        "clean_machine": {"status": "PASS"},
    }
    assert decide_verdict(evidence) == "supervised_public_beta"


def test_decide_verdict_blocked_when_signing_blocked():
    evidence = {"quality": {"status": "PASS"}, "signing": {"status": "BLOCKED_SIGNING_CREDENTIALS"}}
    assert decide_verdict(evidence) == "blocked"


def test_redact_secrets_masks_tokens():
    redacted = redact_secrets("CANVAS_TOKEN=secret APPLE_ID_PASSWORD=pass")
    assert "secret" not in redacted
    assert "pass" not in redacted


def test_write_reports(tmp_path):
    json_path, md_path = write_reports({"verdict": "alpha", "quality": {"status": "SKIP"}}, tmp_path)
    assert json.loads(json_path.read_text())["verdict"] == "alpha"
    assert "alpha" in md_path.read_text()
