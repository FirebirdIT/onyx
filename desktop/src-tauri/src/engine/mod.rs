//! The local Deep X engine: Docker Compose services on this computer.

pub mod commands;
#[cfg(test)]
mod compose_check;
pub mod docker;
pub mod env;
pub mod files;
pub mod hub;
pub mod images;
#[cfg(unix)]
pub mod native;

use crate::config::{get_data_dir, AppConfig, EngineMode};
use crate::route::keystore;
use docker::{find_docker, Compose};
use env::{load_or_create_secrets, write_env, EnvInput, ENGINE_HOST_PORT};
use files::{write_compose_files, EngineLayout};
use serde::{Deserialize, Serialize};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

pub const PROGRESS_EVENT: &str = "engine-progress";

/// Serializes engine work (start, stop, backup, restore), so that two of
/// them never change the containers at the same time.
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

pub fn engine_layout() -> Result<EngineLayout, String> {
    get_data_dir()
        .map(|dir| EngineLayout::new(dir.join("engine")))
        .ok_or_else(|| "Could not find the app data folder".to_string())
}

fn docker_path() -> Result<std::path::PathBuf, String> {
    find_docker().ok_or_else(|| "Docker is not installed".to_string())
}

/// Compose for `mode`, without writing any files.
pub fn compose_for(mode: EngineMode) -> Result<Compose, String> {
    let layout = engine_layout()?;
    Ok(Compose::new(
        docker_path()?,
        layout.compose_dir(),
        layout.compose_files(mode),
    ))
}

/// Write the compose files, the secrets and the `.env` file, and return the
/// compose command for the configured mode.
pub async fn prepare(
    app: &AppHandle,
    config: &AppConfig,
) -> Result<(Compose, hub::EngineImages), String> {
    let layout = engine_layout()?;
    write_compose_files(&layout)?;
    let secrets = load_or_create_secrets(&layout.secrets_path())?;
    let (images, note) =
        hub::resolve_engine_images(&config.hub_url, &layout.root().join("engine-info.json")).await;
    emit_progress(app, "prepare", &note);
    let route_key = keystore::load_key();
    if route_key.is_none() {
        emit_progress(
            app,
            "prepare",
            "No Deep X Route key is saved. Chat needs a key.",
        );
    }
    write_env(
        &layout.env_path(),
        &EnvInput {
            images: &images,
            secrets: &secrets,
            route_api_key: route_key.as_deref(),
            hub_url: &config.hub_url,
        },
    )?;
    Ok((compose_for(config.engine_mode)?, images))
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ServiceStatus {
    #[serde(rename = "Service")]
    pub service: String,
    #[serde(rename = "State")]
    pub state: String,
    #[serde(rename = "Health", default)]
    pub health: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct EngineStatus {
    pub running: bool,
    pub services: Vec<ServiceStatus>,
}

/// Read `docker compose ps --format json`. New Compose versions write one
/// JSON object on each line; old versions write one JSON array.
pub fn parse_ps_output(text: &str) -> Vec<ServiceStatus> {
    let trimmed = text.trim();
    if trimmed.starts_with('[') {
        return serde_json::from_str(trimmed).unwrap_or_default();
    }
    trimmed
        .lines()
        .filter_map(|line| serde_json::from_str(line.trim()).ok())
        .collect()
}

fn is_running(services: &[ServiceStatus]) -> bool {
    ["api_server", "web_server", "nginx"].iter().all(|name| {
        services
            .iter()
            .any(|s| s.service == *name && s.state == "running")
    })
}

pub async fn status() -> Result<EngineStatus, String> {
    #[cfg(unix)]
    {
        let native = native::status().await;
        if !native.services.is_empty() {
            return Ok(native);
        }
    }
    docker_status().await
}

pub async fn docker_status() -> Result<EngineStatus, String> {
    let compose = compose_for(EngineMode::Full)?;
    let text = compose
        .run_capture(["--profile", "*", "ps", "--format", "json"])
        .await?;
    let services = parse_ps_output(&text);
    Ok(EngineStatus {
        running: is_running(&services),
        services,
    })
}

/// Something that is not our engine answers on the engine port.
async fn port_taken_by_other() -> bool {
    // Our own nginx holds the port while it runs.
    let ours = docker_status().await.is_ok_and(|s| {
        s.services
            .iter()
            .any(|service| service.service == "nginx" && service.state == "running")
    });
    if ours {
        return false;
    }
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], ENGINE_HOST_PORT));
    tokio::time::timeout(Duration::from_secs(2), tokio::net::TcpStream::connect(addr))
        .await
        .is_ok_and(|result| result.is_ok())
}

