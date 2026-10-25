//! Start, watch and stop the engine processes.

use std::fs::OpenOptions;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::process::{Child, Command};
use tokio::task::JoinHandle;

/// A crashed service restarts at most this many times in `RESTART_WINDOW`.
const MAX_RESTARTS: usize = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(60);
const RESTART_DELAY: Duration = Duration::from_secs(2);

/// How to run one service.
#[derive(Debug, Clone)]
pub struct ServiceSpec {
    pub name: &'static str,
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    pub env: Vec<(String, String)>,
    pub log: PathBuf,
    /// The signal for a clean stop, for example "TERM", or "INT" for the fast
    /// shutdown of Postgres.
    pub stop_signal: &'static str,
    /// Windows has no signals. There, a clean stop runs
    /// `<signal_with> kill <stop_signal> <pid>` (Postgres `pg_ctl`). Without
    /// it, a clean stop on Windows ends the processes.
    #[cfg_attr(not(windows), expect(dead_code, reason = "read only on Windows"))]
    pub signal_with: Option<PathBuf>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ServiceState {
    Running,
    Restarting,
    Failed,
    Stopped,
}

impl ServiceState {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Restarting => "restarting",
            Self::Failed => "failed",
            Self::Stopped => "stopped",
        }
    }
}

fn command_for(spec: &ServiceSpec) -> Result<Command, String> {
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&spec.log)
        .map_err(|e| format!("Failed to open {}: {e}", spec.log.display()))?;
    let log_err = log
        .try_clone()
        .map_err(|e| format!("Failed to open {}: {e}", spec.log.display()))?;
    let mut command = Command::new(&spec.program);
    command
        .args(&spec.args)
        .current_dir(&spec.cwd)
        .envs(spec.env.iter().map(|(k, v)| (k.as_str(), v.as_str())))
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(log_err))
        .kill_on_drop(false);
    // Each service has its own process group (a job on Windows), so a stop
    // reaches its children.
    #[cfg(unix)]
    command.process_group(0);
    #[cfg(windows)]
    command.creation_flags(super::job::SPAWN_FLAGS);
    Ok(command)
}

/// A service process with its children: a process group on Unix, a job on
/// Windows.
struct Group {
    pid: u32,
    #[cfg(windows)]
    job: Option<super::job::Job>,
}

impl Group {
    #[cfg(unix)]
    fn of(child: &Child) -> Self {
        Self {
            pid: child.id().unwrap_or(0),
        }
    }

    #[cfg(windows)]
    fn of(child: &Child) -> Self {
        let pid = child.id().unwrap_or(0);
        // Without a job, `kill` falls back to `taskkill /T`.
        let job = child
            .raw_handle()
            .and_then(|handle| super::job::Job::adopt(handle, pid).ok());
        Self { pid, job }
    }

    /// The clean stop of `spec`.
    fn stop(&self, spec: &ServiceSpec) {
        #[cfg(unix)]
        self.signal(spec.stop_signal);
        #[cfg(windows)]
        match &spec.signal_with {
            Some(program) if self.pid != 0 => {
                let _ = std::process::Command::new(program)
                    .args(["kill", spec.stop_signal, &self.pid.to_string()])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .creation_flags(super::job::HELPER_FLAGS)
                    .status();
            }
            _ => self.kill(),
        }
    }

    /// End the process and its children now.
    fn kill(&self) {
        #[cfg(unix)]
        self.signal("KILL");
        #[cfg(windows)]
        if let Some(job) = &self.job {
            job.terminate();
        } else if self.pid != 0 {
            let _ = std::process::Command::new("taskkill")
                .args(["/T", "/F", "/PID", &self.pid.to_string()])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .creation_flags(super::job::HELPER_FLAGS)
                .status();
        }
    }

