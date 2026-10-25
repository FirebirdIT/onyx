//! The native engine: the engine processes run on this computer, with no
//! Docker. Postgres, the code runner, the API server and the web server come
//! from one bundle (see `desktop/native-engine/build-bundle.sh`).

pub mod bundle;
pub mod env;
pub mod postgres;
pub mod process;

use crate::config::{get_data_dir, AppConfig};
use crate::engine::secrets::{load_or_create_secrets, secrets_path, EngineSecrets};
use crate::engine::{emit_progress, EngineStatus, ServiceStatus};
use crate::route::keystore;
use crate::secure_random::random_hex;
use bundle::Bundle;
use env::{free_port, NativeInput, Ports, LOOPBACK, NATIVE_WEB_PORT};
use process::{run_to_end, ServiceSpec, ServiceState, Supervised};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::{Duration, Instant};
use tauri::AppHandle;
use tokio::sync::Mutex;

const STOP_GRACE: Duration = Duration::from_secs(15);
const READY_TIMEOUT: Duration = Duration::from_mins(5);
const MIGRATION_TIMEOUT: Duration = Duration::from_mins(20);

/// Folders of the native engine, in the app data folder.
pub struct NativeLayout {
    root: PathBuf,
}

impl NativeLayout {
    pub fn bundles_dir(&self) -> PathBuf {
        self.root.join("bundles")
    }
    pub fn pg_data(&self) -> PathBuf {
        self.root.join("postgres-data")
    }
    pub fn runner_dir(&self) -> PathBuf {
        self.root.join("code-runner")
    }
    pub fn logs_dir(&self) -> PathBuf {
        self.root.join("logs")
    }
    fn log(&self, name: &str) -> PathBuf {
        self.logs_dir().join(format!("{name}.log"))
    }
}

pub fn native_layout() -> Result<NativeLayout, String> {
    get_data_dir()
        .map(|dir| NativeLayout {
            root: dir.join("engine-native"),
        })
        .ok_or_else(|| "Could not find the app data folder".to_string())
}

/// The running engine. Services are in start order.
struct Running {
    bundle: Bundle,
    ports: Ports,
    secrets: EngineSecrets,
    services: Vec<Supervised>,
}

fn running() -> &'static Mutex<Option<Running>> {
    static RUNNING: OnceLock<Mutex<Option<Running>>> = OnceLock::new();
    RUNNING.get_or_init(|| Mutex::new(None))
}

/// Process ids whose executable is in `bundles_dir`, from `lsof -Fpn -d txt`
/// output ("p<pid>" lines, each followed by "n<path>" lines). A process can
/// change its title (Next.js shows "next-server"), but not its executable.
fn bundle_pids(lsof_output: &str, bundles_dir: &str) -> Vec<u32> {
    let mut pids = Vec::new();
    let mut current: Option<u32> = None;
    for line in lsof_output.lines() {
        if let Some(pid) = line.strip_prefix('p') {
            current = pid.parse().ok();
        } else if let (Some(path), Some(pid)) = (line.strip_prefix('n'), current) {
            if path.starts_with(bundles_dir) && !pids.contains(&pid) {
                pids.push(pid);
            }
        }
    }
    pids
}

/// The process groups of `pids`, from `ps -axo pid=,pgid=` output.
fn groups_of(ps_output: &str, pids: &[u32]) -> Vec<u32> {
    let mut groups: Vec<u32> = ps_output
        .lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            let process: u32 = fields.next()?.parse().ok()?;
            let group: u32 = fields.next()?.parse().ok()?;
            (group > 1 && pids.contains(&process)).then_some(group)
        })
        .collect();
    groups.sort_unstable();
    groups.dedup();
    groups
}

async fn command_stdout(program: &str, args: &[&str]) -> String {
    tokio::process::Command::new(program)
        .args(args)
        .output()
        .await
        .map(|output| String::from_utf8_lossy(&output.stdout).to_string())
        .unwrap_or_default()
}

