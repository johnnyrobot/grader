"""The macOS tooling the release scripts drive, named once.

Three things were written out longhand in three or more evidence scripts:
asking git what revision produced an artifact, asking codesign and spctl whether
a bundle is signed, and mounting a DMG to look inside it. `_git_source_revision`
was byte-identical in three files; the codesign/spctl command pair and the
hdiutil mount-parse-detach dance were identical in two apiece, wrapped in
different reporting shapes.

Duplicated commands are not a tidiness problem. `codesign --verify --deep
--strict` is a claim about what "signed" means, and a copy that drifts —
one caller dropping `--deep`, say — reports a pass the other would fail, with
no way to tell from the report which one you got.

Only the three ports with a verified second consumer live here. Keychain,
release host, update channel and updater signing stay on the `runner=` keyword
their scripts already use; a port with one caller is an indirection, not a seam.
"""

from __future__ import annotations

import plistlib
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

Runner = Callable[..., Any]

# Long enough for hdiutil on a large image; short enough that a wedged tool
# surfaces as a failed check rather than a hung CI job.
DEFAULT_TIMEOUT = 60
MOUNT_TIMEOUT = 120


def run_command(
    command: list[str],
    *,
    runner: Runner = subprocess.run,
    timeout: int | None = DEFAULT_TIMEOUT,
    output_limit: int | None = 2000,
) -> dict[str, Any]:
    """Run one command and report what it did, rather than raising.

    An evidence script's job is to say what it found. A missing tool or a wedged
    one is a finding — `{"ok": False}` with the reason in `output` — not a
    traceback that loses every check that had already passed.
    """
    kwargs: dict[str, Any] = {"text": True, "capture_output": True, "check": False}
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        proc = runner(command, **kwargs)
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        returncode = proc.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        output, returncode = str(exc), 127
    return {
        "command": command,
        "returncode": returncode,
        "ok": returncode == 0,
        "output": output if output_limit is None else output[:output_limit],
    }


# ---------------------------------------------------------------------------
# What revision produced the artifact under test
# ---------------------------------------------------------------------------

def source_revision(project_root: Path, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    """The commit an artifact should have been built from, and when it landed.

    Evidence scripts compare this timestamp against an artifact's mtime to catch
    a report describing a build older than the code it claims to cover.
    Unavailable (no git, no repository) is reported, never raised: a tarball
    checkout can still produce a valid signing report.
    """
    commit = run_command(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"], runner=runner
    )
    when = run_command(
        ["git", "-C", str(project_root), "log", "-1", "--format=%ct"], runner=runner
    )
    if not commit["ok"] or not when["ok"]:
        reason = (commit["output"] or when["output"] or "git reported no revision")
        return {"available": False, "reason": reason}

    try:
        timestamp = int(when["output"].strip())
    except ValueError as exc:
        return {"available": False, "reason": str(exc)}

    return {
        "available": True,
        "commit": commit["output"].strip(),
        "commit_time_epoch": timestamp,
        "commit_time": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Is this thing signed, and would Gatekeeper let a teacher open it
# ---------------------------------------------------------------------------

class CodeSigner:
    """The signature questions, each asked exactly one way.

    Every method returns a `run_command` record, so a caller reports the command,
    its exit code and its output without having to know which tool answered.
    """

    def __init__(self, *, runner: Runner = subprocess.run) -> None:
        self.runner = runner

    def verify_app(self, app_path: Path) -> dict[str, Any]:
        """--deep --strict: the nested frameworks have to verify too."""
        return run_command(
            ["codesign", "--verify", "--deep", "--strict", str(app_path)],
            runner=self.runner,
        )

    def assess_app(self, app_path: Path) -> dict[str, Any]:
        """What Gatekeeper decides when a teacher double-clicks the app."""
        return run_command(
            ["spctl", "--assess", "--type", "execute", str(app_path)],
            runner=self.runner,
        )

    def assess_installer(self, path: Path) -> dict[str, Any]:
        """What Gatekeeper decides when a teacher opens the downloaded DMG."""
        return run_command(
            ["spctl", "--assess", "--type", "open", "--context",
             "context:primary-signature", str(path)],
            runner=self.runner,
        )

    def validate_staple(self, path: Path) -> dict[str, Any]:
        """Is the notarization ticket attached, so first launch works offline?"""
        return run_command(
            ["xcrun", "stapler", "validate", str(path)], runner=self.runner
        )


# ---------------------------------------------------------------------------
# Looking inside the thing that actually ships
# ---------------------------------------------------------------------------

def find_app_bundle(root: Path) -> Path | None:
    """The .app in a directory tree, preferring one sitting at the top."""
    try:
        children = sorted(root.iterdir())
    except OSError:
        return None
    for child in children:
        if child.is_dir() and child.suffix == ".app":
            return child
    for child in sorted(root.rglob("*.app")):
        if child.is_dir():
            return child
    return None


class DiskImage:
    """A DMG, and the mount it needs to be inspected through.

    The app a teacher installs is the one inside the image, not the one left in
    the build directory — verifying the latter can pass while the shipped bundle
    is unsigned. So this exists to make looking inside the image routine.
    """

    def __init__(self, path: Path, *, runner: Runner = subprocess.run) -> None:
        self.path = path
        self.runner = runner

    @contextmanager
    def mounted(self) -> Iterator[tuple[Path | None, dict[str, Any]]]:
        """Mount read-only, yield the mount point, and always detach.

        Yields `(mount_point, records)`. `mount_point` is None when the image
        could not be mounted or reported no mount point — `records` says which,
        under "hdiutil_attach" and "reason". Detaching happens even if the
        caller raises: a left-behind mount fails the next run for a reason that
        has nothing to do with the build.
        """
        records: dict[str, Any] = {}
        attach = run_command(
            ["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", str(self.path)],
            runner=self.runner, timeout=MOUNT_TIMEOUT, output_limit=None,
        )
        records["hdiutil_attach"] = attach

        mount_point: Path | None = None
        if not attach["ok"]:
            records["reason"] = "could not mount DMG to verify bundled app"
        else:
            try:
                plist = plistlib.loads(str(attach["output"] or "").encode())
                for entity in plist.get("system-entities", []):
                    candidate = entity.get("mount-point")
                    if candidate:
                        mount_point = Path(candidate)
                        break
            except Exception as exc:
                records["reason"] = f"could not parse mounted DMG metadata: {exc}"
            if mount_point is None:
                records.setdefault("reason", "mounted DMG did not report a mount point")

        if mount_point is not None:
            records["mount_point"] = str(mount_point)
        try:
            yield mount_point, records
        finally:
            if mount_point is not None:
                records["hdiutil_detach"] = run_command(
                    ["hdiutil", "detach", str(mount_point)],
                    runner=self.runner, timeout=MOUNT_TIMEOUT,
                )
