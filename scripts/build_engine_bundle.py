#!/usr/bin/env python3
"""Assemble the engine bundle that ships inside Grader.app.

Grader's engine is Python. Until #23 the app shipped none of it and went looking for a
source checkout at runtime, so it could only ever run on the machine that built it.
This script produces the directory Tauri copies into ``Grader.app/Contents/Resources``:

    app/src-tauri/resources/
      engine/                 the engine source, minus caches and test fixtures
      runtime/
        bin/python3           a relocatable CPython, copied out of uv's managed store
        lib/python3.x/        its standard library
        site-packages/        the project's runtime dependencies
      BUNDLE.json             what was built, from what, and when

Nothing in the result refers to this checkout. ``scripts/verify_engine_bundle.py``
is what proves that; this script only builds.

Usage:
    uv run python scripts/build_engine_bundle.py            # build (reuses a fresh bundle)
    uv run python scripts/build_engine_bundle.py --force    # rebuild from scratch
    uv run python scripts/build_engine_bundle.py --check    # verify a bundle exists and is complete
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ports import source_revision

BUNDLE_DIR = PROJECT_ROOT / "app" / "src-tauri" / "resources"
ENGINE_DEST = BUNDLE_DIR / "engine"
RUNTIME_DEST = BUNDLE_DIR / "runtime"
SITE_PACKAGES = RUNTIME_DEST / "site-packages"
MANIFEST = BUNDLE_DIR / "BUNDLE.json"

# Kept in step with PYTHON_EXECUTABLE in app/src-tauri/src/engine.rs.
PYTHON_EXECUTABLE = "python3"

# The interpreter the bundle carries. Pinned rather than "whatever is installed", so a
# rebuild on another machine produces the same engine.
PYTHON_VERSION = "3.13"

# Engine sources that are development scaffolding, not product.
ENGINE_EXCLUDES = (
    "__pycache__",
    "tests",
    "gates",
    ".pytest_cache",
)
ENGINE_EXCLUDE_SUFFIXES = (".pyc", ".pyo")

# Installed weight that no runtime import path reaches. Everything here is either a
# package's own test suite or headers for compiling against it.
SITE_PACKAGES_PRUNE = (
    "PyObjCTest",
    "torch/include",
    "torch/test",
    "torchvision/include",
)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    printable = " ".join(str(c) for c in cmd)
    print(f"  $ {printable}", flush=True)
    return subprocess.run(cmd, check=True, text=True, **kwargs)


def runtime_dependencies() -> list[str]:
    """The project's runtime dependencies. Dev extras are deliberately not bundled."""
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    return list(pyproject["project"]["dependencies"])


def managed_python_root() -> Path:
    """Locate a relocatable CPython in uv's managed store, installing it if absent.

    A Homebrew or system Python is not usable here: those are not relocatable and
    would reintroduce a dependency on the build machine's filesystem.
    """
    run(["uv", "python", "install", PYTHON_VERSION])
    found = subprocess.run(
        # --system so uv reports the managed interpreter itself rather than the project's
        # virtualenv, which points back at this checkout and carries its own site-packages.
        ["uv", "python", "find", "--managed-python", "--system", PYTHON_VERSION],
        check=True,
        text=True,
        capture_output=True,
        env={**os.environ, "VIRTUAL_ENV": ""},
    ).stdout.strip()
    interpreter = Path(found)
    # <root>/bin/python3.13 -> <root>
    root = interpreter.parent.parent
    if not (root / "lib").is_dir():
        raise SystemExit(
            f"uv reported an interpreter at {interpreter}, but {root} does not look like a "
            "standalone Python install. Refusing to bundle a non-relocatable interpreter."
        )
    if (root / "pyvenv.cfg").is_file():
        raise SystemExit(
            f"uv reported a virtualenv at {root}, not a standalone Python install. A virtualenv "
            "records absolute paths into this checkout and would not survive being copied into "
            "an app bundle."
        )
    return root


def copy_runtime(source_root: Path) -> None:
    print(f"Copying the interpreter from {source_root}")
    if RUNTIME_DEST.exists():
        shutil.rmtree(RUNTIME_DEST)
    shutil.copytree(source_root, RUNTIME_DEST, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))

    python = RUNTIME_DEST / "bin" / PYTHON_EXECUTABLE
    if not python.exists():
        raise SystemExit(f"the copied runtime has no {PYTHON_EXECUTABLE} at {python}")
    python.chmod(0o755)


def install_dependencies() -> None:
    deps = runtime_dependencies()
    print(f"Installing {len(deps)} runtime dependencies into {SITE_PACKAGES}")
    SITE_PACKAGES.mkdir(parents=True, exist_ok=True)
    run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(RUNTIME_DEST / "bin" / PYTHON_EXECUTABLE),
            "--target",
            str(SITE_PACKAGES),
            *deps,
        ]
    )


