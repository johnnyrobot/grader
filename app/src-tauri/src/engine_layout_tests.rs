//! Tests for engine bundle resolution.
//!
//! The engine is resolved from the app bundle's resource directory and nowhere else.
//! A machine without a checkout of this repository must get the same answer as the
//! build machine, so these tests describe resolution purely in terms of a directory
//! handed in by the caller.

use super::*;
use std::fs;
use std::path::{Path, PathBuf};

/// A throwaway directory that models `Grader.app/Contents/Resources`.
struct FakeResources {
    root: PathBuf,
}

impl FakeResources {
    fn empty(name: &str) -> Self {
        let root = std::env::temp_dir().join(format!("grader-engine-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("create fake resource dir");
        Self { root }
    }

    /// A resource directory as `scripts/build_engine_bundle.py` leaves it.
    fn complete(name: &str) -> Self {
        let fixture = Self::empty(name);
        fixture.with_engine_source();
        fixture.with_runtime();
        fixture
    }

    fn with_engine_source(&self) {
        let engine = self.root.join(ENGINE_SUBDIR);
        fs::create_dir_all(engine.join("grader")).unwrap();
        fs::write(engine.join("api.py"), "app = None\n").unwrap();
    }

    fn with_runtime(&self) {
        let runtime = self.root.join(RUNTIME_SUBDIR);
        fs::create_dir_all(runtime.join("bin")).unwrap();
        fs::create_dir_all(runtime.join("site-packages")).unwrap();
        fs::write(runtime.join("bin").join(PYTHON_EXECUTABLE), "#!/bin/sh\n").unwrap();
    }

    fn path(&self) -> &Path {
        &self.root
    }
}

impl Drop for FakeResources {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn resolves_every_path_beneath_the_resource_dir() {
    let fixture = FakeResources::complete("complete");
    let layout = resolve_engine(fixture.path()).expect("a complete bundle resolves");

    assert_eq!(layout.engine_dir, fixture.path().join("engine"));
    assert_eq!(layout.site_packages, fixture.path().join("runtime/site-packages"));
    assert_eq!(layout.python, fixture.path().join("runtime/bin").join(PYTHON_EXECUTABLE));
}

#[test]
fn every_resolved_path_stays_inside_the_bundle() {
    let fixture = FakeResources::complete("contained");
    let layout = resolve_engine(fixture.path()).unwrap();

    for path in [&layout.engine_dir, &layout.python, &layout.site_packages] {
        assert!(
            path.starts_with(fixture.path()),
            "{} escaped the resource dir",
            path.display()
        );
    }
}

#[test]
fn a_bundle_without_engine_source_names_what_is_missing() {
    let fixture = FakeResources::empty("no-engine");
    fixture.with_runtime();

    let err = resolve_engine(fixture.path()).expect_err("an engine-less bundle cannot resolve");

    assert_eq!(err.kind, "engine_missing");
    assert!(!err.message.is_empty(), "the UI needs something to show");
    assert!(
        err.detail.contains("api.py"),
        "the detail should name the missing file, got: {}",
        err.detail
    );
}

#[test]
fn a_bundle_without_a_python_runtime_names_what_is_missing() {
    let fixture = FakeResources::empty("no-runtime");
    fixture.with_engine_source();

    let err = resolve_engine(fixture.path()).expect_err("a runtime-less bundle cannot resolve");

    assert_eq!(err.kind, "runtime_missing");
    assert!(
        err.detail.contains("runtime"),
        "the detail should name the runtime directory, got: {}",
        err.detail
    );
}

#[test]
fn site_packages_is_required_even_when_the_interpreter_is_present() {
    let fixture = FakeResources::empty("no-site-packages");
    fixture.with_engine_source();
    let runtime = fixture.path().join(RUNTIME_SUBDIR);
    fs::create_dir_all(runtime.join("bin")).unwrap();
    fs::write(runtime.join("bin").join(PYTHON_EXECUTABLE), "#!/bin/sh\n").unwrap();

    let err = resolve_engine(fixture.path()).expect_err("an interpreter with no libraries cannot serve");

    assert_eq!(err.kind, "runtime_missing");
}

#[test]
fn a_missing_resource_dir_reports_the_engine_as_missing_rather_than_panicking() {
    let err = resolve_engine(Path::new("/nonexistent/Grader.app/Contents/Resources"))
        .expect_err("a resource dir that does not exist cannot resolve");

    assert_eq!(err.kind, "engine_missing");
}

/// The defect in #23 was a candidate list that reached outside the bundle. Resolution
/// now takes a single directory, so there is nothing left to consult — this test guards
/// the source itself against the pattern coming back.
#[test]
fn resolution_consults_no_home_relative_or_developer_path() {
    let source = include_str!("engine.rs");

    for forbidden in [
        "/Users/",
        "var(\"HOME\")",
        "CARGO_MANIFEST_DIR",
        "code/grader",
        "Projects/grader",
        "Developer/grader",
    ] {
        assert!(
            !source.contains(forbidden),
            "engine.rs still reaches outside the app bundle via {forbidden:?}"
        );
    }
}

/// The engine defaults its database directory to `fixtures/`, relative to its working
/// directory — which is now inside the app bundle. Left unset, a signed app writes its
/// SQLite files into its own sealed Resources directory, where they break the signature
/// and are destroyed by the next update.
#[test]
fn the_engine_writes_its_data_outside_the_app_bundle() {
    let fixture = FakeResources::complete("data-dir");
    let layout = resolve_engine(fixture.path()).unwrap();
    let data_dir = PathBuf::from("/Users/someone/Library/Application Support/com.grader.app");

    let env = engine_environment(&layout, "test-token", &data_dir);
    let data = env
        .iter()
        .find(|(k, _)| k == "GRADER_DATA_DIR")
        .map(|(_, v)| v.clone())
        .expect("GRADER_DATA_DIR must be set");

    assert_eq!(data, data_dir.to_string_lossy());
    assert!(
        !Path::new(&data).starts_with(fixture.path()),
        "the engine would write into the app bundle"
    );
}

#[test]
fn the_engine_environment_pins_python_to_the_bundled_runtime() {
    let fixture = FakeResources::complete("env");
    let layout = resolve_engine(fixture.path()).unwrap();

    let env = engine_environment(&layout, "test-token", Path::new("/tmp/grader-data"));
    let lookup = |key: &str| {
        env.iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.clone())
            .unwrap_or_else(|| panic!("{key} was not set"))
    };

    assert_eq!(lookup("GRADER_ENGINE_TOKEN"), "test-token");
    let python_path = lookup("PYTHONPATH");
    assert!(python_path.contains(layout.site_packages.to_str().unwrap()));
    assert!(python_path.contains(layout.engine_dir.to_str().unwrap()));
    assert_eq!(
        lookup("PYTHONNOUSERSITE"),
        "1",
        "packages in the user's home must not shadow the bundled ones"
    );
    assert!(
        INHERITED_ENV_TO_DROP.contains(&"PYTHONHOME"),
        "an inherited PYTHONHOME would point the bundled interpreter at another install"
    );
}
