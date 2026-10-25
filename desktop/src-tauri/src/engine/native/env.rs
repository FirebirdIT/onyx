//! Settings of the native engine processes.

use super::bundle::Bundle;
use crate::config::NATIVE_SERVER_URL;
use crate::engine::env::{EngineSecrets, POSTGRES_USER};
use std::path::Path;

/// Port of the native engine web entry point on the loopback address.
pub const NATIVE_WEB_PORT: u16 = 9999;
pub const LOOPBACK: &str = "127.0.0.1";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ports {
    pub postgres: u16,
    pub api: u16,
    pub runner: u16,
    pub web: u16,
}

/// Everything that the settings come from.
pub struct NativeInput<'a> {
    pub bundle: &'a Bundle,
    pub ports: Ports,
    pub secrets: &'a EngineSecrets,
    pub route_api_key: Option<&'a str>,
    pub hub_url: &'a str,
    pub runner_token: &'a str,
}

type Env = Vec<(String, String)>;

/// The read-only database role that the backend uses for knowledge graph
/// views. Postgres keeps roles outside a dump, so an import makes it again.
pub const READONLY_ROLE: &str = "db_readonly_user";

/// The read-only role password: stable for the engine, but not the backend
/// default "password". Hex only, so it is safe in SQL text.
pub fn readonly_password(secrets: &EngineSecrets) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(format!("deepx-readonly:{}", secrets.postgres_password));
    hex::encode(&digest[..16])
}

fn pairs(items: &[(&str, &str)]) -> Env {
    items
        .iter()
        .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
        .collect()
}

/// The settings that the migrations and the API server share.
pub fn backend_env(input: &NativeInput<'_>) -> Env {
    let hub = input.hub_url.trim_end_matches('/');
    let backend = input.bundle.backend_dir();
    let mut env = pairs(&[
        ("POSTGRES_HOST", LOOPBACK),
        ("POSTGRES_PORT", &input.ports.postgres.to_string()),
        ("POSTGRES_USER", POSTGRES_USER),
        ("POSTGRES_PASSWORD", &input.secrets.postgres_password),
        ("DB_READONLY_USER", READONLY_ROLE),
        ("DB_READONLY_PASSWORD", &readonly_password(input.secrets)),
        ("AUTH_TYPE", "basic"),
        ("USER_AUTH_SECRET", &input.secrets.user_auth_secret),
        ("PRODUCT_BRAND", "deepx"),
        // Lite: no vector database, and Postgres holds files, cache and auth.
        ("DISABLE_VECTOR_DB", "true"),
        ("FILE_STORE_BACKEND", "postgres"),
        ("CACHE_BACKEND", "postgres"),
        ("AUTH_BACKEND", "postgres"),
        ("WEB_DOMAIN", NATIVE_SERVER_URL),
        ("DEEPX_PRESETS_URL", &format!("{hub}/api/presets")),
        ("DEEPX_HUB_URL", hub),
        ("DEEPX_LOCAL_ENGINE", "true"),
        (
            "CODE_INTERPRETER_BASE_URL",
            &format!("http://{LOOPBACK}:{}", input.ports.runner),
        ),
        ("CODE_INTERPRETER_API_TOKEN", input.runner_token),
        ("PYTHONPATH", &backend.to_string_lossy()),
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("PYTHONUNBUFFERED", "1"),
    ]);
    if let Some(key) = input.route_api_key {
        env.push(("DEEPX_ROUTE_API_KEY".to_string(), key.to_string()));
    }
    env
}

/// The settings of the code runner.
pub fn runner_env(input: &NativeInput<'_>, data_dir: &Path) -> Env {
    let bundle = input.bundle;
    // The runner splits this value like a shell command line, and the app
    // folder has a space ("Application Support").
    let srt = format!(
        "{} {}",
        shell_quote(&bundle.node().to_string_lossy()),
        shell_quote(&bundle.srt_cli().to_string_lossy())
    );
    pairs(&[
        ("DEEPX_CODE_RUNNER_HOST", LOOPBACK),
        ("DEEPX_CODE_RUNNER_PORT", &input.ports.runner.to_string()),
        ("DEEPX_CODE_RUNNER_TOKEN", input.runner_token),
        ("DEEPX_CODE_RUNNER_DATA_DIR", &data_dir.to_string_lossy()),
        (
            "DEEPX_CODE_RUNNER_PYTHON",
            &bundle.job_python().to_string_lossy(),
        ),
        ("DEEPX_CODE_RUNNER_SRT_COMMAND", &srt),
        ("PYTHONPATH", &bundle.backend_dir().to_string_lossy()),
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("PATH", &path_with(&bundle.node_bin_dir())),
    ])
}

/// The settings of the web server.
pub fn web_env(input: &NativeInput<'_>) -> Env {
    pairs(&[
        ("PORT", &input.ports.web.to_string()),
        ("HOSTNAME", LOOPBACK),
        ("NODE_ENV", "production"),
        ("NEXT_TELEMETRY_DISABLED", "1"),
        (
            "INTERNAL_URL",
            &format!("http://{LOOPBACK}:{}", input.ports.api),
        ),
        // The web server forwards /api to the API server itself, because the
        // native engine has no nginx.
        ("OVERRIDE_API_PRODUCTION", "true"),
    ])
}

