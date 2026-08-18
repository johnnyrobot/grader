#!/bin/bash
# Start the engine from a source checkout, for development.
#
# The shipped app does NOT use this script. Grader.app runs the interpreter it carries
# in Contents/Resources/runtime (see app/src-tauri/src/engine.rs); this is the uv-based
# path for working against the checkout, and the only reason it may assume uv and a
# resolved environment.
#
# Usage:
#   ./scripts/start_engine.sh [TOKEN] [PORT]

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONPATH=engine
export GRADER_ENGINE_TOKEN="${1:-dev-token}"
PORT="${2:-8756}"
# Kept literal: scripts/bump_release_version.py and scripts/release_version_guard.py
# both treat this line as a version surface.
export GRADER_APP_VERSION="0.1.3"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv is not on PATH. This is the development launcher; install uv:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

exec uv run --no-sync uvicorn engine.api:app --host 127.0.0.1 --port "$PORT"
