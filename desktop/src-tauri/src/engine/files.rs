//! The Docker Compose files of the local engine. They are part of the app
//! binary and the app writes them to the app data folder before each start.
//!
//! The layout in the data folder is the same as in the repo, because the base
//! compose file mounts `../data/nginx` relative to its own folder:
//!
//! ```text
//! <engine dir>/docker_compose/docker-compose.yml
//! <engine dir>/docker_compose/docker-compose.onyx-lite.yml
//! <engine dir>/docker_compose/docker-compose.deepx.yml
//! <engine dir>/docker_compose/.env
//! <engine dir>/data/nginx/...
//! ```

use crate::config::EngineMode;
use std::fs;
use std::path::{Path, PathBuf};

pub const COMPOSE_DIR: &str = "docker_compose";
pub const BASE_COMPOSE: &str = "docker-compose.yml";
pub const LITE_COMPOSE: &str = "docker-compose.onyx-lite.yml";
pub const DEEPX_COMPOSE: &str = "docker-compose.deepx.yml";
pub const ENV_FILE: &str = ".env";

const BASE_COMPOSE_CONTENTS: &str =
    include_str!("../../../../deployment/docker_compose/docker-compose.yml");
const LITE_COMPOSE_CONTENTS: &str =
    include_str!("../../../../deployment/docker_compose/docker-compose.onyx-lite.yml");

/// Desktop overlay. It publishes nginx on the loopback address only, so the
/// engine is not open to the local network, and it drops the port 80 binding,
/// which is often in use on a desktop computer.
const DEEPX_COMPOSE_CONTENTS: &str = r#"# Deep X desktop overlay. The Deep X app writes this file on each start.
services:
  nginx:
    ports: !override
      - "127.0.0.1:${HOST_PORT:-3000}:80"
"#;

/// nginx files that the `nginx` service mounts from `../data/nginx`.
const NGINX_FILES: [(&str, &str); 4] = [
    (
        "app.conf.template",
        include_str!("../../../../deployment/data/nginx/app.conf.template"),
    ),
    (
        "mcp.conf.inc.template",
        include_str!("../../../../deployment/data/nginx/mcp.conf.inc.template"),
    ),
    (
        "mcp_upstream.conf.inc.template",
        include_str!("../../../../deployment/data/nginx/mcp_upstream.conf.inc.template"),
    ),
    (
        "run-nginx.sh",
        include_str!("../../../../deployment/data/nginx/run-nginx.sh"),
    ),
];

/// Paths of the engine files inside one engine folder.
#[derive(Debug, Clone)]
pub struct EngineLayout {
    root: PathBuf,
}

impl EngineLayout {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn compose_dir(&self) -> PathBuf {
        self.root.join(COMPOSE_DIR)
    }

    pub fn env_path(&self) -> PathBuf {
        self.compose_dir().join(ENV_FILE)
    }

    pub fn secrets_path(&self) -> PathBuf {
        self.root.join("secrets.json")
    }

    pub fn nginx_dir(&self) -> PathBuf {
        self.root.join("data").join("nginx")
    }

    /// The compose files for `mode`, in the order `docker compose -f` needs.
    pub fn compose_files(&self, mode: EngineMode) -> Vec<PathBuf> {
        let dir = self.compose_dir();
        let mut files = vec![dir.join(BASE_COMPOSE)];
        if mode == EngineMode::Lite {
            files.push(dir.join(LITE_COMPOSE));
        }
        files.push(dir.join(DEEPX_COMPOSE));
        files
    }
}

/// Write the compose files and the nginx files. The `.env` file is written
/// separately (see `env.rs`) because it holds secrets.
pub fn write_compose_files(layout: &EngineLayout) -> Result<(), String> {
    let compose_dir = layout.compose_dir();
    create_dir(&compose_dir)?;
    write_file(&compose_dir.join(BASE_COMPOSE), BASE_COMPOSE_CONTENTS)?;
    write_file(&compose_dir.join(LITE_COMPOSE), LITE_COMPOSE_CONTENTS)?;
    write_file(&compose_dir.join(DEEPX_COMPOSE), DEEPX_COMPOSE_CONTENTS)?;

    let nginx_dir = layout.nginx_dir();
    create_dir(&nginx_dir)?;
    for (name, contents) in NGINX_FILES {
        write_file(&nginx_dir.join(name), contents)?;
    }
    Ok(())
}

fn create_dir(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path).map_err(|e| format!("Failed to create {}: {e}", path.display()))
}

fn write_file(path: &Path, contents: &str) -> Result<(), String> {
    // Skip the write when the file is already the same, so that a running
    // engine does not see a changed mount for no reason.
    if fs::read_to_string(path).is_ok_and(|current| current == contents) {
        return Ok(());
    }
    fs::write(path, contents).map_err(|e| format!("Failed to write {}: {e}", path.display()))
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn writes_compose_and_nginx_files() {
        let dir = tempfile::tempdir().unwrap();
        let layout = EngineLayout::new(dir.path());
        write_compose_files(&layout).unwrap();

        let base = fs::read_to_string(layout.compose_dir().join(BASE_COMPOSE)).unwrap();
        assert!(base.contains("relational_db:"));
        assert!(base.contains("../data/nginx:/nginx-templates:ro"));
        let lite = fs::read_to_string(layout.compose_dir().join(LITE_COMPOSE)).unwrap();
        assert!(lite.contains("DISABLE_VECTOR_DB=true"));
        let overlay = fs::read_to_string(layout.compose_dir().join(DEEPX_COMPOSE)).unwrap();
        assert!(overlay.contains("127.0.0.1:"));

        for (name, _) in NGINX_FILES {
            assert!(layout.nginx_dir().join(name).is_file(), "missing {name}");
        }
        // The base file mounts `../data/nginx` relative to its own folder.
        assert_eq!(
            layout
                .compose_dir()
                .join("..")
                .join("data")
                .join("nginx")
                .canonicalize()
                .unwrap(),
            layout.nginx_dir().canonicalize().unwrap()
        );
    }

    #[test]
    fn second_write_keeps_files() {
        let dir = tempfile::tempdir().unwrap();
        let layout = EngineLayout::new(dir.path());
        write_compose_files(&layout).unwrap();
        write_compose_files(&layout).unwrap();
        assert!(layout.compose_dir().join(BASE_COMPOSE).is_file());
    }

    #[test]
    fn lite_mode_adds_the_lite_overlay() {
        let layout = EngineLayout::new("/engine");
        let lite = layout.compose_files(EngineMode::Lite);
        let full = layout.compose_files(EngineMode::Full);
        assert_eq!(lite.len(), 3);
        assert_eq!(full.len(), 2);
        assert!(lite[1].ends_with(LITE_COMPOSE));
        assert!(full.iter().all(|path| !path.ends_with(LITE_COMPOSE)));
        assert!(lite.last().unwrap().ends_with(DEEPX_COMPOSE));
        assert!(full.last().unwrap().ends_with(DEEPX_COMPOSE));
    }
}
