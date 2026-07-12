mod engine;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![get_engine_info, restart_engine])
        .setup(|app| {
            match engine::start_engine() {
                Ok((token, port)) => {
                    println!("GRADER_ENGINE_TOKEN={token}");
                    println!("GRADER_ENGINE_PORT={port}");
                    app.manage(EngineToken(token.clone()));
                    app.manage(EnginePort(port));
                }
                Err(e) => {
                    eprintln!("Failed to start engine: {e}");
                    eprintln!("The app will run in limited mode. Start the engine manually:");
                    eprintln!("  uv run uvicorn engine.api:app --host 127.0.0.1 --port 8756");
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

#[tauri::command]
fn get_engine_info() -> std::collections::HashMap<String, String> {
    let mut info = std::collections::HashMap::new();
    if let Some(token) = engine::get_token() {
        info.insert("token".into(), token);
    }
    info.insert("port".into(), engine::get_port().to_string());
    info.insert("running".into(), engine::is_running().to_string());
    info
}

#[tauri::command]
fn restart_engine() -> Result<String, String> {
    engine::stop_engine()?;
    let (_token, port) = engine::start_engine()?;
    Ok(format!("Engine restarted on port {port}"))
}