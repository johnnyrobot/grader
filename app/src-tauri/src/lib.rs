mod engine;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![get_engine_info, restart_engine])
        .setup(|app| {
            let resource_dir = app.path().resource_dir().unwrap_or_default();
            let log_path = app
                .path()
                .app_log_dir()
                .unwrap_or_else(|_| resource_dir.clone())
                .join("engine.log");
            // The engine's databases belong to the user, not to the bundle. Its own default
            // is `fixtures/` beside its working directory, which is inside Resources.
            let data_dir = app.path().app_data_dir().unwrap_or_else(|_| resource_dir.clone());
            engine::init(resource_dir, log_path, data_dir);

            match engine::start_engine() {
                Ok((token, port)) => {
                    println!("GRADER_ENGINE_TOKEN={token}");
                    println!("GRADER_ENGINE_PORT={port}");
                    app.manage(EngineToken(token.clone()));
                    app.manage(EnginePort(port));
                }
                Err(e) => {
                    // Also kept for `get_engine_info`, which is how the window learns of this.
                    // A line on stderr is invisible to anyone who launched Grader from Finder.
                    eprintln!("Failed to start engine: {e}");
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let _ = engine::stop_engine();
                let _ = window;
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[derive(Clone, serde::Serialize)]
struct EngineToken(String);

#[derive(Clone, serde::Serialize)]
struct EnginePort(u16);

/// The window's whole view of the engine, including why it is not running. An engine
/// that failed to start is a fact the UI has to be able to state; #23 was what happens
/// when that fact only ever reaches stderr.
fn engine_info_map() -> std::collections::HashMap<String, String> {
    let mut info = std::collections::HashMap::new();
    if let Some(token) = engine::get_token() {
        info.insert("token".into(), token);
    }
    info.insert("port".into(), engine::get_port().to_string());
    info.insert("running".into(), engine::is_running().to_string());
    if let Some(path) = engine::log_path() {
        info.insert("log_path".into(), path.display().to_string());
    }
    if let Some(error) = engine::last_error() {
        info.insert("error_kind".into(), error.kind.into());
        info.insert("error_message".into(), error.message);
        info.insert("error_detail".into(), error.detail);
    }
    info
}

#[tauri::command]
fn get_engine_info() -> std::collections::HashMap<String, String> {
    engine_info_map()
}

#[tauri::command]
fn restart_engine() -> Result<std::collections::HashMap<String, String>, String> {
    engine::stop_engine().map_err(|e| e.to_string())?;
    match engine::start_engine() {
        Ok(_) => Ok(engine_info_map()),
        // Flattened to a string for the rejection. The structured form stays available:
        // the failure is recorded on the engine, so the next `get_engine_info` carries
        // its kind, message, detail, and log path.
        Err(e) => Err(e.to_string()),
    }
}
