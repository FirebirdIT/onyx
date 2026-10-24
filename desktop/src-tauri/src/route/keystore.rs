//! Storage of the Deep X Route API key: the OS keychain first, and a 0600
//! file in the config folder if there is no keychain.

use crate::config::get_config_dir;
use crate::fsutil::write_private;
use std::fs;
use std::path::{Path, PathBuf};

const SERVICE: &str = "com.deepx.desktop";
const ACCOUNT: &str = "deepx-route-api-key";
const FALLBACK_FILE: &str = "deepx-route-key";

fn fallback_path() -> Option<PathBuf> {
    get_config_dir().map(|dir| dir.join(FALLBACK_FILE))
}

fn entry() -> Option<keyring::Entry> {
    keyring::Entry::new(SERVICE, ACCOUNT).ok()
}

/// Where the key went.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum KeyLocation {
    Keychain,
    File,
}

pub fn save_key(key: &str) -> Result<KeyLocation, String> {
    if entry().is_some_and(|entry| entry.set_password(key).is_ok()) {
        // Keep one copy only.
        if let Some(path) = fallback_path() {
            remove_file(&path);
        }
        return Ok(KeyLocation::Keychain);
    }
    let path = fallback_path().ok_or("Could not find the config folder")?;
    save_to_file(&path, key)?;
    Ok(KeyLocation::File)
}

pub fn load_key() -> Option<String> {
    entry()
        .and_then(|entry| entry.get_password().ok())
        .filter(|key| !key.is_empty())
        .or_else(|| fallback_path().and_then(|path| load_from_file(&path)))
}

pub fn delete_key() {
    if let Some(entry) = entry() {
        // No key in the keychain is not an error here.
        let _ = entry.delete_credential();
    }
    if let Some(path) = fallback_path() {
        remove_file(&path);
    }
}

fn save_to_file(path: &Path, key: &str) -> Result<(), String> {
    write_private(path, key.as_bytes())
}

fn load_from_file(path: &Path) -> Option<String> {
    fs::read_to_string(path)
        .ok()
        .map(|text| text.trim().to_string())
        .filter(|key| !key.is_empty())
}

fn remove_file(path: &Path) {
    // A missing file is the wanted result.
    let _ = fs::remove_file(path);
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn file_fallback_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(FALLBACK_FILE);
        assert_eq!(load_from_file(&path), None);
        save_to_file(&path, "sk-dxr-test").unwrap();
        assert_eq!(load_from_file(&path).as_deref(), Some("sk-dxr-test"));
        remove_file(&path);
        assert_eq!(load_from_file(&path), None);
    }

    #[cfg(unix)]
    #[test]
    fn file_fallback_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(FALLBACK_FILE);
        save_to_file(&path, "sk-dxr-test").unwrap();
        let mode = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
    }
}
