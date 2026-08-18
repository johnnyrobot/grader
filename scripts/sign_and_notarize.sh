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
APP_VERSION="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$PROJECT_ROOT/app/src-tauri/tauri.conf.json")"
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

ENTITLEMENTS="$PROJECT_ROOT/app/src-tauri/Entitlements.plist"
if [[ ! -f "$ENTITLEMENTS" ]]; then
    echo "ERROR: entitlements not found at $ENTITLEMENTS"
    exit 1
fi

RESOURCES="$APP_PATH/Contents/Resources"
if [[ ! -x "$RESOURCES/runtime/bin/python3" ]]; then
    echo "ERROR: $APP_PATH carries no engine runtime."
    echo "  Grader must ship its own Python; an app without one cannot start on a machine"
    echo "  that has no checkout of this repository (issue #23)."
    echo "  Build the bundle and rebuild the app:"
    echo "    uv run python scripts/build_engine_bundle.py"
    echo "    cd app && npx tauri build --bundles app --no-sign --ci"
    exit 1
fi

# Every Mach-O in the bundle is signed individually, deepest first. --deep is not used:
# it would re-sign the interpreter without its entitlements, and the extension modules
# it loads would then be refused at import time under the hardened runtime.
echo "  Signing the bundled engine runtime (this takes a few minutes)..."
MACHO_LIST="$(mktemp)"
trap 'rm -f "$MACHO_LIST"' EXIT

# Classify first, sign second. Only Mach-O files carry a code signature; handing codesign
# a .py would attach a pointless extended attribute instead.
MACHO_COUNT=0
while IFS= read -r -d '' f; do
    if [[ "$(file -b "$f")" == Mach-O* ]]; then
        printf '%s\0' "$f" >> "$MACHO_LIST"
        MACHO_COUNT=$((MACHO_COUNT + 1))
    fi
done < <(find "$RESOURCES" -depth -type f \( -name "*.so" -o -name "*.dylib" -o -perm +111 \) -print0)

if [[ "$MACHO_COUNT" -eq 0 ]]; then
    echo "ERROR: found no Mach-O files under $RESOURCES — the runtime looks empty"
    exit 1
fi

# In batches: ~500 extension modules is too many codesign invocations otherwise.
xargs -0 -n 25 codesign --force --sign "$SIGNING_IDENTITY" --options runtime \
    --entitlements "$ENTITLEMENTS" --timestamp < "$MACHO_LIST"
echo "    signed $MACHO_COUNT Mach-O files under Resources/"

# Frameworks and libraries outside Resources (Tauri's own).
echo "  Signing frameworks and libraries..."
find "$APP_PATH" -depth \( -type f -name "*.dylib" -o -type d -name "*.framework" \) \
    -not -path "$RESOURCES/*" | while read -r f; do
    codesign --force --sign "$SIGNING_IDENTITY" --options runtime --timestamp "$f" 2>/dev/null || true
done

# Sign the main bundle last, sealing everything beneath it.
echo "  Signing main bundle..."
codesign --force --options runtime --entitlements "$ENTITLEMENTS" --timestamp \
    --sign "$SIGNING_IDENTITY" "$APP_PATH"

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

        echo "  Creating DMG (compressing ~1.2 GB, takes a few minutes)..."
        TEMP_DMG="$DMG_DIR/Grader_temp.dmg"
        # Clear both first. A failed hdiutil run that leaves the previous release's DMG
        # in place is worse than no DMG: it is the right name and a plausible size, and
        # nothing downstream re-reads what is inside it.
        rm -f "$TEMP_DMG" "$DMG_PATH"

        if ! hdiutil create -volname "Grader" -srcfolder "$APP_PATH" -ov -format UDBZ "$TEMP_DMG"; then
            echo "ERROR: hdiutil could not build the DMG. No DMG has been left behind."
            echo "  Quit any running copy of Grader and retry; hdiutil fails with"
            echo "  'No child processes' when the app is running from the bundle it is reading."
            exit 1
        fi
        mv "$TEMP_DMG" "$DMG_PATH"

        echo "  Signing DMG..."
        codesign --force --sign "$SIGNING_IDENTITY" --timestamp "$DMG_PATH"
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
echo "  3. Tag the release: git tag v$APP_VERSION"
