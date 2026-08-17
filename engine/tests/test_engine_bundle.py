"""Tests for the engine bundle build and its self-containment check.

Issue #23: the app shipped no engine and went looking for a source checkout. These cover
the two scripts that replace that arrangement — the one that builds the bundle and the one
that proves it runs where this repository is not.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    """Load a script from scripts/ that is not an importable package."""
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_engine_bundle = _load("build_engine_bundle")
verify_engine_bundle = _load("verify_engine_bundle")


ENGINE_RS = PROJECT_ROOT / "app" / "src-tauri" / "src" / "engine.rs"


def _rust_constant(name: str) -> str:
    """Read a `pub const NAME: &str = "value";` out of engine.rs."""
    for line in ENGINE_RS.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(f"pub const {name}:"):
            return stripped.split('"')[1]
    raise AssertionError(f"engine.rs has no constant {name}")


class TestLayoutAgreesWithTheShell:
    """The Rust shell and these scripts must mean the same thing by "the bundle"."""

    @pytest.mark.parametrize(
        "python_name, rust_name",
        [
            ("ENGINE_SUBDIR", "ENGINE_SUBDIR"),
            ("RUNTIME_SUBDIR", "RUNTIME_SUBDIR"),
            ("PYTHON_EXECUTABLE", "PYTHON_EXECUTABLE"),
        ],
    )
    def test_build_script_matches_engine_rs(self, python_name, rust_name):
        if python_name == "PYTHON_EXECUTABLE":
            assert build_engine_bundle.PYTHON_EXECUTABLE == _rust_constant(rust_name)
        else:
            # The build script derives these from its destination paths.
            expected = _rust_constant(rust_name)
            destination = {
                "ENGINE_SUBDIR": build_engine_bundle.ENGINE_DEST,
                "RUNTIME_SUBDIR": build_engine_bundle.RUNTIME_DEST,
            }[python_name]
            assert destination.name == expected

    @pytest.mark.parametrize("name", ["ENGINE_SUBDIR", "RUNTIME_SUBDIR", "PYTHON_EXECUTABLE"])
    def test_verify_script_matches_engine_rs(self, name):
        assert getattr(verify_engine_bundle, name) == _rust_constant(name)

    def test_dropped_environment_matches_engine_rs(self):
        source = ENGINE_RS.read_text()
        for dropped in verify_engine_bundle.INHERITED_ENV_TO_DROP:
            assert dropped in source, f"engine.rs does not drop {dropped}"


class TestRuntimeDependencies:
    def test_bundles_the_projects_runtime_dependencies(self):
        deps = build_engine_bundle.runtime_dependencies()

        assert deps, "the engine cannot run with no dependencies"
        names = " ".join(deps)
        assert "fastapi" in names and "uvicorn" in names

    def test_does_not_bundle_development_extras(self):
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            pyproject = tomllib.load(handle)
        dev_extras = pyproject["project"]["optional-dependencies"]["dev"]

        deps = build_engine_bundle.runtime_dependencies()

        for extra in dev_extras:
            assert extra not in deps, f"{extra} is a development dependency; it must not ship"


class TestBundleCompletenessCheck:
    """`--check` is what stops a build without an engine reaching the signing script."""

    def _stage(self, root: Path, *, engine=True, python=True, site_packages=True, uvicorn=True, manifest=True):
        bundle = root / "resources"
        engine_dest = bundle / "engine"
        runtime_dest = bundle / "runtime"
        site = runtime_dest / "site-packages"

        if engine:
            engine_dest.mkdir(parents=True)
            (engine_dest / "api.py").write_text("app = None\n")
        if python:
            (runtime_dest / "bin").mkdir(parents=True)
            (runtime_dest / "bin" / build_engine_bundle.PYTHON_EXECUTABLE).write_text("#!/bin/sh\n")
        if site_packages:
            site.mkdir(parents=True, exist_ok=True)
            if uvicorn:
                (site / "uvicorn").mkdir()
        if manifest:
            bundle.mkdir(parents=True, exist_ok=True)
            (bundle / "BUNDLE.json").write_text(
                '{"built_at": "now", "source_revision": {"available": true, "commit": "abcdef123456"}}'
            )

        return {
            "BUNDLE_DIR": bundle,
            "ENGINE_DEST": engine_dest,
            "RUNTIME_DEST": runtime_dest,
            "SITE_PACKAGES": site,
            "MANIFEST": bundle / "BUNDLE.json",
        }

    def _run_check(self, monkeypatch, paths):
        for name, value in paths.items():
            monkeypatch.setattr(build_engine_bundle, name, value)
        return build_engine_bundle.check()

    def test_a_complete_bundle_passes(self, tmp_path, monkeypatch):
        assert self._run_check(monkeypatch, self._stage(tmp_path)) == 0

    def test_a_missing_engine_fails(self, tmp_path, monkeypatch):
        assert self._run_check(monkeypatch, self._stage(tmp_path, engine=False)) == 1

    def test_a_missing_interpreter_fails(self, tmp_path, monkeypatch):
        assert self._run_check(monkeypatch, self._stage(tmp_path, python=False)) == 1

    def test_libraries_without_uvicorn_fail(self, tmp_path, monkeypatch):
        assert self._run_check(monkeypatch, self._stage(tmp_path, uvicorn=False)) == 1

    def test_an_absent_bundle_fails_rather_than_raising(self, tmp_path, monkeypatch):
        paths = self._stage(tmp_path, engine=False, python=False, site_packages=False, manifest=False)
        assert self._run_check(monkeypatch, paths) == 1


class TestSterileEnvironment:
    """The environment the engine is verified under must not contain what a tester lacks."""

    def _environment(self, tmp_path):
        layout = {
            "root": tmp_path / "Resources",
            "engine": tmp_path / "Resources" / "engine",
            "site_packages": tmp_path / "Resources" / "runtime" / "site-packages",
        }
        return verify_engine_bundle.sterile_environment(tmp_path / "home", "tok", layout), layout

    def test_offers_no_uv_and_no_homebrew(self, tmp_path):
        env, _ = self._environment(tmp_path)

        assert "/opt/homebrew" not in env["PATH"]
        assert ".local/bin" not in env["PATH"]
        assert ".cargo" not in env["PATH"]

    def test_uv_is_genuinely_unreachable_on_that_path(self, tmp_path):
        env, _ = self._environment(tmp_path)

        import shutil

        assert shutil.which("uv", path=env["PATH"]) is None

    def test_points_python_at_the_bundle_only(self, tmp_path):
        env, layout = self._environment(tmp_path)

        entries = env["PYTHONPATH"].split(os.pathsep)
        assert str(layout["site_packages"]) in entries
        assert str(layout["engine"]) in entries
        assert all(entry.startswith(str(tmp_path)) for entry in entries)

    def test_drops_inherited_python_redirection(self, tmp_path):
        env, _ = self._environment(tmp_path)

        for dropped in verify_engine_bundle.INHERITED_ENV_TO_DROP:
            assert dropped not in env
        assert env["PYTHONNOUSERSITE"] == "1"

    def test_gives_the_engine_a_throwaway_home(self, tmp_path):
        env, _ = self._environment(tmp_path)

        assert env["HOME"] == str(tmp_path / "home")
        assert str(PROJECT_ROOT) not in env["HOME"]

    def test_carries_the_per_launch_token(self, tmp_path):
        env, _ = self._environment(tmp_path)

        assert env["GRADER_ENGINE_TOKEN"] == "tok"


class TestLocatingTheBundle:
    def test_an_app_without_resources_is_rejected(self, tmp_path):
        app = tmp_path / "Grader.app"
        app.mkdir()

        with pytest.raises(verify_engine_bundle.VerificationFailed, match="Contents/Resources"):
            verify_engine_bundle.locate_bundle(app)

    def test_an_app_bundle_resolves_to_its_resource_dir(self, tmp_path):
        resources = tmp_path / "Grader.app" / "Contents" / "Resources"
        resources.mkdir(parents=True)

        assert verify_engine_bundle.locate_bundle(tmp_path / "Grader.app") == resources
