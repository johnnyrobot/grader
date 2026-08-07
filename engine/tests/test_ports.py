"""The macOS tooling the release scripts drive, tested once instead of thrice.

These commands are claims. `codesign --verify --deep --strict` is what this
project means by "signed", and a copy that quietly drops `--deep` reports a pass
the original would fail. Pinning the argv is the point of most of these.
"""

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.ports import (  # noqa: E402  (needs the path insert above)
    CodeSigner,
    DiskImage,
    find_app_bundle,
    run_command,
    source_revision,
)


def _ok(stdout=""):
    return lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout, "")


def _recording(results=None):
    """A runner that logs the argv it was handed."""
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        code, out = (results or {}).get(tuple(command[:2]), (0, ""))
        return subprocess.CompletedProcess(command, code, out, "")

    return calls, runner


# ---------------------------------------------------------------------------
# run_command reports, it does not raise
# ---------------------------------------------------------------------------

def test_a_command_that_succeeds_is_reported_ok():
    result = run_command(["true"], runner=_ok("hello"))

    assert result["ok"] is True
    assert result["returncode"] == 0
    assert result["output"] == "hello"


def test_a_nonzero_exit_is_a_finding_not_a_failure_to_report():
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "not signed")

    result = run_command(["codesign"], runner=runner)

    assert result["ok"] is False
    assert result["output"] == "not signed"


def test_a_missing_tool_is_reported_rather_than_raised():
    """An evidence run must not lose every check that already passed."""
    def runner(command, **kwargs):
        raise FileNotFoundError("no such tool: spctl")

    result = run_command(["spctl"], runner=runner)

    assert result["ok"] is False
    assert result["returncode"] == 127
    assert "spctl" in result["output"]


def test_a_wedged_tool_times_out_into_a_finding():
    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 60)

    result = run_command(["hdiutil"], runner=runner)

    assert result["ok"] is False
    assert "hdiutil" in result["output"]


def test_output_is_truncated_so_one_check_cannot_swamp_a_report():
    result = run_command(["noisy"], runner=_ok("x" * 5000))

    assert len(result["output"]) == 2000


def test_truncation_can_be_waived_for_output_that_gets_parsed():
    """An hdiutil plist cut off at 2000 characters no longer parses."""
    result = run_command(["hdiutil"], runner=_ok("x" * 5000), output_limit=None)

    assert len(result["output"]) == 5000


# ---------------------------------------------------------------------------
# Which revision produced the artifact
# ---------------------------------------------------------------------------

def test_source_revision_reports_the_commit_and_when_it_landed():
    def runner(command, **kwargs):
        out = "abc123\n" if "rev-parse" in command else "1700000000\n"
        return subprocess.CompletedProcess(command, 0, out, "")

    revision = source_revision(Path("/repo"), runner=runner)

    assert revision["available"] is True
    assert revision["commit"] == "abc123"
    assert revision["commit_time_epoch"] == 1700000000
    assert revision["commit_time"].startswith("2023-11-14")


def test_source_revision_is_scoped_to_the_project_it_was_given():
    calls, runner = _recording()

    source_revision(Path("/repo"), runner=runner)

    assert all(command[:3] == ["git", "-C", "/repo"] for command in calls)


def test_a_checkout_without_git_is_unavailable_not_an_error():
    """A tarball checkout can still produce a valid signing report."""
    def runner(command, **kwargs):
        raise FileNotFoundError("git not installed")

    revision = source_revision(Path("/repo"), runner=runner)

    assert revision["available"] is False
    assert "git" in revision["reason"]


def test_an_unparseable_commit_time_is_unavailable_not_a_crash():
    def runner(command, **kwargs):
        out = "abc123\n" if "rev-parse" in command else "not-a-timestamp\n"
        return subprocess.CompletedProcess(command, 0, out, "")

    assert source_revision(Path("/repo"), runner=runner)["available"] is False


# ---------------------------------------------------------------------------
# What "signed" means, spelled one way
# ---------------------------------------------------------------------------

def test_verifying_an_app_checks_nested_code_too():
    """Without --deep, an unsigned embedded framework passes."""
    calls, runner = _recording()

    CodeSigner(runner=runner).verify_app(Path("/A.app"))

    assert calls == [["codesign", "--verify", "--deep", "--strict", "/A.app"]]


def test_assessing_an_app_asks_what_gatekeeper_does_on_launch():
    calls, runner = _recording()

    CodeSigner(runner=runner).assess_app(Path("/A.app"))

    assert calls == [["spctl", "--assess", "--type", "execute", "/A.app"]]