/// Quote `value` for a POSIX shell (and Python `shlex.split`).
fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

fn path_with(dir: &Path) -> String {
    let current = std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/bin".to_string());
    format!("{}:{current}", dir.to_string_lossy())
}

/// A free TCP port on the loopback address.
pub fn free_port() -> Result<u16, String> {
    std::net::TcpListener::bind((LOOPBACK, 0))
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .map_err(|e| format!("No free local port: {e}"))
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use crate::engine::native::bundle::Manifest;
    use std::collections::HashMap;
    use std::path::PathBuf;

    fn bundle() -> Bundle {
        Bundle {
            root: PathBuf::from("/b"),
            manifest: Manifest {
                version: "0.2.0".into(),
                platform: "darwin-aarch64".into(),
                web_server: "web/server.js".into(),
            },
        }
    }

    fn secrets() -> EngineSecrets {
        EngineSecrets {
            postgres_password: "pgpass".into(),
            user_auth_secret: "authsecret".into(),
            opensearch_admin_password: "x".into(),
        }
    }

    const PORTS: Ports = Ports {
        postgres: 5001,
        api: 5002,
        runner: 5003,
        web: NATIVE_WEB_PORT,
    };

    fn map(env: Env) -> HashMap<String, String> {
        env.into_iter().collect()
    }

    #[test]
    fn backend_reaches_postgres_and_the_runner_with_the_token() {
        let (bundle, secrets) = (bundle(), secrets());
        let input = NativeInput {
            bundle: &bundle,
            ports: PORTS,
            secrets: &secrets,
            route_api_key: Some("dxr-key"),
            hub_url: "https://hub.example/",
            runner_token: "tok",
        };
        let env = map(backend_env(&input));
        assert_eq!(env["POSTGRES_PORT"], "5001");
        assert_eq!(env["POSTGRES_PASSWORD"], "pgpass");
        assert_eq!(env["DB_READONLY_PASSWORD"], readonly_password(&secrets));
        assert_eq!(env["DB_READONLY_PASSWORD"].len(), 32);
        assert_ne!(env["DB_READONLY_PASSWORD"], "password");
        assert_eq!(env["CODE_INTERPRETER_BASE_URL"], "http://127.0.0.1:5003");
        assert_eq!(env["CODE_INTERPRETER_API_TOKEN"], "tok");
        assert_eq!(env["DISABLE_VECTOR_DB"], "true");
        assert_eq!(env["DEEPX_PRESETS_URL"], "https://hub.example/api/presets");
        assert_eq!(env["DEEPX_ROUTE_API_KEY"], "dxr-key");
        assert_eq!(env["WEB_DOMAIN"], "http://localhost:9999");
        assert_eq!(env["PYTHONPATH"], "/b/backend");

        let runner = map(runner_env(&input, Path::new("/data/runner")));
        assert_eq!(runner["DEEPX_CODE_RUNNER_TOKEN"], "tok");
        assert_eq!(runner["DEEPX_CODE_RUNNER_PORT"], "5003");
        assert_eq!(
            runner["DEEPX_CODE_RUNNER_PYTHON"],
            "/b/jobpython/bin/python3"
        );
        assert!(runner["PATH"].starts_with("/b/node/bin:"));

        let web = map(web_env(&input));
        assert_eq!(web["PORT"], "9999");
        assert_eq!(web["INTERNAL_URL"], "http://127.0.0.1:5002");
        assert_eq!(web["OVERRIDE_API_PRODUCTION"], "true");
    }

    #[test]
    fn no_route_key_means_no_route_setting() {
        let (bundle, secrets) = (bundle(), secrets());
        let input = NativeInput {
            bundle: &bundle,
            ports: PORTS,
            secrets: &secrets,
            route_api_key: None,
            hub_url: "https://hub.example",
            runner_token: "tok",
        };
        assert!(!map(backend_env(&input)).contains_key("DEEPX_ROUTE_API_KEY"));
    }

    #[test]
    fn srt_command_survives_spaces_and_quotes() {
        assert_eq!(shell_quote("/a b/node"), "'/a b/node'");
        assert_eq!(shell_quote("it's"), "'it'\\''s'");
        let spaced = Bundle {
            root: PathBuf::from("/Users/x/Library/Application Support/b"),
            ..bundle()
        };
        let secrets = secrets();
        let input = NativeInput {
            bundle: &spaced,
            ports: PORTS,
            secrets: &secrets,
            route_api_key: None,
            hub_url: "https://hub.example",
            runner_token: "tok",
        };
        let runner = map(runner_env(&input, Path::new("/d")));
        assert!(runner["DEEPX_CODE_RUNNER_SRT_COMMAND"]
            .starts_with("'/Users/x/Library/Application Support/b/node/bin/node' '"));
    }

    #[test]
    fn free_ports_are_distinct_and_bindable() {
        let a = free_port().unwrap();
        assert!(a > 0);
    }
}
