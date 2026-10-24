//! Engine secrets and the engine `.env` file.

use crate::engine::hub::EngineImages;
use crate::fsutil::write_private;
use crate::secure_random::random_hex;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

/// Port of the engine web entry point on the loopback address.
pub const ENGINE_HOST_PORT: u16 = 3000;
pub const POSTGRES_USER: &str = "postgres";

/// Secrets that the engine needs. The app makes them once and keeps them in
/// `secrets.json` (mode 0600). Postgres reads its password only when it makes
/// a new data volume, so these values must not change after the first start.
#[derive(Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EngineSecrets {
    pub postgres_password: String,
    pub user_auth_secret: String,
    pub opensearch_admin_password: String,
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
            // OpenSearch needs upper case, lower case, a digit and a symbol.
            opensearch_admin_password: format!("Dx-{}-a9", random_hex::<16>()),
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

/// Everything that goes into the engine `.env` file.
pub struct EnvInput<'a> {
    pub images: &'a EngineImages,
    pub secrets: &'a EngineSecrets,
    pub route_api_key: Option<&'a str>,
    pub hub_url: &'a str,
}

/// Docker Compose reads `$` as a variable, `#` as a comment and quotes as
/// quoting, so only a safe set of characters can go into a value.
fn is_safe_env_value(value: &str) -> bool {
    value
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || "._:/@+=-~".contains(c))
}

fn push_line(out: &mut String, key: &str, value: &str) -> Result<(), String> {
    if !is_safe_env_value(value) {
        // The value can be a secret, so only the key goes into the message.
        return Err(format!(
            "The engine setting {key} has characters that are not permitted"
        ));
    }
    out.push_str(key);
    out.push('=');
    out.push_str(value);
    out.push('\n');
    Ok(())
}

/// Make the `.env` text. Fails if a value has characters that Compose would
/// change, so the engine never gets a value that is different from ours.
pub fn render_env(input: &EnvInput<'_>) -> Result<String, String> {
    let images = input.images;
    let secrets = input.secrets;
    let hub = input.hub_url.trim_end_matches('/');
    let port = ENGINE_HOST_PORT.to_string();

    let mut out = String::from(
        "# Written by the Deep X app on each engine start. Do not edit: changes are lost.\n",
    );
    let lines: [(&str, &str); 15] = [
        ("ONYX_BACKEND_IMAGE", &images.backend_image),
        ("ONYX_WEB_SERVER_IMAGE", &images.web_image),
        ("ONYX_MODEL_SERVER_IMAGE", &images.model_server_image),
        ("IMAGE_TAG", &images.image_tag),
        ("PRODUCT_BRAND", "deepx"),
        ("AUTH_TYPE", "basic"),
        ("POSTGRES_USER", POSTGRES_USER),
        ("POSTGRES_PASSWORD", &secrets.postgres_password),
        ("USER_AUTH_SECRET", &secrets.user_auth_secret),
        (
            "OPENSEARCH_ADMIN_PASSWORD",
            &secrets.opensearch_admin_password,
        ),
        // Files stay in Postgres in both modes, so a backup of the database
        // holds the user files too.
        ("FILE_STORE_BACKEND", "postgres"),
        ("MINIO_REPLICAS", "0"),
        ("HOST_PORT", &port),
        ("WEB_DOMAIN", &format!("http://localhost:{port}")),
        ("DEEPX_PRESETS_URL", &format!("{hub}/api/presets")),
    ];
    for (key, value) in lines {
        push_line(&mut out, key, value)?;
    }
    if let Some(key) = input.route_api_key {
        push_line(&mut out, "DEEPX_ROUTE_API_KEY", key)?;
    }
    Ok(out)
}

