//! Starting and supervising the local grading engine.
//!
//! The engine is a Python process that ships inside the app bundle. Everything it
//! needs — interpreter, libraries, and the `engine/` source — is resolved from the
//! bundle's resource directory, which is the only location this module consults.
//! There is deliberately no search: a build either carries its engine or it does not,
//! and the difference is reported rather than guessed around.

use std::io::{Read, Seek, SeekFrom};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

/// Where `scripts/build_engine_bundle.py` places the engine source, relative to the
/// bundle's resource directory.
pub const ENGINE_SUBDIR: &str = "engine";
/// Where the same script places the interpreter and its libraries.
pub const RUNTIME_SUBDIR: &str = "runtime";
/// The interpreter's filename inside `runtime/bin`.
pub const PYTHON_EXECUTABLE: &str = "python3";

/// Environment the launching process may carry that would redirect the bundled
/// interpreter at another Python installation.
pub const INHERITED_ENV_TO_DROP: &[&str] = &["PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE"];

/// How much of the engine log to quote back when the engine dies on startup.
const LOG_TAIL_BYTES: u64 = 4096;

/// A failure the user is entitled to see. `kind` is for the frontend to branch on,
/// `message` is the one-line statement of what went wrong, and `detail` is what to
/// do about it.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct EngineError {
    pub kind: &'static str,
    pub message: String,
    pub detail: String,
}

impl EngineError {
    fn new(kind: &'static str, message: impl Into<String>, detail: impl Into<String>) -> Self {
        Self { kind, message: message.into(), detail: detail.into() }
    }
}

impl std::fmt::Display for EngineError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} {}", self.message, self.detail)
    }
}

/// The three paths that make a runnable engine, all beneath one resource directory.
#[derive(Debug, Clone, PartialEq)]
pub struct EngineLayout {
    pub engine_dir: PathBuf,
    pub python: PathBuf,
    pub site_packages: PathBuf,
}

/// Resolve the bundled engine, or say precisely which half of it is absent.
pub fn resolve_engine(resource_dir: &Path) -> Result<EngineLayout, EngineError> {
    let engine_dir = resource_dir.join(ENGINE_SUBDIR);
    let runtime_dir = resource_dir.join(RUNTIME_SUBDIR);
    let python = runtime_dir.join("bin").join(PYTHON_EXECUTABLE);
    let site_packages = runtime_dir.join("site-packages");

    let entry_point = engine_dir.join("api.py");
    if !entry_point.is_file() {
        return Err(EngineError::new(
            "engine_missing",
            "This copy of Grader is missing its grading engine.",
            format!(
                "Expected {} inside the app bundle. Reinstall Grader from the signed release. \
                 For a development build, run scripts/build_engine_bundle.py and rebuild.",
                entry_point.display()
            ),
        ));
    }

    if !python.is_file() || !site_packages.is_dir() {
        return Err(EngineError::new(
            "runtime_missing",
            "This copy of Grader is missing its Python runtime.",
            format!(
                "Expected an interpreter at {} and libraries in {}. Reinstall Grader from the \
                 signed release. For a development build, run scripts/build_engine_bundle.py \
                 and rebuild.",
                python.display(),
                site_packages.display()
            ),
        ));
    }

    Ok(EngineLayout { engine_dir, python, site_packages })
}

/// The environment overrides handed to the engine process. Everything else is
/// inherited so the engine keeps the user's `HOME` and its caches with it.
///
/// `data_dir` must be outside the app bundle: the engine defaults its databases to
/// `fixtures/` relative to its working directory, and that working directory is now
/// the bundle's own resource directory.
pub fn engine_environment(
    layout: &EngineLayout,
    token: &str,
    data_dir: &Path,
) -> Vec<(String, String)> {
    let bundle_root = layout.engine_dir.parent().unwrap_or(&layout.engine_dir);
    let python_path = std::env::join_paths([
        layout.site_packages.as_path(),
        bundle_root,
        layout.engine_dir.as_path(),
    ])
    .map(|joined| joined.to_string_lossy().into_owned())
    .unwrap_or_default();

    vec![
        ("GRADER_ENGINE_TOKEN".into(), token.into()),
        ("GRADER_APP_VERSION".into(), env!("CARGO_PKG_VERSION").into()),
        ("GRADER_DATA_DIR".into(), data_dir.to_string_lossy().into_owned()),
        ("PYTHONPATH".into(), python_path),
        ("PYTHONNOUSERSITE".into(), "1".into()),
        ("PYTHONDONTWRITEBYTECODE".into(), "1".into()),
        ("PYTHONUNBUFFERED".into(), "1".into()),
    ]
}