    /// Send `signal` to the process group.
    #[cfg(unix)]
    fn signal(&self, signal: &str) {
        if self.pid == 0 {
            return;
        }
        // A group that has already ended makes `kill` fail, which is fine.
        let _ = std::process::Command::new("/bin/kill")
            .arg(format!("-{signal}"))
            .arg(format!("-{}", self.pid))
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
}

fn spawn(spec: &ServiceSpec) -> Result<(Child, Group), String> {
    let child = command_for(spec)?
        .spawn()
        .map_err(|e| format!("Failed to start {}: {e}", spec.name))?;
    let group = Group::of(&child);
    Ok((child, group))
}

/// Run a one-time step (for example `initdb` or the migrations) to the end.
pub async fn run_to_end(spec: &ServiceSpec, timeout: Duration) -> Result<(), String> {
    let (mut child, group) = spawn(spec)?;
    match tokio::time::timeout(timeout, child.wait()).await {
        Ok(Ok(status)) if status.success() => Ok(()),
        Ok(Ok(status)) => Err(format!(
            "{} failed ({status}). See {}",
            spec.name,
            spec.log.display()
        )),
        Ok(Err(e)) => Err(format!("{} did not finish: {e}", spec.name)),
        Err(_) => {
            group.kill();
            Err(format!("{} took too long and was stopped", spec.name))
        }
    }
}

/// A service that restarts when it crashes, until `stop` is called.
pub struct Supervised {
    pub name: &'static str,
    spec: ServiceSpec,
    group: Arc<Mutex<Arc<Group>>>,
    stopping: Arc<AtomicBool>,
    state: Arc<Mutex<ServiceState>>,
    task: JoinHandle<()>,
}

fn set_state(state: &Mutex<ServiceState>, value: ServiceState) {
    if let Ok(mut guard) = state.lock() {
        *guard = value;
    }
}

fn current(group: &Mutex<Arc<Group>>) -> Option<Arc<Group>> {
    group.lock().ok().map(|guard| guard.clone())
}

impl Supervised {
    pub fn start(spec: ServiceSpec) -> Result<Self, String> {
        let (first, group) = spawn(&spec)?;
        let group = Arc::new(Mutex::new(Arc::new(group)));
        let stopping = Arc::new(AtomicBool::new(false));
        let state = Arc::new(Mutex::new(ServiceState::Running));
        let task = tokio::spawn(watch(
            spec.clone(),
            first,
            group.clone(),
            stopping.clone(),
            state.clone(),
        ));
        Ok(Self {
            name: spec.name,
            spec,
            group,
            stopping,
            state,
            task,
        })
    }

    pub fn state(&self) -> ServiceState {
        self.state
            .lock()
            .map_or(ServiceState::Failed, |guard| *guard)
    }

    /// Stop the service: the clean stop first, and after `grace` a kill.
    pub async fn stop(self, grace: Duration) {
        self.stopping.store(true, Ordering::SeqCst);
        let group = current(&self.group);
        if let Some(group) = &group {
            group.stop(&self.spec);
        }
        let mut task = self.task;
        if tokio::time::timeout(grace, &mut task).await.is_err() {
            if let Some(group) = &group {
                group.kill();
            }
            let _ = tokio::time::timeout(Duration::from_secs(5), task).await;
        }
    }

