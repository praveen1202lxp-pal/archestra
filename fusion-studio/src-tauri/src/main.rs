// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{AppHandle, Manager, State};

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct BridgeMessage {
    pub id: Option<String>,
    pub command: Option<String>,
    pub event: Option<String>,
    pub params: Option<serde_json::Value>,
    pub data: Option<serde_json::Value>,
    pub success: Option<bool>,
    pub error: Option<serde_json::Value>,
}

pub struct BridgeProcessState {
    pub child: Mutex<Option<Child>>,
}

#[tauri::command]
fn send_bridge_command(
    state: State<BridgeProcessState>,
    request: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mut child_guard = state.child.lock().unwrap();

    if child_guard.is_none() {
        // Spawn Python bridge process if not already running
        let py_cmd = if cfg!(windows) { "python" } else { "python3" };
        let mut child = Command::new(py_cmd)
            .args(["-m", "fusion_agent.ui_bridge.server"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| format!("Failed to spawn Python UI bridge: {}", e))?;

        *child_guard = Some(child);
    }

    let child = child_guard.as_mut().unwrap();
    let stdin = child.stdin.as_mut().ok_or("Failed to open child stdin")?;
    let stdout = child.stdout.as_mut().ok_or("Failed to open child stdout")?;

    let req_str = serde_json::to_string(&request).map_err(|e| e.to_string())?;
    writeln!(stdin, "{}", req_str).map_err(|e| e.to_string())?;
    stdin.flush().map_err(|e| e.to_string())?;

    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    reader.read_line(&mut line).map_err(|e| e.to_string())?;

    let resp: serde_json::Value =
        serde_json::from_str(&line).map_err(|e| format!("Invalid JSON from bridge: {}", e))?;

    Ok(resp)
}

fn main() {
    tauri::Builder::default()
        .manage(BridgeProcessState {
            child: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![send_bridge_command])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
