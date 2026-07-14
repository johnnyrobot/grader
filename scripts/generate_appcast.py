#!/usr/bin/env python3
"""Generate the Tauri updater manifest (latest.json) for a release.

Tauri's built-in macOS updater requires a gzip-compressed tar archive whose
top-level entry is the signed ``Grader.app`` bundle. A DMG is a distribution
artifact for people, but it is not a valid input to ``downloadAndInstall``.

The manifest is uploaded to GitHub Releases as the `latest.json` asset.
The app's tauri.conf.json points to:
  https://github.com/johnnyrobot/grader-releases/releases/latest/download/latest.json

Usage:
  # Package a signed/notarized .app and generate its manifest
  uv run python scripts/generate_appcast.py \
    --app app/src-tauri/target/release/bundle/macos/Grader.app \
    --version 0.1.1 \
    --signing-key /path/to/key.pem \
    --output latest.json

  # Or use an already-created Tauri-compatible updater bundle
  uv run python scripts/generate_appcast.py \
    --updater-bundle app/src-tauri/target/release/bundle/macos/Grader.app.tar.gz \
    --version 0.1.1 \
    --signing-key /path/to/key.pem \
    --output latest.json

  # Unsigned local smoke fixture only; never use for release evidence
  uv run python scripts/generate_appcast.py \
    --updater-bundle Grader.app.tar.gz --version 0.1.1 \
    --allow-unsigned \
    --output latest.json

Prerequisites:
  - Tauri updater CLI for signing: cargo install tauri-cli
  - Generate a key pair: tauri signer generate -w /path/to/key.pem
  - Store the public key in tauri.conf.json plugins.updater.pubkey
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path


SIGNATURE_RE = re.compile(r"(?im)^\s*(?:Public\s+)?Signature:\s*(\S+)")


def _file_size(path: Path) -> int:
    return path.stat().st_size


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_password_from_keychain(service: str, account: str | None = None) -> str | None:
    command = ["security", "find-generic-password", "-w", "-s", service]
    if account:
        command.extend(["-a", account])
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    password = (result.stdout or "").rstrip("\n")
    return password or None


def _package_app(app_path: Path, output: Path) -> Path:
    """Create the ``.app.tar.gz`` layout required by Tauri's macOS updater."""
    if not app_path.is_dir() or app_path.suffix != ".app":
        print(f"ERROR: --app expects a .app directory: {app_path}")
        sys.exit(1)
    if not output.name.endswith(".app.tar.gz"):
        print(f"ERROR: updater bundle must end in .app.tar.gz: {output}")
        sys.exit(1)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        archive.add(app_path, arcname=app_path.name, recursive=True)
    return output


def _sign_file(path: Path, key_path: Path, env: dict[str, str] | None = None) -> str | None:
    """Sign a file using tauri CLI. Returns the signature string."""
    try:
        result = subprocess.run(
            ["cargo", "tauri", "signer", "sign", "--private-key-path", str(key_path), str(path)],
            capture_output=True, text=True, check=True, env=env,
        )
        match = SIGNATURE_RE.search(result.stdout)
        if match:
            return match.group(1).strip()
        # If no explicit "Signature:" line, return the whole stdout
        return result.stdout.strip()
    except FileNotFoundError:
        print("WARN: tauri CLI not found — skipping signature")
        print("  Install: cargo install tauri-cli")
        return None
    except subprocess.CalledProcessError as e:
        print(f"WARN: signing failed: {e.stderr}")
        if "password" in (e.stderr or "").lower():
            print(
                "  Hint: set TAURI_SIGNING_PRIVATE_KEY_PASSWORD for non-interactive release signing, "
                "or pass --password when running `cargo tauri signer sign` manually."
            )
        return None


