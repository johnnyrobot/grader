from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_release_workflow_builds_tauri_bundle_before_signing():
    text = WORKFLOW.read_text()

    assert "npx tauri build --bundles app --no-sign --ci" in text
    assert "working-directory: app/src-tauri\n        run: cargo build --release" not in text


def test_release_workflow_passes_signing_script_env_names():
    text = WORKFLOW.read_text()

    assert "SIGNING_IDENTITY: ${{ secrets.APPLE_SIGNING_IDENTITY }}" in text
    assert "KEYCHAIN_PROFILE: ${{ secrets.APPLE_KEYCHAIN_PROFILE }}" in text
    assert "APPLE_SIGNING_IDENTITY:" not in text
    assert "APPLE_KEYCHAIN_PROFILE:" not in text
    assert 'store-credentials "$KEYCHAIN_PROFILE"' in text


def test_release_workflow_uses_app_version_without_tag_prefix():
    text = WORKFLOW.read_text()

    assert 'VERSION="${GITHUB_REF_NAME#v}"' in text
    assert '--version "$VERSION"' in text
    assert "--version ${{ github.ref_name }}" not in text
