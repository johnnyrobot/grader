use std::process::{Child, Command};
use std::sync::Mutex;
use std::path::PathBuf;

struct EngineState {
    child: Option<Child>,
    token: Option<String>,
    port: u16,
}

impl EngineState {
    fn new() -> Self {
        Self {
            child: None,
            token: None,
            port: 8756,
        }
    }
}

static ENGINE: Mutex<Option<EngineState>> = Mutex::new(None);

fn find_engine_dir() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    let candidates = [
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../engine"),
        PathBuf::from("engine"),
        PathBuf::from(&home).join("code/grader/engine"),
        PathBuf::from(&home).join("Projects/grader/engine"),
        PathBuf::from(&home).join("Developer/grader/engine"),
        PathBuf::from("/Users/laccd/code/grader/engine"),
    ];
    for c in &candidates {
        if c.join("api.py").exists() {
            return Some(c.clone());
        }
    }
    None
}

fn find_uv() -> Option<PathBuf> {
    for name in &["uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv"] {
        let p = PathBuf::from(name);
        if p.exists() {
            return Some(p);
        }
    }
    which::which("uv").ok()
}

pub fn start_engine() -> Result<(String, u16), String> {
    let mut state = ENGINE.lock().map_err(|e| e.to_string())?;
    if state.is_none() {
        *state = Some(EngineState::new());
    }
    let s = state.as_mut().unwrap();

    if s.child.is_some() {
        return Ok((s.token.clone().unwrap_or_default(), s.port));
    }

    let engine_dir = find_engine_dir().ok_or("engine directory not found")?;
    let project_root = engine_dir.parent().unwrap_or(&PathBuf::from(".")).to_path_buf();
    let uv = find_uv().ok_or("uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh")?;

    let token = uuid::Uuid::new_v4().to_string();
    let port = 8756u16;

    let script = project_root.join("scripts/start_engine.sh");
    let mut cmd = Command::new("sh");
    cmd.arg("-c");
    cmd.arg(format!("exec {} {}", script.display(), token));
    cmd.current_dir(&project_root);

    let child = cmd.spawn().map_err(|e| format!("failed to start engine: {e}"))?;

    s.child = Some(child);
    s.token = Some(token.clone());
    s.port = port;

    Ok((token, port))
}

pub fn stop_engine() -> Result<(), String> {
    let mut state = ENGINE.lock().map_err(|e| e.to_string())?;
    if let Some(s) = state.as_mut() {
        if let Some(child) = s.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        s.child = None;
        s.token = None;
    }
    Ok(())
}

pub fn get_token() -> Option<String> {
    let state = ENGINE.lock().ok()?;
    state.as_ref().and_then(|s| s.token.clone())
}

pub fn get_port() -> u16 {
    let state = ENGINE.lock().ok();
    state.as_ref().and_then(|s| s.as_ref()).map(|s| s.port).unwrap_or(8756)
}

pub fn is_running() -> bool {
    let state = ENGINE.lock().ok();
    state.as_ref().and_then(|s| s.as_ref()).map(|s| s.child.is_some()).unwrap_or(false)
}