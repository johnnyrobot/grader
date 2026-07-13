#!/bin/bash
# Sign, notarize, and staple a Grader.app build.
#
# Prerequisites:
#   - Apple Developer ID Application certificate in keychain
#   - App-specific password for notarytool stored: xcrun notarytool store-credentials grader --apple-id ... --password ... --team-id ...
#   - Built app at app/src-tauri/target/release/bundle/macos/Grader.app
#
# Usage:
#   ./scripts/sign_and_notarize.sh
#   ./scripts/sign_and_notarize.sh --skip-notarize  # sign only (testing)
#   ./scripts/sign_and_notarize.sh --dmg            # also create + notarize DMG
#   ./scripts/sign_and_notarize.sh --skip-preflight # emergency only; bypass local credential probe
#
# Environment:
#   KEYCHAIN_PROFILE  - notarytool keychain profile name (default: grader)
#   SIGNING_IDENTITY   - codesign identity (default: "Developer ID Application: ...")

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_PATH="$PROJECT_ROOT/app/src-tauri/target/release/bundle/macos/Grader.app"
KEYCHAIN_PROFILE="${KEYCHAIN_PROFILE:-grader}"
SIGNING_IDENTITY="${SIGNING_IDENTITY:-}"

DO_NOTARIZE=true
DO_DMG=false
DO_PREFLIGHT=true

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-notarize) DO_NOTARIZE=false; shift ;;
        --skip-preflight) DO_PREFLIGHT=false; shift ;;
        --dmg) DO_DMG=true; shift ;;
        --app-path) APP_PATH="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ ! -d "$APP_PATH" ]]; then
    echo "ERROR: App not found at $APP_PATH"
    echo "Build it first: cd app && npx tauri build --bundles app --no-sign --ci"
    exit 1
fi

# Auto-detect signing identity if not set
if [[ -z "$SIGNING_IDENTITY" ]]; then
    SIGNING_IDENTITY=$(security find-identity -v -p codesigning | grep "Developer ID Application" | head -1 | sed 's/.*"\(.*\)".*/\1/')
    if [[ -z "$SIGNING_IDENTITY" ]]; then
        echo "ERROR: No 'Developer ID Application' certificate found in keychain"
        echo "Set SIGNING_IDENTITY env var or install the certificate"
        exit 1
    fi
fi

echo "=== Signing ==="
echo "  App: $APP_PATH"
echo "  Identity: $SIGNING_IDENTITY"

if [[ "$DO_PREFLIGHT" == "true" ]]; then
    echo "=== Signing preflight ==="
    PREFLIGHT_ARGS=(
        --config "$PROJECT_ROOT/app/src-tauri/tauri.conf.json"
        --keychain-profile "$KEYCHAIN_PROFILE"
        --signing-identity "$SIGNING_IDENTITY"
        --out "$PROJECT_ROOT/artifacts/release-evidence/signing-preflight.json"
    )
    if [[ "$DO_NOTARIZE" != "true" ]]; then
        PREFLIGHT_ARGS+=(--skip-notary)
    fi
    uv run python "$PROJECT_ROOT/scripts/signing_doctor.py" "${PREFLIGHT_ARGS[@]}"
fi

# Sign the Python sidecar first (innermost)
SIDECAR="$APP_PATH/Contents/Resources/engine"
if [[ -d "$SIDECAR" ]]; then
    echo "  Signing sidecar: engine/"
    find "$SIDECAR" -type f \( -name "*.py" -o -name "*.so" -o -perm +111 \) | while read -r f; do
        codesign --force --sign "$SIGNING_IDENTITY" --options runtime "$f" 2>/dev/null || true
    done
fi

# Sign all dylibs/frameworks first (deep, inside-out)
echo "  Signing frameworks and libraries..."
find "$APP_PATH" \( -type f -name "*.dylib" -o -type d -name "*.framework" \) | while read -r f; do
    codesign --force --sign "$SIGNING_IDENTITY" --options runtime "$f" 2>/dev/null || true
done

# Sign the main bundle (deep + runtime hardening)
echo "  Signing main bundle..."
codesign --deep --force --options runtime --sign "$SIGNING_IDENTITY" "$APP_PATH"

# Verify
echo "  Verifying signature..."
codesign --verify --deep --strict "$APP_PATH"
codesign --verify --verbose=2 "$APP_PATH" 2>&1 | head -5

echo "=== Signed successfully ==="

if [[ "$DO_NOTARIZE" == "true" ]]; then
    echo ""
    echo "=== Notarizing ==="

    echo "  Submitting app for notarization..."
    # Notarize and staple the app itself even when distributing a DMG. The clean-machine
    # launch path assesses the copied .app, not just the outer disk image.
    ZIP_PATH="/tmp/Grader.zip"
    rm -f "$ZIP_PATH"
    ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

    xcrun notarytool submit "$ZIP_PATH" --keychain-profile "$KEYCHAIN_PROFILE" --wait

    echo "  Stapling app..."
    xcrun stapler staple "$APP_PATH"
    xcrun stapler validate "$APP_PATH"
    spctl --assess --type execute "$APP_PATH"
    rm -f "$ZIP_PATH"
    echo "=== App notarized + stapled ==="

    if [[ "$DO_DMG" == "true" ]]; then
        DMG_PATH="$PROJECT_ROOT/app/src-tauri/target/release/bundle/dmg/Grader.dmg"
        DMG_DIR="$(dirname "$DMG_PATH")"
        mkdir -p "$DMG_DIR"

        echo "  Creating DMG..."
        # Use hdiutil to create a DMG
        TEMP_DMG="$DMG_DIR/Grader_temp.dmg"
        hdiutil create -volname "Grader" -srcfolder "$APP_PATH" -ov -format UDBZ "$TEMP_DMG"
        mv "$TEMP_DMG" "$DMG_PATH"

        echo "  Signing DMG..."
        codesign --force --sign "$SIGNING_IDENTITY" "$DMG_PATH"
        codesign --verify "$DMG_PATH"

        echo "  Submitting DMG for notarization..."
        xcrun notarytool submit "$DMG_PATH" --keychain-profile "$KEYCHAIN_PROFILE" --wait

        echo "  Stapling DMG..."
        xcrun stapler staple "$DMG_PATH"
        xcrun stapler validate "$DMG_PATH"
        spctl --assess --type open --context context:primary-signature "$DMG_PATH"
        echo "=== DMG notarized + stapled ==="
        echo "  $DMG_PATH"
    fi
fi

echo ""
echo "=== Done ==="
echo "  App: $APP_PATH"
if [[ "$DO_DMG" == "true" ]]; then
    echo "  DMG: $DMG_PATH"
fi
echo ""
echo "To distribute:"
echo "  1. Upload the DMG to GitHub Releases"
echo "  2. Generate signed Tauri latest.json (scripts/generate_appcast.py --signing-key ...)"
echo "  3. Tag the release: git tag v0.1.0"