pub fn write_env(path: &Path, input: &EnvInput<'_>) -> Result<(), String> {
    let text = render_env(input)?;
    write_private(path, text.as_bytes())
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    fn images() -> EngineImages {
        EngineImages::fallback()
    }

    fn secrets() -> EngineSecrets {
        EngineSecrets {
            postgres_password: "pgpass".into(),
            user_auth_secret: "authsecret".into(),
            opensearch_admin_password: "Dx-os-a9".into(),
        }
    }

    fn env_map(text: &str) -> std::collections::HashMap<String, String> {
        text.lines()
            .filter(|line| !line.starts_with('#') && !line.is_empty())
            .map(|line| {
                let (key, value) = line.split_once('=').unwrap();
                (key.to_string(), value.to_string())
            })
            .collect()
    }

    #[test]
    fn env_has_contract_keys() {
        let images = images();
        let secrets = secrets();
        let text = render_env(&EnvInput {
            images: &images,
            secrets: &secrets,
            route_api_key: Some("sk-dxr-abc_DEF-123"),
            hub_url: "https://deepx-hub.vercel.app/",
        })
        .unwrap();
        let env = env_map(&text);
        assert_eq!(
            env["ONYX_BACKEND_IMAGE"],
            "ghcr.io/firebirdit/deepx-backend:latest"
        );
        assert_eq!(
            env["ONYX_WEB_SERVER_IMAGE"],
            "ghcr.io/firebirdit/deepx-web-server:latest"
        );
        assert_eq!(
            env["ONYX_MODEL_SERVER_IMAGE"],
            "ghcr.io/firebirdit/deepx-model-server:latest"
        );
        assert_eq!(env["IMAGE_TAG"], "latest");
        assert_eq!(env["PRODUCT_BRAND"], "deepx");
        assert_eq!(env["AUTH_TYPE"], "basic");
        assert_eq!(env["POSTGRES_PASSWORD"], "pgpass");
        assert_eq!(env["USER_AUTH_SECRET"], "authsecret");
        assert_eq!(env["DEEPX_ROUTE_API_KEY"], "sk-dxr-abc_DEF-123");
        assert_eq!(
            env["DEEPX_PRESETS_URL"],
            "https://deepx-hub.vercel.app/api/presets"
        );
        assert_eq!(env["FILE_STORE_BACKEND"], "postgres");
    }

    #[test]
    fn env_without_key_omits_it() {
        let images = images();
        let secrets = secrets();
        let text = render_env(&EnvInput {
            images: &images,
            secrets: &secrets,
            route_api_key: None,
            hub_url: "https://hub.example",
        })
        .unwrap();
        assert!(!text.contains("DEEPX_ROUTE_API_KEY"));
    }

    #[test]
    fn env_rejects_unsafe_values_without_echoing_them() {
        let images = images();
        let secrets = secrets();
        let err = render_env(&EnvInput {
            images: &images,
            secrets: &secrets,
            route_api_key: Some("sk-bad\nINJECT=1"),
            hub_url: "https://hub.example",
        })
        .unwrap_err();
        assert!(err.contains("DEEPX_ROUTE_API_KEY"));
        assert!(!err.contains("INJECT"));

        for bad in ["a$b", "a#b", "a b", "a'b", "a\"b"] {
            assert!(!is_safe_env_value(bad), "{bad} must be rejected");
        }
    }

    #[test]
    fn secrets_are_created_once_and_kept() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("secrets.json");
        let first = load_or_create_secrets(&path).unwrap();
        let second = load_or_create_secrets(&path).unwrap();
        assert_eq!(first, second);
        assert_eq!(first.user_auth_secret.len(), 64);
        assert!(is_safe_env_value(&first.opensearch_admin_password));
        assert!(!format!("{first:?}").contains(&first.postgres_password));
    }

    #[cfg(unix)]
    #[test]
    fn env_file_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".env");
        let images = images();
        let secrets = secrets();
        let input = EnvInput {
            images: &images,
            secrets: &secrets,
            route_api_key: None,
            hub_url: "https://hub.example",
        };
        write_env(&path, &input).unwrap();
        let mode = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
    }
}
