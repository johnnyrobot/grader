# Public Beta Release Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build repeatable, FERPA-safe release gates that can decide whether Grader is alpha, blocked, or supervised public beta.

**Architecture:** Add a small public-eval package under `engine/gates/` for evidence tiers, manifests, tiny committed fixtures, quality aggregation, and fairness aggregation. Keep live Canvas and release readiness as guarded scripts that can return `PASS`, `FAIL`, or honest `BLOCKED_*` states, and add one report command that combines Python, frontend, citation, Canvas, signing, updater, and clean-machine evidence into JSON and Markdown.

**Tech Stack:** Python 3.12, `uv run`, FastAPI test helpers where needed, existing metrics in `engine/grader/metrics.py`, existing citation harness, existing Canvas client, existing Tauri/React scripts.

## Global Constraints

- Use `uv run` for Python commands.
- Do not commit downloaded datasets, generated golden sets, private Canvas exports, model weights, credentials, app bundles, DMGs, notarization tickets, or clean-machine logs with secrets.
- Keep Canvas logic in Python, frontend engine calls in `app/src/api.ts`, and release gate orchestration in `engine/gates/` or `scripts/`.
- Keep teacher approval enforced in the data layer before Canvas posting.
- Keep original files immutable.
- Do not change grading thresholds without updating `RESEARCH.md` or a phase document with evidence.
- Public beta quality evidence uses explicit tiers: `public_proxy`, `teacher_local`, and `synthetic_fixture`.
- Synthetic fixtures may validate mechanics and regressions, but must never be reported as accuracy evidence.
- Live Canvas writes are permitted only for courses whose name starts with `ZZ-GRADER-TEST`.

---

## File Structure

- Create `engine/gates/public_proxy.py`: evidence-tier enums, normalized example schema, manifest schema, JSONL loading, deterministic split validation, quality aggregation, fairness aggregation, and tiny-fixture helpers.
- Modify `engine/gates/quality.py`: add `--profile public_proxy`, support public-proxy JSONL directories, preserve the existing teacher-local golden-set path, and report source-level details.
- Modify `engine/gates/bias.py`: add `--profile public_proxy`, reuse public-proxy examples with precomputed perturbed scores or live grading, and enforce the new fairness thresholds.
- Create `fixtures/public_proxy/`: tiny committed synthetic-shaped public-proxy fixtures and manifests used only for gate unit tests.
- Create `engine/tests/test_public_proxy_gate.py`: focused tests for manifests, tier separation, quality verdicts, fairness verdicts, and regression behavior.
- Modify `engine/verify_citations.py`: add explicit JSON artifact fields, retraction failure as a hard failing condition, and a citation-mode summary that downstream reports can consume.
- Create `engine/tests/test_citation_gate.py`: tests citation artifact verdict selection without network calls.
- Create `scripts/live_canvas_e2e.py`: guarded Canvas import -> approve -> post -> read-back test harness with dry-run default and redacted output.
- Create `engine/tests/test_live_canvas_e2e.py`: mocked tests for `ZZ-GRADER-TEST` safety boundary, approval enforcement, progress completion, read-back mismatch, and redaction.
- Create `scripts/release_readiness.py`: collect gate artifacts, check signing/notarization/updater preflight, accept optional clean-machine evidence, and write JSON plus Markdown verdict reports.
- Create `engine/tests/test_release_readiness.py`: tests verdict selection, blocked signing states, secret redaction, and report writing.
- Modify `README.md`, `AGENTS.md`, and `CLAUDE.md` only after implementation to describe the new commands and current readiness truth.

---

### Task 1: Public-Proxy Data Contract And Quality Gate

**Files:**
- Create: `engine/gates/public_proxy.py`
- Create: `fixtures/public_proxy/public_proxy_manifest.json`
- Create: `fixtures/public_proxy/public_proxy_examples.jsonl`
- Modify: `engine/gates/quality.py`
- Test: `engine/tests/test_public_proxy_gate.py`

**Interfaces:**
- Produces: `load_public_proxy_examples(path: Path) -> list[PublicProxyExample]`
- Produces: `evaluate_public_proxy_quality(path: Path, baseline_path: Path | None = None) -> GateResult`
- Produces CLI: `uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir fixtures/public_proxy`

- [ ] **Step 1: Write failing tests**

Create `engine/tests/test_public_proxy_gate.py` with tests that assert:

