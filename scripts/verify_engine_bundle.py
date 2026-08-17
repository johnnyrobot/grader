#!/usr/bin/env python3
"""Prove the bundled engine runs somewhere this repository is not.

Issue #23: every check that runs where the source lives is blind to whether the product
can leave it. This script removes the three things the old engine quietly depended on —
the checkout, ``uv``, and a system Python — and then asks the engine to serve.

What it does:

  1. Relocates the bundle to a temporary directory outside this repository, which is
     also the only test of whether the interpreter survives being moved.
  2. Launches it the way ``app/src-tauri/src/engine.rs`` does, under an environment with
     no ``uv``, no Homebrew, a throwaway ``HOME``, and a working directory outside the
     checkout.
  3. Waits for ``/health``, then checks an authenticated endpoint so the token path is
     exercised too.
  4. Asks ``lsof`` whether the running engine has any file open inside this repository.
     That is the check the clean-machine runbook could not previously make.

It is not a substitute for running the signed app on a Mac that has never held this
repository — the checkout still exists on this disk, merely unused. It is the check that
fails fast on the build machine, so the manual one is not the first to find out.

Usage:
    uv run python scripts/verify_engine_bundle.py
    uv run python scripts/verify_engine_bundle.py --app /Volumes/Grader/Grader.app
    uv run python scripts/verify_engine_bundle.py --json artifacts/engine-bundle-verification.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Kept in step with app/src-tauri/src/engine.rs.
ENGINE_SUBDIR = "engine"
RUNTIME_SUBDIR = "runtime"
PYTHON_EXECUTABLE = "python3"
INHERITED_ENV_TO_DROP = ("PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE")

STARTUP_TIMEOUT_SECONDS = 180.0

# Deliberately excludes /opt/homebrew and ~/.local: uv and a Homebrew Python must not be
# reachable, because a tester's Mac will not have them.
STERILE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class VerificationFailed(Exception):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def locate_bundle(app: Path | None) -> Path:
    if app is not None:
        resources = app / "Contents" / "Resources"
        if not resources.is_dir():
            raise VerificationFailed(f"{app} has no Contents/Resources")
        return resources

    built = PROJECT_ROOT / "app" / "src-tauri" / "resources"
    if not (built / ENGINE_SUBDIR / "api.py").is_file():
        raise VerificationFailed(
            f"no engine bundle at {built}. Build it: uv run python scripts/build_engine_bundle.py"
        )
    return built


def relocate(bundle: Path, destination: Path) -> Path:
    """Copy the bundle outside the repository, cloning blocks where the filesystem allows."""
    log(f"Relocating the bundle to {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        # -c clones on APFS, which makes a 1 GB bundle cheap to move.
        subprocess.run(["/bin/cp", "-Rc", str(bundle) + "/.", str(destination)], check=True)
    except subprocess.CalledProcessError:
        shutil.copytree(bundle, destination, symlinks=True, dirs_exist_ok=True)
    return destination


def sterile_environment(home: Path, token: str, layout: dict[str, Path]) -> dict[str, str]:
    """The environment engine.rs produces, minus everything this machine happens to have."""
    env = {
        "HOME": str(home),
        "PATH": STERILE_PATH,
        "TMPDIR": str(home / "tmp"),
        "GRADER_ENGINE_TOKEN": token,
        # The engine defaults to `fixtures/` beside its working directory, which is inside
        # the bundle. engine.rs sets this for the same reason.
        "GRADER_DATA_DIR": str(home / "data"),
        "PYTHONPATH": os.pathsep.join(
            [str(layout["site_packages"]), str(layout["root"]), str(layout["engine"])]
        ),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    for dropped in INHERITED_ENV_TO_DROP:
        env.pop(dropped, None)
    return env


def wait_for_health(port: int, process: subprocess.Popen, log_path: Path) -> float:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    started = time.monotonic()
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise VerificationFailed(
                f"the engine exited with status {process.returncode} before serving.\n"
                f"--- {log_path} ---\n{tail(log_path)}"
            )
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                if response.status == 200:
                    return time.monotonic() - started
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        time.sleep(0.5)

    raise VerificationFailed(
        f"the engine did not answer /health within {STARTUP_TIMEOUT_SECONDS:.0f}s.\n"
        f"--- {log_path} ---\n{tail(log_path)}"
    )


def check_authenticated_endpoint(port: int, token: str) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/queue?assignment_id=default",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise VerificationFailed(f"/api/queue answered {response.status}")
    except urllib.error.HTTPError as e:
        raise VerificationFailed(f"/api/queue answered {e.code}: {e.read()[:400]!r}") from e
    except (urllib.error.URLError, OSError) as e:
        raise VerificationFailed(f"/api/queue could not be reached: {e}") from e


def files_open_inside_repository(pid: int) -> list[str]:
    """Ask the running engine, via lsof, whether it touches this checkout at all."""
    try:
        result = subprocess.run(
            ["/usr/sbin/lsof", "-p", str(pid), "-Fn"],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    root = str(PROJECT_ROOT)
    return sorted(
        {
            line[1:]
            for line in result.stdout.splitlines()
            if line.startswith("n") and line[1:].startswith(root)
        }
    )


def snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def files_written_into_the_bundle(root: Path, before: set[str]) -> list[str]:
    """A signed bundle is sealed. Anything the engine adds to it breaks that seal.

    The engine's database directory defaults to `fixtures/` beside its working directory,
    and that working directory is the bundle's own Resources. Left unset it writes SQLite
    files into the app, where they invalidate the signature and are destroyed by the next
    update.
    """
    return sorted(snapshot(root) - before)


def tail(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(errors="replace")[-limit:].strip()
    except OSError:
        return "(no log)"


def verify(bundle: Path, keep: bool) -> dict:
    workspace = Path(tempfile.mkdtemp(prefix="grader-bundle-verify-"))
    relocated = workspace / "Resources"
    home = workspace / "home"
    (home / "tmp").mkdir(parents=True)
    log_path = workspace / "engine.log"

    try:
        relocate(bundle, relocated)

        layout = {
            "root": relocated,
            "engine": relocated / ENGINE_SUBDIR,
            "site_packages": relocated / RUNTIME_SUBDIR / "site-packages",
            "python": relocated / RUNTIME_SUBDIR / "bin" / PYTHON_EXECUTABLE,
        }
        for name, path in layout.items():
            if not path.exists():
                raise VerificationFailed(f"the relocated bundle has no {name} at {path}")

        token = str(uuid.uuid4())
        port = free_port()
        env = sterile_environment(home, token, layout)
        bundle_before = snapshot(relocated)

        log(f"Launching {layout['python']}")
        log(f"  PATH={STERILE_PATH}  HOME={home}  cwd={layout['root']}")

        with log_path.open("wb") as sink:
            process = subprocess.Popen(
                [
                    str(layout["python"]),
                    "-m",
                    "uvicorn",
                    "engine.api:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=layout["root"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
            )

        try:
            startup_seconds = wait_for_health(port, process, log_path)
            log(f"  /health answered after {startup_seconds:.1f}s")

            check_authenticated_endpoint(port, token)
            log("  /api/queue answered with the per-launch token")

            leaked = files_open_inside_repository(process.pid)
            if leaked:
                raise VerificationFailed(
                    "the engine has files open inside this checkout, so the bundle is not "
                    "self-contained:\n  " + "\n  ".join(leaked[:20])
                )
            log(f"  lsof: no file open under {PROJECT_ROOT}")

            written = files_written_into_the_bundle(relocated, bundle_before)
            if written:
                raise VerificationFailed(
                    "the engine wrote into the app bundle, which would break the code "
                    "signature and lose the teacher's data on the next update:\n  "
                    + "\n  ".join(written[:20])
                )
            log("  the engine wrote nothing into the bundle")
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()

        return {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "bundle": str(bundle),
            "relocated_to": str(relocated),
            "startup_seconds": round(startup_seconds, 2),
            "path": STERILE_PATH,
            "uv_on_path": shutil.which("uv", path=STERILE_PATH) is not None,
            "health_ok": True,
            "authenticated_request_ok": True,
            "files_open_in_checkout": [],
            "files_written_into_bundle": [],
            "runs_without_checkout": True,
        }
    finally:
        if keep:
            log(f"Left the workspace at {workspace}")
        else:
            shutil.rmtree(workspace, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app", type=Path, help="verify a built Grader.app instead of the staged bundle")
    parser.add_argument("--json", type=Path, help="write the result here as evidence")
    parser.add_argument("--keep", action="store_true", help="leave the temporary workspace in place")
    args = parser.parse_args()

    try:
        bundle = locate_bundle(args.app)
        log(f"Verifying the engine bundle at {bundle}")
        result = verify(bundle, keep=args.keep)
    except VerificationFailed as e:
        print(f"\nFAIL: {e}", file=sys.stderr)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(
                json.dumps(
                    {
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                        "runs_without_checkout": False,
                        "failure": str(e),
                    },
                    indent=2,
                )
                + "\n"
            )
        return 1

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n")
        log(f"Wrote {args.json}")

    log("\nPASS: the engine served from outside this checkout, with no uv and no system Python.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
