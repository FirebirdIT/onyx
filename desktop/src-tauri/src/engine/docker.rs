//! The Docker CLI: find it, check it, and run `docker compose` for the engine.

use serde::Serialize;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio::process::Command;

pub const PROJECT_NAME: &str = "deepx";
const DOCKER_INSTALL_URL: &str = "https://www.docker.com/products/docker-desktop/";

/// Apps that start from the Finder or the Start menu do not get the shell
/// `PATH`, so look in the usual install folders too.
fn candidate_dirs() -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|path| std::env::split_paths(&path).collect())
        .unwrap_or_default();
    if cfg!(target_os = "macos") {
        dirs.extend(
            [
                "/usr/local/bin",
                "/opt/homebrew/bin",
                "/Applications/Docker.app/Contents/Resources/bin",
            ]
            .map(PathBuf::from),
        );
        if let Some(home) = directories::BaseDirs::new().map(|d| d.home_dir().to_path_buf()) {
            dirs.push(home.join(".docker").join("bin"));
            dirs.push(home.join(".orbstack").join("bin"));
            dirs.push(home.join(".rd").join("bin"));
        }
    }
    if cfg!(target_os = "windows") {
        dirs.push(PathBuf::from(
            r"C:\Program Files\Docker\Docker\resources\bin",
        ));
    }
    dirs
}

const fn docker_file_name() -> &'static str {
    if cfg!(target_os = "windows") {
        "docker.exe"
    } else {
        "docker"
    }
}

pub fn find_docker() -> Option<PathBuf> {
    candidate_dirs()
        .into_iter()
        .map(|dir| dir.join(docker_file_name()))
        .find(|path| path.is_file())
}

/// `PATH` for child processes: the Docker folder first, so that Docker
/// credential helpers next to the CLI are found.
fn child_path(docker: &Path) -> OsString {
    let mut dirs = candidate_dirs();
    if let Some(parent) = docker.parent() {
        dirs.insert(0, parent.to_path_buf());
    }
    std::env::join_paths(dirs).unwrap_or_default()
}

pub fn docker_command(docker: &Path) -> Command {
    let mut command = Command::new(docker);
    command.env("PATH", child_path(docker)).stdin(Stdio::null());
    #[cfg(target_os = "windows")]
    {
        // CREATE_NO_WINDOW: do not open a console window for each command.
        command.creation_flags(0x0800_0000);
    }
    command
}

#[derive(Debug, Clone, Serialize)]
pub struct DockerCheck {
    pub installed: bool,
    pub running: bool,
    pub compose: bool,
    pub server_version: Option<String>,
    pub message: String,
    pub install_url: &'static str,
}

async fn output_text(mut command: Command) -> Result<String, String> {
    let output = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await
        .map_err(|e| e.to_string())?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
    }
}

pub async fn check_docker() -> DockerCheck {
    let mut check = DockerCheck {
        installed: false,
        running: false,
        compose: false,
        server_version: None,
        message: String::new(),
        install_url: DOCKER_INSTALL_URL,
    };
    let Some(docker) = find_docker() else {
        check.message = "Docker is not installed. Install Docker Desktop, open it, and then select \"Check again\".".to_string();
        return check;
    };
    check.installed = true;

    let mut version = docker_command(&docker);
    version.args(["version", "--format", "{{.Server.Version}}"]);
    let Ok(server_version) = output_text(version).await else {
        check.message = "Docker is installed but it is not running. Open Docker Desktop, wait until the engine runs, and then select \"Check again\".".to_string();
        return check;
    };
    check.running = true;
    check.server_version = Some(server_version);

    let mut compose = docker_command(&docker);
    compose.args(["compose", "version", "--short"]);
    if output_text(compose).await.is_ok() {
        check.compose = true;
        check.message = "Docker is ready.".to_string();
    } else {
        check.message =
            "Docker Compose v2 is not available. Update Docker Desktop to the newest version."
                .to_string();
    }
    check
}

