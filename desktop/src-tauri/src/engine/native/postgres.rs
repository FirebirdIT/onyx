//! The native engine database: create, check, dump and restore.

use super::bundle::Bundle;
use super::env::LOOPBACK;
use super::process::{run_to_end, ServiceSpec};
use crate::engine::env::POSTGRES_USER;
use std::path::Path;
use std::time::{Duration, Instant};

const DB_NAME: &str = "postgres";
const STEP_TIMEOUT: Duration = Duration::from_mins(30);

fn spec(
    bundle: &Bundle,
    name: &'static str,
    program: &str,
    args: Vec<String>,
    password: &str,
    log: &Path,
) -> ServiceSpec {
    ServiceSpec {
        name,
        program: bundle.postgres_bin(program),
        args,
        cwd: bundle.root.clone(),
        env: vec![("PGPASSWORD".to_string(), password.to_string())],
        log: log.to_path_buf(),
        stop_signal: "INT",
    }
}

fn connection_args(port: u16) -> Vec<String> {
    vec![
        "-h".into(),
        LOOPBACK.into(),
        "-p".into(),
        port.to_string(),
        "-U".into(),
        POSTGRES_USER.into(),
    ]
}

/// Make a new database folder that only accepts password logins.
pub async fn init(bundle: &Bundle, data: &Path, password: &str, log: &Path) -> Result<(), String> {
    let parent = data
        .parent()
        .ok_or("The database folder has no parent folder")?;
    let pwfile = parent.join(".pg-password");
    crate::fsutil::write_private(&pwfile, password.as_bytes())?;
    let args = vec![
        "-D".into(),
        data.to_string_lossy().to_string(),
        "-U".into(),
        POSTGRES_USER.into(),
        format!("--pwfile={}", pwfile.to_string_lossy()),
        "--auth=scram-sha-256".into(),
        "-E".into(),
        "UTF8".into(),
        "--locale=C".into(),
    ];
    let result = run_to_end(
        &spec(bundle, "initdb", "initdb", args, password, log),
        STEP_TIMEOUT,
    )
    .await;
    let _ = std::fs::remove_file(&pwfile);
    if result.is_err() {
        let _ = std::fs::remove_dir_all(data);
    }
    result
}

/// The `postgres` server process. No Unix socket: the app folder path is too
/// long for one, and TCP on the loopback address is enough.
pub fn server_spec(bundle: &Bundle, data: &Path, port: u16, log: &Path) -> ServiceSpec {
    ServiceSpec {
        name: "postgres",
        program: bundle.postgres_bin("postgres"),
        args: vec![
            "-D".into(),
            data.to_string_lossy().to_string(),
            "-p".into(),
            port.to_string(),
            "-h".into(),
            LOOPBACK.into(),
            "-c".into(),
            "unix_socket_directories=".into(),
            "-c".into(),
            "max_connections=250".into(),
        ],
        cwd: bundle.root.clone(),
        env: Vec::new(),
        log: log.to_path_buf(),
        stop_signal: "INT",
    }
}

/// Wait until the server accepts connections.
pub async fn wait_ready(bundle: &Bundle, port: u16, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        let ready = tokio::process::Command::new(bundle.postgres_bin("pg_isready"))
            .args(["-q", "-h", LOOPBACK, "-p", &port.to_string()])
            .status()
            .await
            .is_ok_and(|status| status.success());
        if ready {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
    }
    Err("The database did not start in time".to_string())
}

pub async fn dump(
    bundle: &Bundle,
    port: u16,
    password: &str,
    target: &Path,
    log: &Path,
) -> Result<(), String> {
    let mut args = connection_args(port);
    args.extend([
        "-Fc".into(),
        "-d".into(),
        DB_NAME.into(),
        "-f".into(),
        target.to_string_lossy().to_string(),
    ]);
    run_to_end(
        &spec(bundle, "pg_dump", "pg_dump", args, password, log),
        STEP_TIMEOUT,
    )
    .await
}

/// Make the read-only role if it does not exist, as the backend migration
/// does. A dump does not hold roles, so a restore needs this first.
pub async fn ensure_readonly_role(
    bundle: &Bundle,
    port: u16,
    password: &str,
    role: &str,
    role_password: &str,
    log: &Path,
) -> Result<(), String> {
    let sql = format!(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{role}') \
         THEN EXECUTE format('CREATE USER %I WITH PASSWORD %L', '{role}', '{role_password}'); \
         END IF; END $$;"
    );
    let mut args = connection_args(port);
    args.extend([
        "-v".into(),
        "ON_ERROR_STOP=1".into(),
        "-d".into(),
        DB_NAME.into(),
        "-c".into(),
        sql,
    ]);
    run_to_end(
        &spec(bundle, "psql", "psql", args, password, log),
        STEP_TIMEOUT,
    )
    .await
}

/// Restore a `pg_dump -Fc` file. `clean` drops the objects that exist first.
pub async fn restore(
    bundle: &Bundle,
    port: u16,
    password: &str,
    source: &Path,
    clean: bool,
    log: &Path,
) -> Result<(), String> {
    let mut args = connection_args(port);
    if clean {
        args.extend(["--clean".into(), "--if-exists".into()]);
    }
    args.extend([
        "--no-owner".into(),
        "--single-transaction".into(),
        "--exit-on-error".into(),
        "-d".into(),
        DB_NAME.into(),
        source.to_string_lossy().to_string(),
    ]);
    run_to_end(
        &spec(bundle, "pg_restore", "pg_restore", args, password, log),
        STEP_TIMEOUT,
    )
    .await
}
