# Installation

## System Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| macOS | 13.0 (Ventura) | 14.0 (Sonoma) or later |
| RAM | 16 GB | 32 GB+ |
| Storage | 10 GB free | 20 GB free |
| Python | 3.12+ (bundled with uv) | — |
| Ollama | 0.4+ | latest |

## Install Grader

### Download (when available)

1. Download `Grader.dmg` from the [public releases channel](https://github.com/johnnyrobot/grader-releases/releases/latest)
2. Open the DMG, drag Grader.app to Applications
3. Launch Grader — no Gatekeeper warning (signed + notarized)

### Build from source

```bash
git clone https://github.com/johnnyrobot/grader.git
cd grader

# Install dependencies
uv sync

# Install frontend deps
cd app && npm install && cd ..

# Install Ollama
brew install ollama

# Pull models (16 GB tier)
ollama pull qwen3:8b
ollama pull minicpm-v4.6:latest

# Bake context-capped variants (CRITICAL for 16 GB machines)
./scripts/make_models.sh

# Run the engine (for development)
uv run uvicorn engine.api:app --host 127.0.0.1 --port 8756 --reload

# In another terminal, run the frontend
cd app && npm run dev
```

### Build the desktop app

```bash
cd app/src-tauri
cargo build --release

# The app bundle will be at:
# target/release/bundle/macos/Grader.app
```

## Install Ollama

### Homebrew (recommended)

```bash
brew install ollama
ollama serve  # start the daemon
```

### Direct download

Download from [ollama.com](https://ollama.com/download/mac)

## Pull models

Grader uses context-capped model variants to fit in 16 GB RAM:

```bash
# Pull the base models
ollama pull qwen3:8b          # text grading (~5 GB)
ollama pull minicpm-v4.6:latest  # vision/handwriting (~3 GB)

# Bake capped variants (creates grader-text:8b, grader-vision:1.3b)
./scripts/make_models.sh
```

The `make_models.sh` script creates context-capped versions:
- `grader-text:8b` — Qwen3 8B with 16384-token context (instead of 262k default)
- `grader-vision:1.3b` — MiniCPM-V with 4096-token context

Without capped variants, Ollama loads models at full default context. Qwen3's 262k default
made a 4B model consume 41 GB on a 16 GB machine (P0 finding).

## First-run setup

When you launch Grader for the first time:

1. **Engine starts** — Grader launches the Python engine as a sidecar process
2. **Canvas token** — paste your Canvas LMS access token (or skip for local-only mode)
3. **Rubric** — paste or import a rubric for your assignment
4. **Models** — verify Ollama is running and models are installed (Model Manager screen)
5. **Import** — pull submissions from Canvas, or load local files

## Data storage

| Data | Location | Notes |
|---|---|---|
| Engine state | `$GRADER_DATA_DIR/*.db` (default `fixtures/*.db`) | SQLite (submissions, grade records, audit log); selected when the engine starts |
| Rubrics | `$GRADER_DATA_DIR/rubrics.db` (default `fixtures/rubrics.db`) | SQLite |
| Exemplars | `$GRADER_DATA_DIR/exemplars.db` (default `fixtures/exemplars.db`) | SQLite + embeddings |
| Canvas downloads | Settings > General > Canvas attachment directory | Applies to new Canvas attachment downloads |
| Offline Canvas post queue | `$GRADER_DATA_DIR/post_queue.db` (default `fixtures/post_queue.db`) | Durable pending posts; selected when the engine starts |
| Engine settings | `$GRADER_DATA_DIR/settings.json` (default `fixtures/settings.json`) | Canvas token is local, masked in the UI, and stored owner-only |
| Interface preferences | `~/Library/Application Support/Grader/` | Tauri/WebView-managed appearance and onboarding state |
| Models | `~/.ollama/models/` | Ollama-managed |

All data stays on your device. No student work is transmitted anywhere. See
[`docs/FERPA.md`](FERPA.md) for the privacy audit.

## Troubleshooting

### "Engine not found" on launch

The app looks for the engine directory in this order:
1. Relative to the app bundle (production builds)
2. `CARGO_MANIFEST_DIR/../../engine` (dev builds)
3. Current working directory

If none are found, the app runs in limited mode. Start the engine manually:
```bash
uv run uvicorn engine.api:app --host 127.0.0.1 --port 8756
```

### "uv not found"

Install uv:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Ollama connection refused

```bash
ollama serve  # starts the daemon on port 11434
ollama list   # verify models are available
```

### Models take too much memory

Use the capped variants from `make_models.sh`. Check memory usage:
```bash
ollama ps  # shows loaded models + memory
```

### Gatekeeper warning

If you see "Grader cannot be opened because the developer cannot be verified":
- This means the app isn't signed. For dev builds, run:
  ```bash
  xattr -cr /Applications/Grader.app
  ```
- For release builds, make sure you downloaded the signed DMG from GitHub Releases.

## Uninstall

```bash
# Remove the app
rm -rf /Applications/Grader.app

# Remove data (optional)
rm -rf fixtures/*.db ~/Library/Application\ Support/Grader/

# Remove models (optional)
ollama rm grader-text:8b grader-vision:1.3b nomic-embed-text:latest
```