def generate_manifest(
    artifact_path: Path,
    version: str,
    output: Path,
    signing_key: Path | None = None,
    release_url: str | None = None,
    allow_unsigned: bool = False,
    signing_env: dict[str, str] | None = None,
) -> None:
    """Generate the Tauri updater manifest (latest.json)."""

    if not artifact_path.exists():
        print(f"ERROR: artifact not found: {artifact_path}")
        sys.exit(1)
    if not artifact_path.name.endswith(".app.tar.gz"):
        print("ERROR: macOS updater artifact must be a Tauri-compatible .app.tar.gz bundle")
        print("  DMGs are installer downloads and cannot be consumed by tauri-plugin-updater.")
        sys.exit(1)

    size = _file_size(artifact_path)
    sha256 = _sha256(artifact_path)

    if not signing_key and not allow_unsigned:
        print("ERROR: updater manifest signing key is required for release manifests")
        print("  Pass --signing-key /path/to/key.pem or use --allow-unsigned for local smoke fixtures only.")
        sys.exit(1)

    signature = _sign_file(artifact_path, signing_key, env=signing_env) if signing_key else None
    if not signature and not allow_unsigned:
        print("ERROR: updater manifest signing failed or returned an empty signature")
        sys.exit(1)

    if release_url is None:
        release_url = f"https://github.com/johnnyrobot/grader-releases/releases/download/v{version}/{artifact_path.name}"

    pub_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest: dict = {
        "version": version,
        "notes": f"Grader v{version}",
        "pub_date": pub_date,
        "platforms": {
            "darwin-aarch64": {
                "signature": signature or "",
                "url": release_url,
            },
            "darwin-x86_64": {
                "signature": signature or "",
                "url": release_url,
            },
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2))
    print(f"Generated: {output}")
    print(f"  version: {version}")
    print(f"  artifact: {artifact_path.name} ({size / 1024 / 1024:.1f} MB)")
    print(f"  sha256: {sha256}")
    if signature:
        print(f"  signature: {signature[:40]}...")
    else:
        print("  signature: (unsigned smoke fixture — not valid for release)")
    print(f"  url: {release_url}")
    print()
    print("Upload to GitHub Releases:")
    print(
        f"  uv run python scripts/publish_local_release.py --updater-bundle {artifact_path} "
        f"--manifest {output} --version {version} --apply"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Tauri updater manifest (latest.json)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--updater-bundle", type=Path, help="Path to signed Grader.app.tar.gz")
    group.add_argument("--app", type=Path, help="Path to signed .app (will be packaged as .app.tar.gz)")
    parser.add_argument("--version", required=True, help="Version string (e.g. 0.1.1)")
    parser.add_argument("--output", type=Path, default=Path("latest.json"),
                        help="Output manifest path (default: latest.json)")
    parser.add_argument("--signing-key", type=Path, default=None,
                        help="Path to Tauri signing key (.pem)")
    parser.add_argument("--release-url", default=None,
                        help="Download URL (default: GitHub releases URL)")
    parser.add_argument("--password-keychain-service", default=None,
                        help="Read updater key password from this macOS Keychain service without printing it")
    parser.add_argument("--password-keychain-account", default=None,
                        help="Optional macOS Keychain account for --password-keychain-service")
    parser.add_argument("--bundle-output", type=Path, default=None,
                        help="When --app is used, where to write the .app.tar.gz artifact")
    parser.add_argument("--allow-unsigned", action="store_true",
                        help="Allow unsigned manifest generation for local smoke fixtures only")

    args = parser.parse_args()

    if args.updater_bundle:
        artifact = args.updater_bundle
    else:
        app = args.app
        if not app.exists():
            print(f"ERROR: app not found: {app}")
            sys.exit(1)
        artifact = _package_app(
            app,
            args.bundle_output or app.with_name(f"{app.name}.tar.gz"),
        )

    signing_env = None
    if args.password_keychain_service and not os.environ.get("TAURI_SIGNING_PRIVATE_KEY_PASSWORD"):
        password = _load_password_from_keychain(
            args.password_keychain_service,
            args.password_keychain_account,
        )
        if password:
            signing_env = dict(os.environ)
            signing_env["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"] = password
        else:
            print("ERROR: updater key password was not found in macOS Keychain")
            print("  Store it first with security add-generic-password, or set TAURI_SIGNING_PRIVATE_KEY_PASSWORD in a private release shell.")
            sys.exit(1)

    generate_manifest(
        artifact_path=artifact,
        version=args.version,
        output=args.output,
        signing_key=args.signing_key,
        release_url=args.release_url,
        allow_unsigned=args.allow_unsigned,
        signing_env=signing_env,
    )


if __name__ == "__main__":
    main()
