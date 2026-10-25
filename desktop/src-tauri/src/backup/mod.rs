//! Encrypted backup and restore of the engine database through the hub.
// Tauri command handlers take IPC arguments and extractors by value.
#![allow(clippy::needless_pass_by_value)]

pub mod crypto;
pub mod hub_client;
pub mod window;

use crate::config::{get_data_dir, ConfigState};
use crate::engine::{self, EngineLock};
use crate::route::keystore;
use crypto::{decrypt_stream, derive_keys, encrypt_stream, BackupKeys};
use hub_client::{download_part, part_count, HubBackup, MAX_PART_BYTES};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{BufWriter, Read};
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Emitter, Manager, State};

pub const PROGRESS_EVENT: &str = "backup-progress";
const MIN_PASSPHRASE_CHARS: usize = 12;

fn progress(app: &AppHandle, line: &str) {
    // A closed window only loses the progress text.
    let _ = app.emit(PROGRESS_EVENT, line);
}

/// A work folder that is deleted when the backup or restore ends, so that
/// no plain-text dump stays on the disk.
struct WorkDir(PathBuf);

impl WorkDir {
    fn create() -> Result<Self, String> {
        let dir = get_data_dir()
            .ok_or("Could not find the app data folder")?
            .join("backup-work");
        // A folder from a crashed run has no value.
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir)
            .map_err(|e| format!("Failed to create the work folder: {e}"))?;
        Ok(Self(dir))
    }

    fn path(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for WorkDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn validate_input(email: &str, passphrase: &str) -> Result<(), String> {
    let email = email.trim();
    if email.len() < 3 || !email.contains('@') {
        return Err("Enter a valid email address.".to_string());
    }
    if passphrase.chars().count() < MIN_PASSPHRASE_CHARS {
        return Err(format!(
            "The passphrase must have at least {MIN_PASSPHRASE_CHARS} characters."
        ));
    }
    Ok(())
}

async fn keys_for(email: String, passphrase: String) -> Result<BackupKeys, String> {
    tauri::async_runtime::spawn_blocking(move || derive_keys(&email, &passphrase))
        .await
        .map_err(|e| format!("Key derivation did not finish: {e}"))?
}

fn route_key() -> Result<String, String> {
    keystore::load_key()
        .ok_or_else(|| "Connect Deep X Route first. The hub needs the key.".to_string())
}

async fn require_running() -> Result<(), String> {
    if engine::status().await.is_ok_and(|s| s.running) {
        Ok(())
    } else {
        Err("Start the engine first.".to_string())
    }
}

fn sha256_file(path: &Path) -> Result<(String, u64), String> {
    let mut file = File::open(path).map_err(|e| format!("Failed to open the backup: {e}"))?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1024 * 1024];
    let mut size = 0u64;
    loop {
        let n = file
            .read(&mut buffer)
            .map_err(|e| format!("Read failed: {e}"))?;
        if n == 0 {
            break;
        }
        hasher.update(&buffer[..n]);
        size += n as u64;
    }
    Ok((hex::encode(hasher.finalize()), size))
}

async fn run_blocking<T: Send + 'static>(
    f: impl FnOnce() -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(f)
        .await
        .map_err(|e| format!("Work did not finish: {e}"))?
}

async fn upload(app: &AppHandle, hub: &HubBackup<'_>, path: &Path) -> Result<(), String> {
    let source = path.to_path_buf();
    let (sha256, size) = run_blocking(move || sha256_file(&source)).await?;
    let parts = part_count(size);
    let upload_id = hub.start(parts, size, &sha256).await?;
    let mut file = File::open(path).map_err(|e| format!("Failed to open the backup: {e}"))?;
    for index in 0..parts {
        let mut part = vec![0u8; MAX_PART_BYTES];
        let mut filled = 0;
        while filled < part.len() {
            let n = file
                .read(&mut part[filled..])
                .map_err(|e| format!("Read failed: {e}"))?;
            if n == 0 {
                break;
            }
            filled += n;
        }
        part.truncate(filled);
        progress(app, &format!("Uploading part {} of {parts}", index + 1));
        hub.put_part(&upload_id, index, part).await?;
    }
    hub.commit(&upload_id).await
}

#[derive(Serialize)]
pub struct BackupResult {
    size: u64,
}

async fn backup_flow(
    app: &AppHandle,
    email: String,
    passphrase: String,
) -> Result<BackupResult, String> {
    validate_input(&email, &passphrase)?;
    let route_key = route_key()?;
    require_running().await?;
    let config = app.state::<ConfigState>().config();
    progress(app, "Making the backup keys");
    let keys = keys_for(email, passphrase).await?;
    let work = WorkDir::create()?;
    let dump_path = work.path("engine.dump");
    let sealed_path = work.path("engine.dxb");

    progress(app, "Exporting the database");
    dump_database(&dump_path).await?;
    progress(app, "Encrypting");
    let keys = std::sync::Arc::new(keys);
    let (source, target, sealing_keys) = (dump_path.clone(), sealed_path.clone(), keys.clone());
    run_blocking(move || {
        let input = File::open(&source).map_err(|e| format!("Failed to open the dump: {e}"))?;
        let output =
            File::create(&target).map_err(|e| format!("Failed to create the backup: {e}"))?;
        encrypt_stream(&sealing_keys, input, BufWriter::new(output))
    })
    .await?;
    // The plain-text dump is not necessary after this point.
    let _ = std::fs::remove_file(&dump_path);

    let hub = HubBackup {
        hub_url: &config.hub_url,
        backup_id: &keys.backup_id,
        token: &keys.token,
        route_key: &route_key,
    };
    upload(app, &hub, &sealed_path).await?;
    let size = std::fs::metadata(&sealed_path).map_or(0, |m| m.len());
    progress(app, "The backup is complete");
    Ok(BackupResult { size })
}