/// Stop engine processes that an earlier app run left behind, for example
/// after a crash. Only processes whose executable is in our bundle folder
/// are stopped.
async fn stop_leftovers(layout: &NativeLayout) {
    // lsof shows the real path, and the app folder can be under /tmp, which
    // is /private/tmp.
    let bundles_dir = std::fs::canonicalize(layout.bundles_dir())
        .unwrap_or_else(|_| layout.bundles_dir())
        .to_string_lossy()
        .to_string();
    let lsof = command_stdout("/usr/sbin/lsof", &["-nP", "-Fpn", "-d", "txt"]).await;
    let pids = bundle_pids(&lsof, &bundles_dir);
    if pids.is_empty() {
        return;
    }
    let ps = command_stdout("/bin/ps", &["-axo", "pid=,pgid="]).await;
    let groups = groups_of(&ps, &pids);
    for signal in ["-INT", "-KILL"] {
        for pgid in &groups {
            // A group that already ended makes `kill` fail, which is fine.
            let _ = tokio::process::Command::new("/bin/kill")
                .args([signal, &format!("-{pgid}")])
                .stderr(std::process::Stdio::null())
                .status()
                .await;
        }
        if signal == "-INT" {
            // Postgres takes INT as a fast shutdown and needs a moment.
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    }
}

async fn port_in_use(port: u16) -> bool {
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    tokio::time::timeout(Duration::from_secs(2), tokio::net::TcpStream::connect(addr))
        .await
        .is_ok_and(|result| result.is_ok())
}

async fn wait_http_ok(url: &str, timeout: Duration, what: &str) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {e}"))?;
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if client
            .get(url)
            .send()
            .await
            .is_ok_and(|r| r.status().is_success())
        {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    Err(format!("{what} did not start in time"))
}

fn python_spec(
    name: &'static str,
    bundle: &Bundle,
    args: &[&str],
    env: Vec<(String, String)>,
    log: PathBuf,
) -> ServiceSpec {
    ServiceSpec {
        name,
        program: bundle.python(),
        args: args.iter().map(|a| (*a).to_string()).collect(),
        cwd: bundle.backend_dir(),
        env,
        log,
        stop_signal: "TERM",
    }
}

/// Start Postgres, making the database on the first start.
async fn start_database(
    app: &AppHandle,
    bundle: &Bundle,
    layout: &NativeLayout,
    port: u16,
    secrets: &EngineSecrets,
) -> Result<Supervised, String> {
    let data = layout.pg_data();
    let first_start = !data.join("PG_VERSION").exists();
    if first_start {
        emit_progress(app, "prepare", "Making the database");
        postgres::init(
            bundle,
            &data,
            &secrets.postgres_password,
            &layout.log("initdb"),
        )
        .await?;
    }
    let server = Supervised::start(postgres::server_spec(
        bundle,
        &data,
        port,
        &layout.log("postgres"),
    ))?;
    if let Err(error) = postgres::wait_ready(bundle, port, Duration::from_secs(60)).await {
        server.stop(STOP_GRACE).await;
        return Err(error);
    }
    Ok(server)
}

async fn start_services(
    app: &AppHandle,
    bundle: &Bundle,
    layout: &NativeLayout,
    input: &NativeInput<'_>,
) -> Result<Vec<Supervised>, String> {
    let mut services = Vec::new();
    let result = async {
        emit_progress(app, "start", "Starting the code runner");
        services.push(Supervised::start(python_spec(
            "code_runner",
            bundle,
            &["-m", "onyx.deepx.code_runner"],
            env::runner_env(input, &layout.runner_dir()),
            layout.log("code_runner"),
        ))?);

        emit_progress(app, "start", "Updating the database");
        let backend_env = env::backend_env(input);
        run_to_end(
            &python_spec(
                "migrations",
                bundle,
                &["-m", "alembic", "upgrade", "head"],
                backend_env.clone(),
                layout.log("migrations"),
            ),
            MIGRATION_TIMEOUT,
        )
        .await?;

        emit_progress(app, "start", "Starting the API server");
        let api_port = input.ports.api.to_string();
        services.push(Supervised::start(python_spec(
            "api_server",
            bundle,
            &[
                "-m",
                "uvicorn",
                "onyx.main:app",
                "--host",
                LOOPBACK,
                "--port",
                &api_port,
            ],
            backend_env,
            layout.log("api_server"),
        ))?);

        emit_progress(app, "start", "Starting the web server");
        services.push(Supervised::start(ServiceSpec {
            name: "web_server",
            program: bundle.node(),
            args: vec![bundle.web_server().to_string_lossy().to_string()],
            cwd: bundle
                .web_server()
                .parent()
                .map_or_else(|| bundle.root.clone(), Path::to_path_buf),
            env: env::web_env(input),
            log: layout.log("web_server"),
            stop_signal: "TERM",
        })?);

        emit_progress(app, "start", "Waiting for the engine to be ready");
        wait_http_ok(
            &format!("http://{LOOPBACK}:{}/health", input.ports.api),
            READY_TIMEOUT,
            "The API server",
        )
        .await?;
        wait_http_ok(
            &format!("http://{LOOPBACK}:{}/api/health", input.ports.web),
            READY_TIMEOUT,
            "The web server",
        )
        .await
    }
    .await;
    match result {
        Ok(()) => Ok(services),
        Err(error) => {
            for service in services.into_iter().rev() {
                service.stop(STOP_GRACE).await;
            }
            Err(error)
        }
    }
}

/// Start the native engine and wait until it answers. A failure is also
/// written to `logs/engine.log`, because the window shows it only once.
pub async fn start(app: &AppHandle, config: &AppConfig) -> Result<(), String> {
    let result = start_engine(app, config).await;
    if let Err(error) = &result {
        if let Ok(layout) = native_layout() {
            let line = format!(
                "{} start failed: {error}\n",
                crate::debug_log::format_utc_timestamp()
            );
            let _ = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(layout.log("engine"))
                .and_then(|mut file| std::io::Write::write_all(&mut file, line.as_bytes()));
        }
    }
    result
}

// The lock is held for the whole start, so a second start waits for this one.
#[allow(clippy::significant_drop_tightening)]
async fn start_engine(app: &AppHandle, config: &AppConfig) -> Result<(), String> {
    let mut guard = running().lock().await;
    if guard.is_some() {
        emit_progress(app, "done", "The engine is ready");
        return Ok(());
    }
    let layout = native_layout()?;
    stop_leftovers(&layout).await;
    if port_in_use(NATIVE_WEB_PORT).await {
        return Err(format!(
            "Another program uses port {NATIVE_WEB_PORT}. Stop that program, and then start the engine again."
        ));
    }
    std::fs::create_dir_all(layout.logs_dir())
        .map_err(|e| format!("Failed to create the log folder: {e}"))?;
    emit_progress(app, "prepare", "Checking the engine version");
    let bundle = bundle::ensure_bundle(app, &config.hub_url, &layout.bundles_dir()).await?;
    let secrets = load_or_create_secrets(&secrets_path()?)?;
    let ports = Ports {
        postgres: free_port()?,
        api: free_port()?,
        runner: free_port()?,
        web: NATIVE_WEB_PORT,
    };
    let database = start_database(app, &bundle, &layout, ports.postgres, &secrets).await?;
    let route_key = keystore::load_key();
    if route_key.is_none() {
        emit_progress(
            app,
            "prepare",
            "No Deep X Route key is saved. Chat needs a key.",
        );
    }
    let runner_token = random_hex::<32>();
    let input = NativeInput {
        bundle: &bundle,
        ports,
        secrets: &secrets,
        route_api_key: route_key.as_deref(),
        hub_url: &config.hub_url,
        runner_token: &runner_token,
    };
    let mut services = match start_services(app, &bundle, &layout, &input).await {
        Ok(services) => services,
        Err(error) => {
            database.stop(STOP_GRACE).await;
            return Err(error);
        }
    };
    services.insert(0, database);
    *guard = Some(Running {
        bundle,
        ports,
        secrets,
        services,
    });
    emit_progress(app, "done", "The engine is ready");
    Ok(())
}

/// Stop every native engine process, the database last.
pub async fn stop(app: &AppHandle) -> Result<(), String> {
    let Some(engine) = running().lock().await.take() else {
        return Ok(());
    };
    for service in engine.services.into_iter().rev() {
        emit_progress(app, "stop", &format!("Stopping {}", service.name));
        service.stop(STOP_GRACE).await;
    }
    Ok(())
}

/// Stop the engine before the app exits, without an async runtime.
pub fn stop_blocking(grace: Duration) {
    let Ok(mut guard) = running().try_lock() else {
        return;
    };
    if let Some(engine) = guard.take() {
        for service in engine.services.iter().rev() {
            service.stop_blocking(grace);
        }
    }
}

pub async fn status() -> EngineStatus {
    let services: Vec<ServiceStatus> = running()
        .lock()
        .await
        .as_ref()
        .map(|engine| {
            engine
                .services
                .iter()
                .map(|service| ServiceStatus {
                    service: service.name.to_string(),
                    state: service.state().as_str().to_string(),
                    health: String::new(),
                })
                .collect()
        })
        .unwrap_or_default();
    let running = !services.is_empty()
        && services
            .iter()
            .all(|s| s.state == ServiceState::Running.as_str());
    EngineStatus { running, services }
}

/// Dump the running engine's database, for a backup.
// The lock is held during the dump, so the engine cannot stop in the middle.
#[allow(clippy::significant_drop_tightening)]
pub async fn dump_database(target: &Path) -> Result<(), String> {
    let guard = running().lock().await;
    let engine = guard.as_ref().ok_or("The engine is not running")?;
    let layout = native_layout()?;
    postgres::dump(
        &engine.bundle,
        engine.ports.postgres,
        &engine.secrets.postgres_password,
        target,
        &layout.log("pg_dump"),
    )
    .await
}

/// Replace the running engine's database with a dump, for a restore. The
/// caller starts the engine again afterwards.
pub async fn restore_database(app: &AppHandle, source: &Path) -> Result<(), String> {
    let engine = running()
        .lock()
        .await
        .take()
        .ok_or("The engine is not running")?;
    let layout = native_layout()?;
    let mut services = engine.services;
    let database = services.remove(0);
    for service in services.into_iter().rev() {
        emit_progress(app, "restore", &format!("Stopping {}", service.name));
        service.stop(STOP_GRACE).await;
    }
    // A backup can come from another computer, and a dump has no roles.
    let restored = match postgres::ensure_readonly_role(
        &engine.bundle,
        engine.ports.postgres,
        &engine.secrets.postgres_password,
        env::READONLY_ROLE,
        &env::readonly_password(&engine.secrets),
        &layout.log("pg_restore"),
    )
    .await
    {
        Ok(()) => {
            postgres::restore(
                &engine.bundle,
                engine.ports.postgres,
                &engine.secrets.postgres_password,
                source,
                true,
                &layout.log("pg_restore"),
            )
            .await
        }
        Err(error) => Err(error),
    };
    database.stop(STOP_GRACE).await;
    restored
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn leftovers_are_found_by_executable_not_by_title() {
        let lsof = "p101\nn/data/bundles/0.2.0/postgres/bin/postgres\nn/usr/lib/dyld\n\
                    p202\nn/data/bundles/0.2.0/node/bin/node\n\
                    p303\nn/usr/bin/python3\n";
        assert_eq!(bundle_pids(lsof, "/data/bundles"), vec![101, 202]);
        let ps = "  101   101\n  202   202\n  303   303\n    1     1\nbad\n";
        assert_eq!(groups_of(ps, &[101, 202, 1]), vec![101, 202]);
    }
}
