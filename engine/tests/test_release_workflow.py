from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_release_workflow_verifies_source_without_apple_secrets():
    text = WORKFLOW.read_text()

    assert "permissions:\n  contents: read" in text
    assert "run: uv sync" in text
    assert "run: uv run pytest engine/tests/ -q" in text
    assert "run: npm test -- --run" in text
    assert "run: cargo check" in text
    assert "npx tauri build --bundles app --no-sign --ci" in text
    assert "APPLE_CERTIFICATE_P12_BASE64" not in text
    assert "APPLE_CERTIFICATE_PASSWORD" not in text
    assert "APPLE_APP_SPECIFIC_PASSWORD" not in text
    assert "softprops/action-gh-release" not in text


def test_release_workflow_runs_version_guard_for_tags():
    text = WORKFLOW.read_text()

    assert "if: startsWith(github.ref, 'refs/tags/')" in text
    assert 'scripts/release_version_guard.py' in text
    assert '--tag "${GITHUB_REF_NAME}"' in text


def test_release_workflow_documents_local_publication_boundary():
    text = WORKFLOW.read_text()

    assert "scripts/sign_and_notarize.sh" in text
    assert "scripts/publish_local_release.py" in text
