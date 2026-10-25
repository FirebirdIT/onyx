//! The local Deep X engine. It runs as processes on this computer, with no
//! Docker (see `native/`). Only macOS has engine bundles for now.

pub mod commands;
#[cfg(unix)]
pub mod native;
pub mod secrets;

use crate::config::AppConfig;
use serde::Serialize;
use std::time::Duration;
use tauri::{AppHandle, Emitter};

pub const PROGRESS_EVENT: &str = "engine-progress";

const UNSUPPORTED: &str =
    "The Deep X engine does not run on this computer yet. Use a Deep X server: enter its address in Settings.";

/// Serializes engine work (start, stop, backup, restore), so that two of
/// them never change the engine at the same time.
#[derive(Default)]
pub struct EngineLock(pub tokio::sync::Mutex<()>);

#[derive(Clone, Serialize)]
pub struct Progress<'a> {
    pub step: &'a str,
    pub line: &'a str,
}

pub fn emit_progress(app: &AppHandle, step: &str, line: &str) {
    // A closed window only loses the progress text.
    let _ = app.emit(PROGRESS_EVENT, Progress { step, line });
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct ServiceStatus {
    pub service: String,
    pub state: String,
    pub health: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct EngineStatus {
    pub running: bool,
    pub services: Vec<ServiceStatus>,
}

/// True when this computer can run the engine.
pub const fn is_supported() -> bool {
    cfg!(target_os = "macos")
}

/// Start the engine and wait until it answers.
pub async fn start(app: &AppHandle, config: &AppConfig) -> Result<(), String> {
    #[cfg(unix)]
    if is_supported() {
        return native::start(app, config).await;
    }
    let _ = (app, config);
    Err(UNSUPPORTED.to_string())
}

/// Stop every engine process. The data stays.
pub async fn stop(app: &AppHandle) -> Result<(), String> {
    #[cfg(unix)]
    return native::stop(app).await;
    #[cfg(not(unix))]
    {
        let _ = app;
        Ok(())
    }
}

pub async fn status() -> Result<EngineStatus, String> {
    #[cfg(unix)]
    return Ok(native::status().await);
    #[cfg(not(unix))]
    Ok(EngineStatus {
        running: false,
        services: Vec::new(),
    })
}

/// Stop the engine before the app exits. Blocks for at most `timeout`.
pub fn stop_blocking(timeout: Duration) {
    #[cfg(unix)]
    native::stop_blocking(timeout);
    #[cfg(not(unix))]
    let _ = timeout;
}