/// Open Docker Desktop, so that the user does not have to find it.
pub fn open_docker_desktop() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("open")
        .args(["-a", "Docker"])
        .status();
    #[cfg(target_os = "windows")]
    let result = std::process::Command::new(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
        .spawn()
        .map(|_| std::process::ExitStatus::default());
    #[cfg(target_os = "linux")]
    let result = std::process::Command::new("systemctl")
        .args(["--user", "start", "docker-desktop"])
        .status();

    match result {
        Ok(status) if status.success() => Ok(()),
        _ => Err("Could not open Docker Desktop. Open it from your applications.".to_string()),
    }
}

/// `docker compose` for the Deep X project with a fixed set of files.
#[derive(Debug, Clone)]
pub struct Compose {
    docker: PathBuf,
    project_dir: PathBuf,
    files: Vec<PathBuf>,
}

impl Compose {
    pub const fn new(docker: PathBuf, project_dir: PathBuf, files: Vec<PathBuf>) -> Self {
        Self {
            docker,
            project_dir,
            files,
        }
    }

    pub fn args<'a>(&self, extra: impl IntoIterator<Item = &'a str>) -> Vec<OsString> {
        let mut args: Vec<OsString> = vec!["compose".into(), "--progress".into(), "plain".into()];
        args.extend(["-p".into(), PROJECT_NAME.into()]);
        args.extend([
            "--project-directory".into(),
            self.project_dir.clone().into(),
        ]);
        for file in &self.files {
            args.push("-f".into());
            args.push(file.clone().into());
        }
        args.extend(extra.into_iter().map(OsString::from));
        args
    }

    pub fn command<'a>(&self, extra: impl IntoIterator<Item = &'a str>) -> Command {
        let mut command = docker_command(&self.docker);
        command.args(self.args(extra));
        command
    }

    /// Run a compose command and send each output line to `on_line`.
    pub async fn run_streamed<'a, F>(
        &self,
        extra: impl IntoIterator<Item = &'a str>,
        on_line: F,
    ) -> Result<(), String>
    where
        F: Fn(&str) + Send + Sync,
    {
        let mut child = self
            .command(extra)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Failed to run docker compose: {e}"))?;
        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        let last_lines = std::sync::Mutex::new(Vec::<String>::new());
        let handle = |line: &str| {
            on_line(line);
            let mut lines = last_lines
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            lines.push(line.to_string());
            if lines.len() > 5 {
                lines.remove(0);
            }
        };
        tokio::join!(pump(stdout, &handle), pump(stderr, &handle));
        let status = child
            .wait()
            .await
            .map_err(|e| format!("docker compose did not finish: {e}"))?;
        if status.success() {
            Ok(())
        } else {
            let tail = last_lines
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .join("\n");
            Err(format!("docker compose failed ({status}).\n{tail}"))
        }
    }

    /// Run a compose command and return its standard output.
    pub async fn run_capture<'a>(
        &self,
        extra: impl IntoIterator<Item = &'a str>,
    ) -> Result<String, String> {
        output_text(self.command(extra)).await
    }
}

async fn pump<R, F>(reader: Option<R>, on_line: &F)
where
    R: AsyncRead + Unpin + Send,
    F: Fn(&str) + Sync,
{
    let Some(reader) = reader else { return };
    let mut lines = BufReader::new(reader).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let trimmed = line.trim();
        if !trimmed.is_empty() {
            on_line(trimmed);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compose_args_name_project_and_files_in_order() {
        let compose = Compose::new(
            PathBuf::from("docker"),
            PathBuf::from("/engine/docker_compose"),
            vec![PathBuf::from("/a.yml"), PathBuf::from("/b.yml")],
        );
        let args: Vec<String> = compose
            .args(["up", "-d"])
            .into_iter()
            .map(|a| a.to_string_lossy().to_string())
            .collect();
        assert_eq!(
            args,
            [
                "compose",
                "--progress",
                "plain",
                "-p",
                "deepx",
                "--project-directory",
                "/engine/docker_compose",
                "-f",
                "/a.yml",
                "-f",
                "/b.yml",
                "up",
                "-d"
            ]
        );
    }
}