```python
from pathlib import Path

from gates.public_proxy import evaluate_public_proxy_quality, load_public_proxy_examples


FIXTURE_DIR = Path("fixtures/public_proxy")


def test_public_proxy_examples_load_with_tier_and_manifest():
    examples = load_public_proxy_examples(FIXTURE_DIR)
    assert {example.tier for example in examples} == {"public_proxy"}
    assert {example.source for example in examples} == {"tiny_source_a", "tiny_source_b"}
    assert all(example.split == "test" for example in examples)


def test_public_proxy_quality_passes_on_tiny_fixture():
    result = evaluate_public_proxy_quality(FIXTURE_DIR)
    assert result.status == "PASS"
    assert result.details["tier"] == "public_proxy"
    assert float(result.details["qwk"]) >= 0.70
    assert "tiny_source_a" in result.details["sources"]


def test_public_proxy_quality_fails_source_floor(tmp_path):
    source = tmp_path / "fixture"
    source.mkdir()
    (source / "manifest.json").write_text((FIXTURE_DIR / "public_proxy_manifest.json").read_text())
    rows = []
    for line in (FIXTURE_DIR / "public_proxy_examples.jsonl").read_text().splitlines():
        row = line.replace('"ai_scores": {"content": 4}', '"ai_scores": {"content": 0}')
        rows.append(row)
    (source / "examples.jsonl").write_text("\n".join(rows) + "\n")
    result = evaluate_public_proxy_quality(source)
    assert result.status == "FAIL"
    assert "QWK" in result.summary
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest engine/tests/test_public_proxy_gate.py -v`

Expected: fail because `gates.public_proxy` does not exist.

- [ ] **Step 3: Implement data contract and tiny fixtures**

Add `PublicProxyExample`, JSONL loading, manifest loading, source aggregation, QWK computation, and pass/fail logic in `engine/gates/public_proxy.py`. Add two tiny sources with enough deterministic score variation to pass QWK in `fixtures/public_proxy/`.

- [ ] **Step 4: Wire `quality.py` profile**

Add `--profile {teacher_local,public_proxy}` and `--public-proxy-dir`. When `public_proxy`, call `evaluate_public_proxy_quality`; when `teacher_local`, preserve current behavior.

- [ ] **Step 5: Verify**

Run:

```bash
uv run pytest engine/tests/test_public_proxy_gate.py -v
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir fixtures/public_proxy
uv run python engine/gates/quality.py
```

Expected: focused tests pass; public-proxy fixture gate passes; default teacher-local behavior still returns PASS/FAIL/SKIP according to local data.

- [ ] **Step 6: Commit**

```bash
git add engine/gates/public_proxy.py engine/gates/quality.py engine/tests/test_public_proxy_gate.py fixtures/public_proxy/
git commit -m "feat: add public proxy quality gate"
```

---

### Task 2: Public-Proxy Fairness Gate

**Files:**
- Modify: `engine/gates/public_proxy.py`
- Modify: `engine/gates/bias.py`
- Modify: `fixtures/public_proxy/public_proxy_examples.jsonl`
- Modify: `engine/tests/test_public_proxy_gate.py`

**Interfaces:**
- Produces: `evaluate_public_proxy_fairness(path: Path, threshold: float = 1.0) -> BiasResult`
- Produces CLI: `uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir fixtures/public_proxy`

- [ ] **Step 1: Add failing fairness tests**

Extend `engine/tests/test_public_proxy_gate.py`:

```python
from gates.public_proxy import evaluate_public_proxy_fairness


def test_public_proxy_fairness_passes_on_controlled_fixture():
    result = evaluate_public_proxy_fairness(FIXTURE_DIR)
    assert result.status == "PASS"
    assert result.details["tier"] == "public_proxy"
    assert result.details["perturbations"]["informal"]["status"] == "PASS"


def test_public_proxy_fairness_fails_large_delta(tmp_path):
    source = tmp_path / "fixture"
    source.mkdir()
    (source / "manifest.json").write_text((FIXTURE_DIR / "public_proxy_manifest.json").read_text())
    bad_rows = []
    for line in (FIXTURE_DIR / "public_proxy_examples.jsonl").read_text().splitlines():
        bad_rows.append(line.replace('"informal": {"content": 4}', '"informal": {"content": 0}'))
    (source / "examples.jsonl").write_text("\n".join(bad_rows) + "\n")
    result = evaluate_public_proxy_fairness(source)
    assert result.status == "FAIL"
    assert "informal" in result.summary
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest engine/tests/test_public_proxy_gate.py -v`

Expected: fail because `evaluate_public_proxy_fairness` is missing.

- [ ] **Step 3: Implement fairness aggregation**