/// Paths handed in once at startup. Kept apart from [`EngineState`] so a restart can
/// reuse them without an `AppHandle`.
#[derive(Debug, Clone)]
struct EnginePaths {
    resource_dir: PathBuf,
    log_path: PathBuf,
    data_dir: PathBuf,
}

struct EngineState {
    child: Option<Child>,
    token: Option<String>,
    port: u16,
    last_error: Option<EngineError>,
}

impl EngineState {
    fn new() -> Self {
        Self { child: None, token: None, port: 8756, last_error: None }
    }
}

static PATHS: Mutex<Option<EnginePaths>> = Mutex::new(None);
static ENGINE: Mutex<Option<EngineState>> = Mutex::new(None);

/// Record where the bundle, the engine log, and the engine's databases live.
/// Called once, from Tauri setup.
pub fn init(resource_dir: PathBuf, log_path: PathBuf, data_dir: PathBuf) {
    if let Ok(mut paths) = PATHS.lock() {
        *paths = Some(EnginePaths { resource_dir, log_path, data_dir });
    }
}

fn paths() -> Result<EnginePaths, EngineError> {
    PATHS
        .lock()
        .ok()
        .and_then(|p| p.clone())
        .ok_or_else(|| {
            EngineError::new(
                "not_initialised",
                "Grader could not locate its own application bundle.",
                "The app failed to resolve its resource directory at startup. Reinstall Grader \
                 from the signed release.",
            )
        })
}

/// Quote the end of the engine log so a startup crash arrives with its own explanation.
fn log_tail(log_path: &Path) -> String {
    let Ok(mut file) = std::fs::File::open(log_path) else {
        return String::new();
    };
    let Ok(len) = file.metadata().map(|m| m.len()) else {
        return String::new();
    };
    let start = len.saturating_sub(LOG_TAIL_BYTES);
    if file.seek(SeekFrom::Start(start)).is_err() {
        return String::new();
    }
    let mut tail = String::new();
    let _ = file.read_to_string(&mut tail);
    tail.trim().to_string()
}

/// Notice a dead engine and turn its exit into something the UI can show.
fn reap_exited_child(state: &mut EngineState, log_path: &Path) {
    let Some(child) = state.child.as_mut() else {
        return;
    };
    let exit = match child.try_wait() {
        Ok(Some(status)) => Some(status.to_string()),
        Err(e) => Some(format!("could not be waited on: {e}")),
        Ok(None) => None,
    };
    let Some(exit) = exit else { return };

    state.child = None;
    state.token = None;
    let tail = log_tail(log_path);
    let detail = if tail.is_empty() {
        format!("The engine process {exit}. Its log is at {}.", log_path.display())
    } else {
        format!("The engine process {exit}. The end of {}:\n{tail}", log_path.display())
    };
    state.last_error = Some(EngineError::new("engine_exited", "The local grading engine stopped.", detail));
}

fn find_free_port() -> Result<u16, EngineError> {
    let bind_failure = |e: std::io::Error| {
        EngineError::new(
            "port_unavailable",
            "Grader could not reserve a local port for its engine.",
            format!("Binding to 127.0.0.1 failed: {e}. Check whether a firewall or security tool blocks loopback connections."),
        )
    };
    let listener = TcpListener::bind("127.0.0.1:0").map_err(bind_failure)?;
    let port = listener.local_addr().map_err(bind_failure)?.port();
    drop(listener);
    Ok(port)
}

fn open_log(log_path: &Path) -> Result<std::fs::File, EngineError> {
    if let Some(parent) = log_path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| {
            EngineError::new(
                "log_unavailable",
                "Grader could not open its engine log.",
                format!("Creating {} failed: {e}", parent.display()),
            )
        })?;
    }
    std::fs::File::create(log_path).map_err(|e| {
        EngineError::new(
            "log_unavailable",
            "Grader could not open its engine log.",
            format!("Creating {} failed: {e}", log_path.display()),
        )
    })
}