/// Start the engine and wait until all services are healthy.
pub async fn start(app: &AppHandle, config: &AppConfig) -> Result<(), String> {
    if config.engine_mode == EngineMode::Native {
        return start_native(app, config).await;
    }
    emit_progress(app, "check", "Checking Docker");
    let check = docker::check_docker().await;
    if !(check.running && check.compose) {
        return Err(check.message);
    }
    if port_taken_by_other().await {
        return Err(format!(
            "Another program uses port {ENGINE_HOST_PORT}. Stop that program, and then start the engine again."
        ));
    }
    emit_progress(app, "prepare", "Writing engine files");
    let (compose, images) = prepare(app, config).await?;
    emit_progress(
        app,
        "start",
        "Starting the engine. The first start downloads the images and can take many minutes.",
    );
    let handle = app.clone();
    compose
        .run_streamed(
            [
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "1500",
                "--remove-orphans",
            ],
            move |line| emit_progress(&handle, "start", line),
        )
        .await?;
    remove_old_images(app, &images).await;
    emit_progress(app, "done", "The engine is ready");
    Ok(())
}

#[cfg(unix)]
async fn start_native(app: &AppHandle, config: &AppConfig) -> Result<(), String> {
    native::start(app, config).await
}

#[cfg(not(unix))]
async fn start_native(_app: &AppHandle, _config: &AppConfig) -> Result<(), String> {
    Err("The engine without Docker does not support this computer yet. Select Lite.".to_string())
}

/// Remove engine images of earlier versions. A failure only shows a note,
/// because the engine runs without this step.
async fn remove_old_images(app: &AppHandle, current: &hub::EngineImages) {
    match images::remove_old_engine_images(current).await {
        Ok(0) => {}
        Ok(count) => emit_progress(
            app,
            "cleanup",
            &format!("Removed {count} old engine image(s) to free disk space"),
        ),
        Err(error) => emit_progress(
            app,
            "cleanup",
            &format!("Could not remove old engine images: {error}"),
        ),
    }
}

/// Stop every engine service, in all modes. The data stays.
pub async fn stop(app: &AppHandle) -> Result<(), String> {
    #[cfg(unix)]
    native::stop(app).await?;
    // Nothing runs in Docker when Docker is missing or not running.
    if find_docker().is_none() || !docker::check_docker().await.running {
        return Ok(());
    }
    let compose = compose_for(EngineMode::Full)?;
    let handle = app.clone();
    compose
        .run_streamed(["--profile", "*", "stop"], move |line| {
            emit_progress(&handle, "stop", line);
        })
        .await
}

/// Stop the engine before the app exits. Blocks for at most `timeout`.
pub fn stop_blocking(timeout: Duration) {
    #[cfg(unix)]
    native::stop_blocking(timeout.min(Duration::from_secs(20)));
    let Ok(compose) = compose_for(EngineMode::Full) else {
        return;
    };
    let Some(docker) = find_docker() else { return };
    let mut command = std::process::Command::new(docker);
    command
        .args(compose.args(["--profile", "*", "stop", "--timeout", "20"]))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let Ok(mut child) = command.spawn() else {
        return;
    };
    let deadline = std::time::Instant::now() + timeout;
    while std::time::Instant::now() < deadline {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    let _ = child.kill();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ps_lines_and_arrays() {
        let lines = "{\"Service\":\"api_server\",\"State\":\"running\",\"Health\":\"healthy\"}\n{\"Service\":\"nginx\",\"State\":\"running\",\"Health\":\"\"}\n";
        let services = parse_ps_output(lines);
        assert_eq!(services.len(), 2);
        assert_eq!(services[0].health, "healthy");

        let array = "[{\"Service\":\"web_server\",\"State\":\"exited\"}]";
        let services = parse_ps_output(array);
        assert_eq!(services[0].state, "exited");
        assert_eq!(parse_ps_output(""), Vec::new());
    }

    #[test]
    fn running_needs_api_web_and_nginx() {
        let make = |service: &str, state: &str| ServiceStatus {
            service: service.into(),
            state: state.into(),
            health: String::new(),
        };
        let mut services = vec![
            make("api_server", "running"),
            make("web_server", "running"),
            make("nginx", "running"),
        ];
        assert!(is_running(&services));
        services[2] = make("nginx", "exited");
        assert!(!is_running(&services));
    }
}