Support per-example `perturbed_scores` keyed by perturbation name. Compute mean delta, mean absolute delta, max absolute delta, within-one-band percentage, and source-family deltas. Fail on thresholds from the spec.

- [ ] **Step 4: Wire `bias.py` profile**

Add `--profile {teacher_local,public_proxy}` and `--public-proxy-dir`. When public proxy is selected, call `evaluate_public_proxy_fairness`; preserve current teacher-local behavior as default.

- [ ] **Step 5: Verify**

Run:

```bash
uv run pytest engine/tests/test_public_proxy_gate.py -v
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir fixtures/public_proxy
uv run python engine/gates/bias.py
```

Expected: focused tests pass; public-proxy fixture fairness gate passes; default teacher-local behavior still returns PASS/FAIL/SKIP according to local data.

- [ ] **Step 6: Commit**

```bash
git add engine/gates/public_proxy.py engine/gates/bias.py engine/tests/test_public_proxy_gate.py fixtures/public_proxy/public_proxy_examples.jsonl
git commit -m "feat: add public proxy fairness gate"
```

---

### Task 3: Citation Mode Artifact

**Files:**
- Modify: `engine/verify_citations.py`
- Test: `engine/tests/test_citation_gate.py`

**Interfaces:**
- Produces: `decide_citation_mode(summary: dict) -> dict`
- JSON summary includes `mode`, `release_status`, `experimental_required`, and `failures`.

- [ ] **Step 1: Write failing tests**

Create `engine/tests/test_citation_gate.py`:

```python
from verify_citations import decide_citation_mode


def test_citation_mode_enabled_when_all_thresholds_pass():
    summary = {
        "accuracy": 0.91,
        "false_positives": 0,
        "retraction_check": {"total": 3, "caught": 3},
    }
    mode = decide_citation_mode(summary)
    assert mode["mode"] == "enabled"
    assert mode["release_status"] == "PASS"
    assert mode["experimental_required"] is False


def test_citation_mode_experimental_when_retractions_fail():
    summary = {
        "accuracy": 0.95,
        "false_positives": 0,
        "retraction_check": {"total": 3, "caught": 2},
    }
    mode = decide_citation_mode(summary)
    assert mode["mode"] == "experimental"
    assert mode["release_status"] == "FAIL"
    assert "retraction" in " ".join(mode["failures"])
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest engine/tests/test_citation_gate.py -v`

Expected: fail because `decide_citation_mode` does not exist.

- [ ] **Step 3: Implement citation mode decision**

Add `decide_citation_mode(summary)` to `engine/verify_citations.py`. Include its result in the JSON written by `--output`. Keep the CLI exit code failing when citation enabled-mode thresholds fail.

- [ ] **Step 4: Verify**

Run:

```bash
uv run pytest engine/tests/test_citation_gate.py -v
uv run python engine/verify_citations.py --fixtures fixtures/citations/citation_fixtures.json --output /tmp/grader-citation-results.json
```

Expected: tests pass; the real citation command may fail if accuracy remains below threshold, but `/tmp/grader-citation-results.json` contains the mode block.

- [ ] **Step 5: Commit**

```bash
git add engine/verify_citations.py engine/tests/test_citation_gate.py
git commit -m "feat: report citation beta mode"
```

---

### Task 4: Guarded Live Canvas E2E Harness

**Files:**
- Create: `scripts/live_canvas_e2e.py`
- Test: `engine/tests/test_live_canvas_e2e.py`

**Interfaces:**
- Produces: `redact(value: str) -> str`
- Produces: `validate_course_name(name: str, prefix: str = "ZZ-GRADER-TEST") -> None`
- Produces: `summarize_result(result: dict) -> dict`
- CLI: `uv run python scripts/live_canvas_e2e.py --course-id 106 --assignment-id 2836 --dry-run`

- [ ] **Step 1: Write failing tests**

Create `engine/tests/test_live_canvas_e2e.py`:

```python
import pytest

from scripts.live_canvas_e2e import redact, summarize_result, validate_course_name


def test_validate_course_name_allows_zz_boundary():
    validate_course_name("ZZ-GRADER-TEST-SANDBOX (safe to delete)")


def test_validate_course_name_rejects_unmarked_course():
    with pytest.raises(ValueError):
        validate_course_name("Production Biology 101")


def test_redact_removes_tokens_and_student_emails():
    text = "token=abc123 student@example.edu https://canvas.example/api"
    redacted = redact(text)
    assert "abc123" not in redacted
    assert "student@example.edu" not in redacted


def test_summarize_result_requires_completed_progress_and_readback_match():
    result = {
        "progress_state": "completed",
        "posted": 1,
        "read_back": [{"expected_score": 9, "actual_score": 9}],
    }
    assert summarize_result(result)["status"] == "PASS"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest engine/tests/test_live_canvas_e2e.py -v`