def test_assessing_an_installer_asks_about_its_primary_signature():
    calls, runner = _recording()

    CodeSigner(runner=runner).assess_installer(Path("/G.dmg"))

    assert calls == [["spctl", "--assess", "--type", "open", "--context",
                      "context:primary-signature", "/G.dmg"]]


def test_validating_the_staple_asks_whether_first_launch_works_offline():
    calls, runner = _recording()

    CodeSigner(runner=runner).validate_staple(Path("/G.dmg"))

    assert calls == [["xcrun", "stapler", "validate", "/G.dmg"]]


# ---------------------------------------------------------------------------
# Looking inside the image that actually ships
# ---------------------------------------------------------------------------

def _attach_plist(mount_point):
    return plistlib.dumps({"system-entities": [{"mount-point": str(mount_point)}]}).decode()


def _dmg_runner(mount_point, *, attach_ok=True, plist=None):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[:2] == ["hdiutil", "attach"]:
            if not attach_ok:
                return subprocess.CompletedProcess(command, 1, "", "resource busy")
            body = _attach_plist(mount_point) if plist is None else plist
            return subprocess.CompletedProcess(command, 0, body, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    return calls, runner


def test_mounting_yields_the_mount_point_and_then_detaches(tmp_path):
    calls, runner = _dmg_runner(tmp_path)

    with DiskImage(Path("/G.dmg"), runner=runner).mounted() as (mount_point, records):
        assert mount_point == tmp_path
        assert records["mount_point"] == str(tmp_path)

    assert calls[0][:2] == ["hdiutil", "attach"]
    assert calls[-1] == ["hdiutil", "detach", str(tmp_path)]
    assert records["hdiutil_detach"]["ok"] is True


def test_the_image_is_detached_even_when_the_caller_raises(tmp_path):
    """A left-behind mount fails the next run for an unrelated reason."""
    calls, runner = _dmg_runner(tmp_path)

    with pytest.raises(RuntimeError):
        with DiskImage(Path("/G.dmg"), runner=runner).mounted():
            raise RuntimeError("verification blew up")

    assert calls[-1] == ["hdiutil", "detach", str(tmp_path)]


def test_the_image_is_mounted_read_only_and_unbrowsable():
    """Mounting a release artifact must not be able to modify it."""
    calls, runner = _dmg_runner(Path("/Volumes/G"))

    with DiskImage(Path("/G.dmg"), runner=runner).mounted():
        pass

    assert calls[0] == ["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", "/G.dmg"]


def test_a_dmg_that_will_not_mount_reports_why(tmp_path):
    _, runner = _dmg_runner(tmp_path, attach_ok=False)

    with DiskImage(Path("/G.dmg"), runner=runner).mounted() as (mount_point, records):
        assert mount_point is None
        assert "could not mount" in records["reason"]


def test_unparseable_mount_metadata_reports_why(tmp_path):
    _, runner = _dmg_runner(tmp_path, plist="not a plist")

    with DiskImage(Path("/G.dmg"), runner=runner).mounted() as (mount_point, records):
        assert mount_point is None
        assert "could not parse" in records["reason"]


def test_a_mount_that_reports_no_mount_point_says_so():
    _, runner = _dmg_runner(None, plist=plistlib.dumps({"system-entities": []}).decode())

    with DiskImage(Path("/G.dmg"), runner=runner).mounted() as (mount_point, records):
        assert mount_point is None
        assert "did not report a mount point" in records["reason"]


def test_nothing_is_detached_when_nothing_mounted(tmp_path):
    calls, runner = _dmg_runner(tmp_path, attach_ok=False)

    with DiskImage(Path("/G.dmg"), runner=runner).mounted():
        pass

    assert not any(command[:2] == ["hdiutil", "detach"] for command in calls)


# ---------------------------------------------------------------------------
# Finding the bundle
# ---------------------------------------------------------------------------

def test_a_top_level_app_is_preferred_over_a_nested_one(tmp_path):
    (tmp_path / "Grader.app").mkdir()
    (tmp_path / "extras").mkdir()
    (tmp_path / "extras" / "Helper.app").mkdir()

    assert find_app_bundle(tmp_path) == tmp_path / "Grader.app"


def test_a_nested_app_is_found_when_there_is_no_top_level_one(tmp_path):
    (tmp_path / "extras").mkdir()
    (tmp_path / "extras" / "Grader.app").mkdir()

    assert find_app_bundle(tmp_path) == tmp_path / "extras" / "Grader.app"


def test_a_directory_with_no_app_yields_nothing(tmp_path):
    (tmp_path / "README.txt").write_text("no bundle here")

    assert find_app_bundle(tmp_path) is None


def test_an_unreadable_directory_yields_nothing_rather_than_raising(tmp_path):
    assert find_app_bundle(tmp_path / "does-not-exist") is None
