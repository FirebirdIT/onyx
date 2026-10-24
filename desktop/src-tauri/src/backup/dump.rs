//! `pg_dump` and `pg_restore` in the engine `relational_db` container.
//! The commands use the local socket in the container, so no password is
//! necessary.

use crate::engine::docker::Compose;
use crate::engine::env::POSTGRES_USER;
use std::path::Path;
use std::process::Stdio;
use tokio::io::AsyncReadExt;

const DB_SERVICE: &str = "relational_db";
const DB_NAME: &str = "postgres";

pub fn dump_args() -> Vec<&'static str> {
    vec![
        "exec",
        "-T",
        DB_SERVICE,
        "pg_dump",
        "-Fc",
        "-U",
        POSTGRES_USER,
        "-d",
        DB_NAME,
    ]
}

pub fn restore_args() -> Vec<&'static str> {
    vec![
        "exec",
        "-T",
        DB_SERVICE,
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--single-transaction",
        "-U",
        POSTGRES_USER,
        "-d",
        DB_NAME,
    ]
}

fn tail(text: &str) -> String {
    let lines: Vec<&str> = text.lines().collect();
    lines[lines.len().saturating_sub(5)..].join("\n")
}

async fn run_with_files(
    compose: &Compose,
    args: Vec<&str>,
    stdin: Stdio,
    stdout: Stdio,
) -> Result<(), String> {
    let mut child = compose
        .command(args)
        .stdin(stdin)
        .stdout(stdout)
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to run docker compose: {e}"))?;
    let mut stderr = String::new();
    if let Some(mut pipe) = child.stderr.take() {
        // Missing stderr text only makes the error message shorter.
        let _ = pipe.read_to_string(&mut stderr).await;
    }
    let status = child
        .wait()
        .await
        .map_err(|e| format!("docker compose did not finish: {e}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{status}\n{}", tail(&stderr)))
    }
}

pub async fn dump_to_file(compose: &Compose, path: &Path) -> Result<(), String> {
    let file = std::fs::File::create(path)
        .map_err(|e| format!("Failed to create {}: {e}", path.display()))?;
    run_with_files(compose, dump_args(), Stdio::null(), Stdio::from(file))
        .await
        .map_err(|e| format!("The database dump failed: {e}"))
}

pub async fn restore_from_file(compose: &Compose, path: &Path) -> Result<(), String> {
    let file =
        std::fs::File::open(path).map_err(|e| format!("Failed to open {}: {e}", path.display()))?;
    run_with_files(compose, restore_args(), Stdio::from(file), Stdio::null())
        .await
        .map_err(|e| format!("The database restore failed: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dump_uses_custom_format_without_tty() {
        let args = dump_args();
        assert_eq!(&args[..3], ["exec", "-T", "relational_db"]);
        assert!(args.contains(&"-Fc"));
    }

    #[test]
    fn restore_cleans_in_one_transaction() {
        let args = restore_args();
        assert!(args.contains(&"--clean"));
        assert!(args.contains(&"--if-exists"));
        assert!(args.contains(&"--single-transaction"));
    }

    #[test]
    fn tail_keeps_last_lines() {
        assert_eq!(tail("a\nb\nc\nd\ne\nf\ng"), "c\nd\ne\nf\ng");
    }
}