Expected: fail because `scripts.live_canvas_e2e` does not exist.

- [ ] **Step 3: Implement guarded harness helpers and CLI skeleton**

Implement dry-run by default. Validate env vars before live writes. For now, the CLI can produce `BLOCKED_CANVAS_CREDENTIALS` when `CANVAS_BASE_URL` or `CANVAS_TOKEN` is missing and can refuse `--apply` without verified `ZZ-GRADER-TEST` course name.

- [ ] **Step 4: Verify**

Run:

```bash
uv run pytest engine/tests/test_live_canvas_e2e.py -v
uv run python scripts/live_canvas_e2e.py --dry-run
```

Expected: tests pass; dry-run exits 0 with a JSON blocked or dry-run report and no live writes.

- [ ] **Step 5: Commit**

```bash
git add scripts/live_canvas_e2e.py engine/tests/test_live_canvas_e2e.py
git commit -m "feat: add guarded Canvas e2e harness"
```

---

### Task 5: Release Readiness Report

**Files:**
- Create: `scripts/release_readiness.py`
- Test: `engine/tests/test_release_readiness.py`

**Interfaces:**
- Produces: `redact_secrets(value: str) -> str`
- Produces: `decide_verdict(evidence: dict) -> str`
- Produces: `write_reports(evidence: dict, out_dir: Path) -> tuple[Path, Path]`
- CLI: `uv run python scripts/release_readiness.py --profile public_beta --out artifacts/release-readiness`

- [ ] **Step 1: Write failing tests**

Create `engine/tests/test_release_readiness.py`:

```python
import json
from pathlib import Path

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
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest engine/tests/test_release_readiness.py -v`

Expected: fail because `scripts.release_readiness` does not exist.

- [ ] **Step 3: Implement report command**

Implement secret redaction, evidence collection from optional JSON artifacts, signing preflight checks that return `BLOCKED_SIGNING_CREDENTIALS` without secrets, updater manifest checks when artifacts exist, and Markdown/JSON report writing.

- [ ] **Step 4: Verify**

Run:

```bash
uv run pytest engine/tests/test_release_readiness.py -v
uv run python scripts/release_readiness.py --profile public_beta --out /tmp/grader-release-readiness
```

Expected: tests pass; local command writes JSON and Markdown, likely with blocked signing/clean-machine state if credentials/evidence are absent.

- [ ] **Step 5: Commit**

```bash
git add scripts/release_readiness.py engine/tests/test_release_readiness.py
git commit -m "feat: add release readiness reporting"
```

---

### Task 6: Documentation And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Produces documented commands for public-proxy quality, public-proxy fairness, citation mode, live Canvas E2E, and release readiness.

- [ ] **Step 1: Update docs**

Update the current readiness section to say the app is still alpha until the new gates pass on real public proxy data, live Canvas credentials, and clean-machine signing evidence. Add the exact new commands.

- [ ] **Step 2: Run full focused verification**

Run:

```bash
uv run pytest engine/tests/ -v
uv run python engine/gates/quality.py --profile public_proxy --public-proxy-dir fixtures/public_proxy
uv run python engine/gates/bias.py --profile public_proxy --public-proxy-dir fixtures/public_proxy
uv run python engine/gates/egress.py --capture-duration 15
uv run python engine/gates/perf.py --memory-floor 16
cd app && npm run build
cd app && npm test -- --run
cd app/src-tauri && cargo check
uv run python scripts/live_canvas_e2e.py --dry-run
uv run python scripts/release_readiness.py --profile public_beta --out /tmp/grader-release-readiness
```

Expected: unit tests and local fixture gates pass; live Canvas/signing/clean-machine may be blocked honestly.

- [ ] **Step 3: Commit**

```bash
git add README.md AGENTS.md CLAUDE.md
git commit -m "docs: update beta readiness commands"
```

- [ ] **Step 4: Merge back to main locally**

From the original checkout, merge the implementation branch into `main` after verification:

```bash
git checkout main
git merge --no-ff codex/public-beta-release-gates
```

Expected: local `main` contains the implementation commits. Preserve unrelated dirty `.omo` state and any user doc edits by resolving carefully if conflicts occur.
