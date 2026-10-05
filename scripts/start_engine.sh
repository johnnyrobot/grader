#!/bin/bash
cd /Users/johnnyrobot/code/grader
export PYTHONPATH=engine
export GRADER_ENGINE_TOKEN="$1"
PORT="${2:-8756}"
export GRADER_APP_VERSION="0.1.0"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
exec /opt/homebrew/bin/uv run --no-sync uvicorn engine.api:app --host 127.0.0.1 --port "$PORT"
