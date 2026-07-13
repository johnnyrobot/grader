from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_release_workflow_builds_tauri_bundle_before_signing():
    text = WORKFLOW.read_text()

    assert "permissions:\n  contents: write" in text
    assert "npx tauri build --bundles app --no-sign --ci" in text
    assert "working-directory: app/src-tauri\n        run: cargo build --release" not in text


def test_release_workflow_passes_signing_script_env_names():
    text = WORKFLOW.read_text()

    assert "APPLE_CERTIFICATE_P12_BASE64: ${{ secrets.APPLE_CERTIFICATE_P12_BASE64 }}" in text
    assert "APPLE_CERTIFICATE_PASSWORD: ${{ secrets.APPLE_CERTIFICATE_PASSWORD }}" in text
    assert "security import \"$CERT_PATH\"" in text
    assert "security set-key-partition-list" in text
    assert "SIGNING_IDENTITY: ${{ secrets.APPLE_SIGNING_IDENTITY }}" in text
    assert "KEYCHAIN_PROFILE: grader" in text
    assert "APPLE_SIGNING_IDENTITY:" not in text
    assert "APPLE_KEYCHAIN_PROFILE:" not in text
    assert 'store-credentials "$KEYCHAIN_PROFILE"' in text


def test_release_workflow_uses_app_version_without_tag_prefix():
    text = WORKFLOW.read_text()

    assert 'VERSION="${GITHUB_REF_NAME#v}"' in text
    assert '--version "$VERSION"' in text
    assert "--version ${{ github.ref_name }}" not in text


def test_release_workflow_materializes_updater_key_secret_as_file():
    text = WORKFLOW.read_text()

    assert "TAURI_SIGNING_KEY: ${{ secrets.TAURI_SIGNING_KEY }}" in text
    assert (
        "TAURI_SIGNING_PRIVATE_KEY_PASSWORD: "
        "${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}"
    ) in text
    assert 'UPDATER_KEY_PATH="$RUNNER_TEMP/tauri-updater-key.pem"' in text
    assert 'printf \'%s\' "$TAURI_SIGNING_KEY" > "$UPDATER_KEY_PATH"' in text
    assert '--signing-key "$UPDATER_KEY_PATH"' in text
    assert "--signing-key ${{ secrets.TAURI_SIGNING_KEY }}" not in text
