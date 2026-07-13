#!/usr/bin/env python3
"""Dry-run-first helper for moving all release version surfaces together.

The release workflow deliberately fails if the GitHub tag and app metadata
drift. This helper is the safe companion step: it prepares the next release
version in the app/package metadata and small runtime fixtures that report the
installed version during updater tests. It performs no git, tag, GitHub, signing,
or publishing operations.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class VersionSurface:
    label: str
    path: Path
    pattern: re.Pattern[str] | None = None
    json_key: str | None = None


DEFAULT_SURFACES = (
    VersionSurface("tauri_config", Path("app/src-tauri/tauri.conf.json"), json_key="version"),
    VersionSurface("frontend_package", Path("app/package.json"), json_key="version"),
    VersionSurface("frontend_package_lock", Path("app/package-lock.json")),
    VersionSurface(
        "tauri_cargo",
        Path("app/src-tauri/Cargo.toml"),
        re.compile(r'(?m)^version\s*=\s*"(?P<version>[^"]+)"'),
    ),
    VersionSurface(
        "tauri_cargo_lock",
        Path("app/src-tauri/Cargo.lock"),
        re.compile(
            r'(?ms)(\[\[package\]\]\s*name\s*=\s*"grader-app"\s*version\s*=\s*")(?P<version>[^"]+)(")'
        ),
    ),
    VersionSurface(
        "python_project",
        Path("pyproject.toml"),
        re.compile(r'(?m)^version\s*=\s*"(?P<version>[^"]+)"'),
    ),
    VersionSurface(
        "engine_launcher",
        Path("scripts/start_engine.sh"),
        re.compile(r'(?m)^export GRADER_APP_VERSION="(?P<version>[^"]+)"'),
    ),
    VersionSurface(
        "dev_mock_update_fixture",
        Path("scripts/dev_mock_engine.py"),
        re.compile(r'"current_version":\s*"(?P<version>[^"]+)"'),
    ),
)


def _repo_path(path: Path, *, root: Path) -> Path:
    return path if path.is_absolute() else root / path


def _validate_version(version: str) -> None:
    if not SEMVER_RE.fullmatch(version):
        raise ValueError(
            f"target version {version!r} is not supported; use semantic version form like 0.1.1"
        )


def _normalize_tag(version: str) -> str:
    value = version.strip()
    if value.startswith("refs/tags/"):
        value = value.removeprefix("refs/tags/")
    if value.startswith("v"):
        value = value[1:]
    return value


def _read_json_surface(path: Path, key: str) -> tuple[str | None, str]:
    text = path.read_text()
    payload = json.loads(text)
    value = payload.get(key)
    return (str(value) if value is not None else None), text


def _write_json_surface(path: Path, key: str, target_version: str) -> None:
    payload = json.loads(path.read_text())
    payload[key] = target_version
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _read_package_lock_surface(path: Path) -> tuple[str | None, str]:
    text = path.read_text()
    payload = json.loads(text)
    root_version = payload.get("version")
    package_version = payload.get("packages", {}).get("", {}).get("version")
    if root_version != package_version:
        raise ValueError(
            f"package-lock root version {root_version!r} differs from packages[''] version {package_version!r}"
        )
    return (str(root_version) if root_version is not None else None), text


def _write_package_lock_surface(path: Path, target_version: str) -> None:
    payload = json.loads(path.read_text())
    payload["version"] = target_version
    payload.setdefault("packages", {}).setdefault("", {})["version"] = target_version
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _read_regex_surface(path: Path, pattern: re.Pattern[str]) -> tuple[str | None, str]:
    text = path.read_text()
    match = pattern.search(text)
    return (match.group("version") if match else None), text


def _write_regex_surface(path: Path, pattern: re.Pattern[str], target_version: str) -> None:
    text = path.read_text()

    def replace(match: re.Match[str]) -> str:
        start, end = match.span("version")
        return match.string[match.start() : start] + target_version + match.string[end : match.end()]

    updated, count = pattern.subn(replace, text, count=1)
    if count != 1:
        raise ValueError(f"could not find a single version field in {path}")
    path.write_text(updated)


def _read_surface(surface: VersionSurface, *, root: Path) -> tuple[str | None, str]:
    path = _repo_path(surface.path, root=root)
    if surface.label == "frontend_package_lock":
        return _read_package_lock_surface(path)
    if surface.json_key:
        return _read_json_surface(path, surface.json_key)
    if surface.pattern:
        return _read_regex_surface(path, surface.pattern)
    raise ValueError(f"unsupported version surface {surface.label}")


def _write_surface(surface: VersionSurface, *, root: Path, target_version: str) -> None:
    path = _repo_path(surface.path, root=root)
    if surface.label == "frontend_package_lock":
        _write_package_lock_surface(path, target_version)
        return
    if surface.json_key:
        _write_json_surface(path, surface.json_key, target_version)
        return
    if surface.pattern:
        _write_regex_surface(path, surface.pattern, target_version)
        return
    raise ValueError(f"unsupported version surface {surface.label}")


def collect_plan(
    *,
    target_version: str,
    root: Path = PROJECT_ROOT,
    surfaces: tuple[VersionSurface, ...] = DEFAULT_SURFACES,
    allow_same: bool = False,
) -> dict[str, Any]:
    target = _normalize_tag(target_version)
    _validate_version(target)

    failures: list[str] = []
    warnings: list[str] = []
    surface_rows: dict[str, dict[str, Any]] = {}
    current_versions: set[str] = set()

    for surface in surfaces:
        path = _repo_path(surface.path, root=root)
        row: dict[str, Any] = {
            "path": str(surface.path),
            "absolute_path": str(path),
            "current_version": None,
            "target_version": target,
            "will_change": False,
        }
        try:
            current, _ = _read_surface(surface, root=root)
        except FileNotFoundError:
            current = None
            failures.append(f"{surface.label} is missing at {surface.path}")
        except Exception as exc:
            current = None
            failures.append(f"{surface.label} version could not be read from {surface.path}: {exc}")
        row["current_version"] = current
        if current is None:
            failures.append(f"{surface.label} version is missing from {surface.path}")
        else:
            current_versions.add(current)
            row["will_change"] = current != target
        surface_rows[surface.label] = row

    if len(current_versions) > 1:
        warnings.append("current version surfaces are already inconsistent: " + ", ".join(sorted(current_versions)))
    if current_versions == {target} and not allow_same:
        failures.append(
            f"all version surfaces already report {target}; pass --allow-same to record a no-op plan"
        )

    status = "PASS" if not failures else "BLOCKED_VERSION_BUMP"
    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "target_version": target,
        "surfaces": surface_rows,
        "failures": failures,
        "warnings": warnings,
    }


def apply_plan(
    *,
    target_version: str,
    root: Path = PROJECT_ROOT,
    surfaces: tuple[VersionSurface, ...] = DEFAULT_SURFACES,
    allow_same: bool = False,
) -> dict[str, Any]:
    plan = collect_plan(
        target_version=target_version,
        root=root,
        surfaces=surfaces,
        allow_same=allow_same,
    )
    if plan["status"] != "PASS":
        return plan | {"applied": False}

    for surface in surfaces:
        _write_surface(surface, root=root, target_version=plan["target_version"])

    applied = collect_plan(
        target_version=plan["target_version"],
        root=root,
        surfaces=surfaces,
        allow_same=True,
    )
    applied["read_only"] = False
    applied["applied"] = True
    if applied["status"] == "PASS":
        applied["status"] = "APPLIED"
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare all Grader release version surfaces together")
    parser.add_argument("--target-version", required=True, help="Next app version, e.g. 0.1.1 or v0.1.1")
    parser.add_argument("--apply", action="store_true", help="Write files. Default is dry-run only.")
    parser.add_argument("--allow-same", action="store_true", help="Allow a no-op plan when every surface already has the target version")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("artifacts/release-evidence/version-bump-plan.json"),
        help="Diagnostic JSON output path",
    )
    args = parser.parse_args()

    result = apply_plan(target_version=args.target_version, allow_same=args.allow_same) if args.apply else collect_plan(
        target_version=args.target_version,
        allow_same=args.allow_same,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"status={result['status']}")
    print(f"json={args.out}")
    if result["failures"]:
        print("failures=" + "; ".join(result["failures"]))
    if result["warnings"]:
        print("warnings=" + "; ".join(result["warnings"]))
    if result["status"] not in {"PASS", "APPLIED"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