    /// Stop without an async runtime, for app exit.
    pub fn stop_blocking(&self, grace: Duration) {
        self.stopping.store(true, Ordering::SeqCst);
        let group = current(&self.group);
        if let Some(group) = &group {
            group.stop(&self.spec);
        }
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline && !self.task.is_finished() {
            std::thread::sleep(Duration::from_millis(100));
        }
        if !self.task.is_finished() {
            if let Some(group) = &group {
                group.kill();
            }
        }
    }
}

async fn watch(
    spec: ServiceSpec,
    mut child: Child,
    group: Arc<Mutex<Arc<Group>>>,
    stopping: Arc<AtomicBool>,
    state: Arc<Mutex<ServiceState>>,
) {
    let mut restarts: Vec<Instant> = Vec::new();
    loop {
        let _ = child.wait().await;
        // Children of the service can live on after it ends.
        if let Some(ended) = current(&group) {
            ended.kill();
        }
        if stopping.load(Ordering::SeqCst) {
            set_state(&state, ServiceState::Stopped);
            return;
        }
        let now = Instant::now();
        restarts.retain(|at| now.duration_since(*at) < RESTART_WINDOW);
        if restarts.len() >= MAX_RESTARTS {
            set_state(&state, ServiceState::Failed);
            return;
        }
        restarts.push(now);
        set_state(&state, ServiceState::Restarting);
        tokio::time::sleep(RESTART_DELAY).await;
        if stopping.load(Ordering::SeqCst) {
            set_state(&state, ServiceState::Stopped);
            return;
        }
        let Ok((next, next_group)) = spawn(&spec) else {
            set_state(&state, ServiceState::Failed);
            return;
        };
        if let Ok(mut guard) = group.lock() {
            *guard = Arc::new(next_group);
        }
        child = next;
        set_state(&state, ServiceState::Running);
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[cfg(unix)]
    fn spec(dir: &std::path::Path, script: &str) -> ServiceSpec {
        ServiceSpec {
            name: "test",
            program: PathBuf::from("/bin/sh"),
            args: vec!["-c".into(), script.into()],
            cwd: dir.to_path_buf(),
            env: vec![("DEEPX_TEST_VALUE".into(), "hello".into())],
            log: dir.join("test.log"),
            stop_signal: "TERM",
            signal_with: None,
        }
    }

    #[cfg(windows)]
    fn spec(dir: &std::path::Path, script: &str) -> ServiceSpec {
        ServiceSpec {
            name: "test",
            program: PathBuf::from("powershell.exe"),
            args: vec![
                "-NoProfile".into(),
                "-NonInteractive".into(),
                "-Command".into(),
                script.into(),
            ],
            cwd: dir.to_path_buf(),
            env: vec![("DEEPX_TEST_VALUE".into(), "hello".into())],
            log: dir.join("test.log"),
            stop_signal: "TERM",
            signal_with: None,
        }
    }

    #[cfg(unix)]
    const ECHO_ENV: &str = "echo $DEEPX_TEST_VALUE";
    #[cfg(windows)]
    const ECHO_ENV: &str = "Write-Output $env:DEEPX_TEST_VALUE";
    #[cfg(unix)]
    const SLEEP: &str = "sleep 30";
    #[cfg(windows)]
    const SLEEP: &str = "Start-Sleep 30";

    #[tokio::test]
    async fn run_to_end_reports_success_failure_and_env() {
        let dir = tempfile::tempdir().unwrap();
        run_to_end(&spec(dir.path(), ECHO_ENV), Duration::from_secs(30))
            .await
            .unwrap();
        let log = std::fs::read_to_string(dir.path().join("test.log")).unwrap();
        assert_eq!(log.trim(), "hello");
        assert!(
            run_to_end(&spec(dir.path(), "exit 3"), Duration::from_secs(30))
                .await
                .is_err()
        );
        assert!(
            run_to_end(&spec(dir.path(), SLEEP), Duration::from_millis(300))
                .await
                .is_err()
        );
    }

    #[tokio::test]
    async fn a_crashing_service_restarts_then_fails() {
        let dir = tempfile::tempdir().unwrap();
        let service = Supervised::start(spec(dir.path(), "exit 1")).unwrap();
        tokio::time::sleep(RESTART_DELAY * 4 + Duration::from_secs(5)).await;
        assert_eq!(service.state(), ServiceState::Failed);
    }

    #[cfg(unix)]
    fn child_script(marker: &std::path::Path) -> String {
        format!("sleep 300 & echo $! > {}; wait", marker.display())
    }

    #[cfg(windows)]
    fn child_script(marker: &std::path::Path) -> String {
        format!(
            "$p = Start-Process -PassThru -WindowStyle Hidden ping -ArgumentList '-n','300','127.0.0.1'; \
             Set-Content -Encoding ascii -Path '{}' -Value $p.Id; Wait-Process -Id $p.Id",
            marker.display()
        )
    }

    #[cfg(unix)]
    fn is_alive(pid: &str) -> bool {
        std::process::Command::new("/bin/kill")
            .args(["-0", pid])
            .status()
            .unwrap()
            .success()
    }

    #[cfg(windows)]
    fn is_alive(pid: &str) -> bool {
        let output = std::process::Command::new("tasklist")
            .args(["/NH", "/FI", &format!("PID eq {pid}")])
            .output()
            .unwrap();
        String::from_utf8_lossy(&output.stdout).contains(pid)
    }

    #[tokio::test]
    async fn stop_ends_the_service_and_its_children() {
        let dir = tempfile::tempdir().unwrap();
        let marker = dir.path().join("child.pid");
        let service = Supervised::start(spec(dir.path(), &child_script(&marker))).unwrap();
        let deadline = Instant::now() + Duration::from_secs(20);
        while !marker.exists() && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
        let child_pid = std::fs::read_to_string(&marker).unwrap().trim().to_string();
        assert!(is_alive(&child_pid), "the child process did not start");
        service.stop(Duration::from_secs(5)).await;
        assert!(!is_alive(&child_pid), "the child process still runs");
    }
}
