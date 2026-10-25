//! Engine secrets, kept in `engine/secrets.json` in the app data folder.

use crate::fsutil::write_private;
use crate::secure_random::random_hex;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

pub const POSTGRES_USER: &str = "postgres";

/// The secrets file. Earlier app versions made it for the Docker engine, so it
/// stays in the `engine` folder: an updated app keeps its database password.
pub fn secrets_path() -> Result<PathBuf, String> {
    crate::config::get_data_dir()
        .map(|dir| dir.join("engine").join("secrets.json"))
        .ok_or_else(|| "Could not find the app data folder".to_string())
}

/// Secrets that the engine needs. The app makes them once and keeps them in
/// `secrets.json` (mode 0600). The database password is set when the database
/// is made, so these values must not change after the first start.
#[derive(Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EngineSecrets {
    pub postgres_password: String,
    pub user_auth_secret: String,
}

impl std::fmt::Debug for EngineSecrets {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("EngineSecrets { .. }")
    }
}

impl EngineSecrets {
    pub fn generate() -> Self {
        Self {
            postgres_password: random_hex::<24>(),
            user_auth_secret: random_hex::<32>(),
        }
    }
}

/// Read the secrets file, or make and save new secrets if there is none.
pub fn load_or_create_secrets(path: &Path) -> Result<EngineSecrets, String> {
    match fs::read_to_string(path) {
        Ok(contents) => serde_json::from_str(&contents).map_err(|e| {
            format!(
                "The engine secrets file {} is not valid: {e}",
                path.display()
            )
        }),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            let secrets = EngineSecrets::generate();
            let json = serde_json::to_vec_pretty(&secrets)
                .map_err(|e| format!("Failed to encode engine secrets: {e}"))?;
            write_private(path, &json)?;
            Ok(secrets)
        }
        Err(e) => Err(format!("Failed to read {}: {e}", path.display())),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn secrets_are_made_once_and_old_files_still_load() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("secrets.json");
        let first = load_or_create_secrets(&path).unwrap();
        assert_eq!(load_or_create_secrets(&path).unwrap(), first);
        // A file from the Docker engine has one more field.
        fs::write(
            &path,
            r#"{"postgres_password":"p","user_auth_secret":"u","opensearch_admin_password":"o"}"#,
        )
        .unwrap();
        assert_eq!(
            load_or_create_secrets(&path).unwrap().postgres_password,
            "p"
        );
    }
}
