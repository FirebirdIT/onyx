//! Tauri commands for the setup and settings pages to control the engine.
// Tauri command handlers take IPC arguments and extractors by value.
#![allow(clippy::needless_pass_by_value)]

use super::docker::{check_docker, open_docker_desktop, DockerCheck};
use super::{EngineLock, EngineStatus};
use crate::config::{ConfigState, EngineMode};
use tauri::{AppHandle, Manager, State};

#[tauri::command]
pub async fn engine_check_docker() -> DockerCheck {
    check_docker().await
}

#[tauri::command]
pub fn engine_open_docker() -> Result<(), String> {
    open_docker_desktop()
}

#[tauri::command]
pub async fn engine_status() -> Result<EngineStatus, String> {
    super::status().await
}

#[tauri::command]
pub async fn engine_start(app: AppHandle, lock: State<'_, EngineLock>) -> Result<(), String> {
    let _guard = lock.0.lock().await;
    let config = app.state::<ConfigState>().config();
    super::start(&app, &config).await
}

#[tauri::command]
pub async fn engine_stop(app: AppHandle, lock: State<'_, EngineLock>) -> Result<(), String> {
    let _guard = lock.0.lock().await;
    super::stop(&app).await
}

/// Stop all services and start the services of the configured mode. Used
/// after a mode change, so that the services of the old mode do not run on.
#[tauri::command]
pub async fn engine_restart(app: AppHandle, lock: State<'_, EngineLock>) -> Result<(), String> {
    let _guard = lock.0.lock().await;
    // The first start has no files yet, so there is nothing to stop.
    if super::status().await.is_ok() {
        super::stop(&app).await?;
    }
    let config = app.state::<ConfigState>().config();
    super::start(&app, &config).await
}

#[tauri::command]
pub fn engine_set_mode(state: State<ConfigState>, mode: EngineMode) -> Result<(), String> {
    state
        .update_and_persist(|c| {
            c.engine_mode = mode;
            if c.local_engine {
                c.server_url = mode.local_server_url().to_string();
            }
        })
        .map(|_| ())
}

#[tauri::command]
pub fn engine_set_stop_on_quit(state: State<ConfigState>, enabled: bool) -> Result<(), String> {
    state
        .update_and_persist(|c| c.stop_engine_on_quit = enabled)
        .map(|_| ())
}

/// Use the local engine again, after a remote server was in use.
#[tauri::command]
pub fn use_local_engine(state: State<ConfigState>) -> Result<String, String> {
    let config = state.update_and_persist(|c| {
        c.local_engine = true;
        c.server_url = c.engine_mode.local_server_url().to_string();
    })?;
    state.set_config_initialized(true);
    Ok(config.server_url)
}

#[tauri::command]
pub fn complete_setup(state: State<ConfigState>) -> Result<(), String> {
    state.update_and_persist(|c| c.setup_complete = true)?;
    state.set_config_initialized(true);
    Ok(())
}