def prune_site_packages() -> int:
    """Drop installed weight nothing imports. Returns bytes removed."""
    removed = 0
    for relative in SITE_PACKAGES_PRUNE:
        target = SITE_PACKAGES / relative
        if target.is_dir():
            removed += directory_size(target)
            shutil.rmtree(target)
    for cache in SITE_PACKAGES.rglob("__pycache__"):
        if cache.is_dir():
            removed += directory_size(cache)
            shutil.rmtree(cache, ignore_errors=True)
    return removed


def copy_engine_source() -> None:
    print(f"Copying the engine source into {ENGINE_DEST}")
    if ENGINE_DEST.exists():
        shutil.rmtree(ENGINE_DEST)

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {name for name in names if name in ENGINE_EXCLUDES}
        skipped |= {name for name in names if name.endswith(ENGINE_EXCLUDE_SUFFIXES)}
        return skipped

    shutil.copytree(PROJECT_ROOT / "engine", ENGINE_DEST, ignore=ignore)

    if not (ENGINE_DEST / "api.py").is_file():
        raise SystemExit("the copied engine has no api.py")


def directory_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def write_manifest(python_source: Path) -> dict:
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        # scripts/ports.py is the one git-revision seam the evidence scripts share.
        "source_revision": source_revision(PROJECT_ROOT),
        "python_version": PYTHON_VERSION,
        "python_source": str(python_source),
        "dependencies": runtime_dependencies(),
        "engine_bytes": directory_size(ENGINE_DEST),
        "runtime_bytes": directory_size(RUNTIME_DEST),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def check() -> int:
    """Report whether a complete bundle is present, in the same terms engine.rs uses."""
    problems = []
    if not (ENGINE_DEST / "api.py").is_file():
        problems.append(f"engine_missing: no {ENGINE_DEST / 'api.py'}")
    if not (RUNTIME_DEST / "bin" / PYTHON_EXECUTABLE).is_file():
        problems.append(f"runtime_missing: no {RUNTIME_DEST / 'bin' / PYTHON_EXECUTABLE}")
    if not SITE_PACKAGES.is_dir():
        problems.append(f"runtime_missing: no {SITE_PACKAGES}")
    elif not (SITE_PACKAGES / "uvicorn").is_dir():
        problems.append(f"runtime_missing: {SITE_PACKAGES} has no uvicorn")
    if not MANIFEST.is_file():
        problems.append(f"no bundle manifest at {MANIFEST}")

    if problems:
        print("Engine bundle is incomplete:")
        for problem in problems:
            print(f"  - {problem}")
        print("\nBuild it: uv run python scripts/build_engine_bundle.py")
        return 1

    manifest = json.loads(MANIFEST.read_text())
    revision = manifest.get("source_revision")
    # Bundles built before source_revision moved to scripts/ports.py carry a bare string.
    commit = revision.get("commit") if isinstance(revision, dict) else revision
    commit = str(commit or "an unknown revision")[:12]
    print(f"Engine bundle present ({human(directory_size(BUNDLE_DIR))})")
    print(f"  built at {manifest['built_at']} from {commit}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify a complete bundle exists; build nothing")
    parser.add_argument("--force", action="store_true", help="discard any existing bundle first")
    parser.add_argument("--skip-runtime", action="store_true", help="refresh only the engine source (development)")
    args = parser.parse_args()

    if args.check:
        return check()

    if args.force and BUNDLE_DIR.exists():
        print(f"Discarding {BUNDLE_DIR}")
        shutil.rmtree(BUNDLE_DIR)

    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)

    if args.skip_runtime:
        if not (RUNTIME_DEST / "bin" / PYTHON_EXECUTABLE).is_file():
            raise SystemExit("--skip-runtime needs an existing runtime; run without it first")
        python_source = Path(json.loads(MANIFEST.read_text())["python_source"]) if MANIFEST.is_file() else Path("reused")
    else:
        python_source = managed_python_root()
        copy_runtime(python_source)
        install_dependencies()
        pruned = prune_site_packages()
        print(f"Pruned {human(pruned)} of tests and headers")

    copy_engine_source()
    manifest = write_manifest(python_source)

    print()
    print("Engine bundle built:")
    print(f"  engine   {human(manifest['engine_bytes'])}")
    print(f"  runtime  {human(manifest['runtime_bytes'])}")
    print(f"  total    {human(directory_size(BUNDLE_DIR))}")
    print()
    print("Verify it runs without this checkout: uv run python scripts/verify_engine_bundle.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