async fn download(app: &AppHandle, hub: &HubBackup<'_>, path: &Path) -> Result<(), String> {
    let info = hub
        .info()
        .await?
        .ok_or("No backup found for this email and passphrase.")?;
    progress(app, &format!("Found a backup from {}", info.created_at));
    let mut file = File::create(path).map_err(|e| format!("Failed to create the download: {e}"))?;
    let total = info.parts.len();
    for (index, url) in info.parts.iter().enumerate() {
        progress(app, &format!("Downloading part {} of {total}", index + 1));
        let bytes = download_part(url).await?;
        std::io::Write::write_all(&mut file, &bytes).map_err(|e| format!("Write failed: {e}"))?;
    }
    drop(file);
    let source = path.to_path_buf();
    let (sha256, size) = run_blocking(move || sha256_file(&source)).await?;
    if size != info.size || !sha256.eq_ignore_ascii_case(&info.sha256) {
        return Err("The downloaded backup is damaged. Try again.".to_string());
    }
    Ok(())
}

#[cfg(unix)]
async fn dump_database(dump_path: &Path) -> Result<(), String> {
    engine::native::dump_database(dump_path).await
}

#[cfg(unix)]
async fn restore_database(app: &AppHandle, dump_path: &Path) -> Result<(), String> {
    progress(app, "Restoring the database");
    engine::native::restore_database(app, dump_path).await
}

#[cfg(not(unix))]
async fn dump_database(_dump_path: &Path) -> Result<(), String> {
    Err("Backups need the engine, which does not run on this computer yet.".to_string())
}

#[cfg(not(unix))]
async fn restore_database(_app: &AppHandle, _dump_path: &Path) -> Result<(), String> {
    Err("Backups need the engine, which does not run on this computer yet.".to_string())
}

async fn restore_flow(app: &AppHandle, email: String, passphrase: String) -> Result<(), String> {
    validate_input(&email, &passphrase)?;
    let route_key = route_key()?;
    require_running().await?;
    let config = app.state::<ConfigState>().config();
    progress(app, "Making the backup keys");
    let keys = keys_for(email, passphrase).await?;
    let work = WorkDir::create()?;
    let sealed_path = work.path("engine.dxb");
    let dump_path = work.path("engine.dump");
    let hub = HubBackup {
        hub_url: &config.hub_url,
        backup_id: &keys.backup_id,
        token: &keys.token,
        route_key: &route_key,
    };
    download(app, &hub, &sealed_path).await?;

    progress(app, "Decrypting");
    let keys = std::sync::Arc::new(keys);
    let (source, target, opening_keys) = (sealed_path.clone(), dump_path.clone(), keys.clone());
    run_blocking(move || {
        let input = File::open(&source).map_err(|e| format!("Failed to open the backup: {e}"))?;
        let output =
            File::create(&target).map_err(|e| format!("Failed to create the dump: {e}"))?;
        decrypt_stream(&opening_keys, input, BufWriter::new(output))
    })
    .await?;

    let restored = restore_database(app, &dump_path).await;
    // Start the services again also when the restore failed.
    progress(app, "Starting the engine again");
    let started = engine::start(app, &config).await;
    restored?;
    started?;
    progress(app, "The restore is complete");
    Ok(())
}

#[tauri::command]
pub async fn backup_now(
    app: AppHandle,
    lock: State<'_, EngineLock>,
    email: String,
    passphrase: String,
) -> Result<BackupResult, String> {
    let _guard = lock.0.lock().await;
    backup_flow(&app, email, passphrase).await
}

#[tauri::command]
pub async fn backup_restore(
    app: AppHandle,
    lock: State<'_, EngineLock>,
    email: String,
    passphrase: String,
) -> Result<(), String> {
    let _guard = lock.0.lock().await;
    restore_flow(&app, email, passphrase).await
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn input_rules() {
        assert!(validate_input("a@b.co", "twelve chars").is_ok());
        assert!(validate_input("no-at", "twelve chars").is_err());
        assert!(validate_input("a@b.co", "short").is_err());
    }

    #[test]
    fn sha256_file_matches_known_digest() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("f");
        std::fs::write(&path, b"abc").unwrap();
        let (digest, size) = sha256_file(&path).unwrap();
        assert_eq!(size, 3);
        assert_eq!(
            digest,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