pub fn start_engine() -> Result<(String, u16), EngineError> {
    let paths = paths()?;

    let mut state = ENGINE.lock().map_err(|e| {
        EngineError::new("engine_lock_poisoned", "Grader lost track of its engine.", e.to_string())
    })?;
    if state.is_none() {
        *state = Some(EngineState::new());
    }
    let s = state.as_mut().unwrap();
    reap_exited_child(s, &paths.log_path);

    if s.child.is_some() {
        return Ok((s.token.clone().unwrap_or_default(), s.port));
    }

    let layout = resolve_engine(&paths.resource_dir).inspect_err(|e| {
        s.last_error = Some(e.clone());
    })?;

    let token = uuid::Uuid::new_v4().to_string();
    let port = find_free_port().inspect_err(|e| {
        s.last_error = Some(e.clone());
    })?;

    let start = || -> Result<Child, EngineError> {
        std::fs::create_dir_all(&paths.data_dir).map_err(|e| {
            EngineError::new(
                "data_dir_unavailable",
                "Grader could not open the folder it keeps your grading in.",
                format!("Creating {} failed: {e}", paths.data_dir.display()),
            )
        })?;

        let log = open_log(&paths.log_path)?;
        let log_err = log.try_clone().map_err(|e| {
            EngineError::new("log_unavailable", "Grader could not open its engine log.", e.to_string())
        })?;

        let mut cmd = Command::new(&layout.python);
        cmd.arg("-m")
            .arg("uvicorn")
            .arg("engine.api:app")
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(port.to_string());
        cmd.current_dir(layout.engine_dir.parent().unwrap_or(&layout.engine_dir));
        for key in INHERITED_ENV_TO_DROP {
            cmd.env_remove(key);
        }
        cmd.envs(engine_environment(&layout, &token, &paths.data_dir));
        cmd.stdin(Stdio::null()).stdout(Stdio::from(log)).stderr(Stdio::from(log_err));

        cmd.spawn().map_err(|e| {
            EngineError::new(
                "spawn_failed",
                "Grader could not start its grading engine.",
                format!("Running {} failed: {e}", layout.python.display()),
            )
        })
    };

    let child = start().inspect_err(|e| {
        s.last_error = Some(e.clone());
    })?;

    s.child = Some(child);
    s.token = Some(token.clone());
    s.port = port;
    s.last_error = None;

    Ok((token, port))
}

pub fn stop_engine() -> Result<(), EngineError> {
    let mut state = ENGINE.lock().map_err(|e| {
        EngineError::new("engine_lock_poisoned", "Grader lost track of its engine.", e.to_string())
    })?;
    if let Some(s) = state.as_mut() {
        if let Some(child) = s.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        s.child = None;
        s.token = None;
        s.last_error = None;
    }
    Ok(())
}

/// Re-read engine state, noticing an engine that has died since the last look.
fn with_current_state<T>(f: impl FnOnce(&mut EngineState) -> T) -> Option<T> {
    let log_path = paths().ok().map(|p| p.log_path);
    let mut state = ENGINE.lock().ok()?;
    let s = state.as_mut()?;
    if let Some(log_path) = log_path.as_deref() {
        reap_exited_child(s, log_path);
    }
    Some(f(s))
}

pub fn get_token() -> Option<String> {
    with_current_state(|s| s.token.clone()).flatten()
}

pub fn get_port() -> u16 {
    with_current_state(|s| s.port).unwrap_or(8756)
}

pub fn is_running() -> bool {
    with_current_state(|s| s.child.is_some()).unwrap_or(false)
}

/// The most recent reason the engine is not running, if it is not running.
pub fn last_error() -> Option<EngineError> {
    with_current_state(|s| if s.child.is_some() { None } else { s.last_error.clone() }).flatten()
}

/// Where the engine's own output is written, for the UI to point at.
pub fn log_path() -> Option<PathBuf> {
    paths().ok().map(|p| p.log_path)
}

#[cfg(test)]
#[path = "engine_layout_tests.rs"]
mod tests;
