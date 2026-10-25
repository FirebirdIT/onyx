//! The native engine bundle: download, check, unpack and find its programs.
//!
//! The hub serves `GET {hub}/api/engine/native`:
//! `{version, platforms: {"darwin-aarch64": {url, sha256, size}}}`.
//! `DEEPX_ENGINE_BUNDLE` can name a local `.tar.gz` bundle instead (for tests).

use crate::engine::emit_progress;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::Duration;
use tauri::AppHandle;
use tokio::io::AsyncWriteExt;

const HUB_TIMEOUT: Duration = Duration::from_secs(8);
const CURRENT_FILE: &str = "current.json";
const LOCAL_BUNDLE_ENV: &str = "DEEPX_ENGINE_BUNDLE";

/// The platform name of this computer in bundle file names.
pub const fn current_platform() -> Option<&'static str> {
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        Some("darwin-aarch64")
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        Some("darwin-x86_64")
    } else {
        None
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Manifest {
    pub version: String,
    pub platform: String,
    pub web_server: String,
}

/// An unpacked bundle.
#[derive(Debug, Clone)]
pub struct Bundle {
    pub root: PathBuf,
    pub manifest: Manifest,
}

impl Bundle {
    pub fn load(root: &Path) -> Result<Self, String> {
        let text = fs::read_to_string(root.join("manifest.json"))
            .map_err(|e| format!("The engine bundle has no manifest: {e}"))?;
        let manifest: Manifest = serde_json::from_str(&text)
            .map_err(|e| format!("The engine bundle manifest is not valid: {e}"))?;
        if Some(manifest.platform.as_str()) != current_platform() {
            return Err(format!(
                "The engine bundle is for {}, not for this computer",
                manifest.platform
            ));
        }
        Ok(Self {
            root: root.to_path_buf(),
            manifest,
        })
    }

    pub fn python(&self) -> PathBuf {
        self.root.join("python/bin/python3")
    }
    pub fn job_python(&self) -> PathBuf {
        self.root.join("jobpython/bin/python3")
    }
    pub fn node(&self) -> PathBuf {
        self.root.join("node/bin/node")
    }
    pub fn node_bin_dir(&self) -> PathBuf {
        self.root.join("node/bin")
    }
    pub fn postgres_bin(&self, program: &str) -> PathBuf {
        self.root.join("postgres/bin").join(program)
    }
    pub fn srt_cli(&self) -> PathBuf {
        self.root
            .join("srt/node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js")
    }
    pub fn backend_dir(&self) -> PathBuf {
        self.root.join("backend")
    }
    pub fn web_server(&self) -> PathBuf {
        self.root.join(self.manifest.web_server.replace("/./", "/"))
    }
}

/// A bundle release on the hub, for this platform.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Release {
    pub version: String,
    pub url: String,
    pub sha256: String,
}

#[derive(Deserialize)]
struct HubPlatform {
    url: String,
    sha256: String,
}

#[derive(Deserialize)]
struct HubNativeResponse {
    version: String,
    platforms: HashMap<String, HubPlatform>,
}

/// Names that are safe as one folder name.
pub fn is_valid_version(version: &str) -> bool {
    !version.is_empty()
        && version.len() <= 64
        && !version.starts_with('.')
        && version
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "._-+".contains(c))
}

fn is_sha256_hex(value: &str) -> bool {
    value.len() == 64 && value.chars().all(|c| c.is_ascii_hexdigit())
}

/// Read the hub answer and pick the release for `platform`.
pub fn parse_release(body: &str, platform: &str) -> Result<Release, String> {
    let parsed: HubNativeResponse =
        serde_json::from_str(body).map_err(|e| format!("Hub engine answer is not valid: {e}"))?;
    let entry = parsed
        .platforms
        .get(platform)
        .ok_or_else(|| format!("The hub has no native engine for {platform}"))?;
    if !is_valid_version(&parsed.version)
        || !entry.url.starts_with("https://")
        || !is_sha256_hex(&entry.sha256)
    {
        return Err("The hub native engine answer has a value that is not valid".to_string());
    }
    Ok(Release {
        version: parsed.version,
        url: entry.url.clone(),
        sha256: entry.sha256.to_ascii_lowercase(),
    })
}

async fn fetch_release(hub_url: &str, platform: &str) -> Result<Release, String> {
    let url = format!("{}/api/engine/native", hub_url.trim_end_matches('/'));
    let client = reqwest::Client::builder()
        .timeout(HUB_TIMEOUT)
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {e}"))?;
    let response = client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("Hub is not reachable: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("Hub returned HTTP {}", response.status().as_u16()));
    }
    let body = response
        .text()
        .await
        .map_err(|e| format!("Failed to read hub answer: {e}"))?;
    parse_release(&body, platform)
}

fn read_current(bundles_dir: &Path) -> Option<String> {
    let text = fs::read_to_string(bundles_dir.join(CURRENT_FILE)).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    value["version"]
        .as_str()
        .filter(|v| is_valid_version(v))
        .map(str::to_string)
}

fn write_current(bundles_dir: &Path, version: &str) -> Result<(), String> {
    let text = serde_json::json!({ "version": version }).to_string();
    crate::fsutil::write_private(&bundles_dir.join(CURRENT_FILE), text.as_bytes())
}

pub fn sha256_file(path: &Path) -> Result<String, String> {
    let mut file =
        fs::File::open(path).map_err(|e| format!("Failed to open {}: {e}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1 << 20];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|e| format!("Failed to read {}: {e}", path.display()))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hex::encode(hasher.finalize()))
}

async fn download(app: &AppHandle, release: &Release, target: &Path) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {e}"))?;
    let mut response = client
        .get(&release.url)
        .send()
        .await
        .map_err(|e| format!("The engine download failed: {e}"))?;
    if !response.status().is_success() {
        return Err(format!(
            "The engine download failed: HTTP {}",
            response.status().as_u16()
        ));
    }
    let total = response.content_length().unwrap_or(0);
    let mut file = tokio::fs::File::create(target)
        .await
        .map_err(|e| format!("Failed to create {}: {e}", target.display()))?;
    let mut received: u64 = 0;
    let mut last_step = u64::MAX;
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|e| format!("The engine download stopped: {e}"))?
    {
        file.write_all(&chunk)
            .await
            .map_err(|e| format!("Failed to write the download: {e}"))?;
        received += chunk.len() as u64;
        let step = received / (50 << 20);
        if step != last_step {
            last_step = step;
            let line = if total > 0 {
                format!(
                    "Downloading the engine: {} of {} MB",
                    received >> 20,
                    total >> 20
                )
            } else {
                format!("Downloading the engine: {} MB", received >> 20)
            };
            emit_progress(app, "download", &line);
        }
    }
    file.flush()
        .await
        .map_err(|e| format!("Failed to write the download: {e}"))
}

/// Unpack `archive` as version `version`, make it current, and remove older
/// bundles.
async fn install(bundles_dir: &Path, archive: &Path, version: &str) -> Result<Bundle, String> {
    let partial = bundles_dir.join(format!("{version}.partial"));
    let target = bundles_dir.join(version);
    let _ = fs::remove_dir_all(&partial);
    fs::create_dir_all(&partial)
        .map_err(|e| format!("Failed to create {}: {e}", partial.display()))?;
    let status = tokio::process::Command::new("/usr/bin/tar")
        .arg("-xzf")
        .arg(archive)
        .arg("-C")
        .arg(&partial)
        .status()
        .await
        .map_err(|e| format!("Failed to unpack the engine: {e}"))?;
    if !status.success() {
        let _ = fs::remove_dir_all(&partial);
        return Err(format!(
            "Failed to unpack the engine: tar ended with {status}"
        ));
    }
    let bundle = Bundle::load(&partial)?;
    if bundle.manifest.version != version {
        let _ = fs::remove_dir_all(&partial);
        return Err("The engine bundle has a different version than the hub said".to_string());
    }
    let _ = fs::remove_dir_all(&target);
    fs::rename(&partial, &target).map_err(|e| format!("Failed to install the engine: {e}"))?;
    write_current(bundles_dir, version)?;
    remove_other_versions(bundles_dir, version);
    Bundle::load(&target)
}

fn remove_other_versions(bundles_dir: &Path, keep: &str) {
    let Ok(entries) = fs::read_dir(bundles_dir) else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if entry.path().is_dir() && name != keep {
            // A folder that stays only uses disk space.
            let _ = fs::remove_dir_all(entry.path());
        }
    }
}

fn installed(bundles_dir: &Path) -> Option<Bundle> {
    let version = read_current(bundles_dir)?;
    Bundle::load(&bundles_dir.join(version)).ok()
}

async fn install_local_archive(bundles_dir: &Path, archive: &Path) -> Result<Bundle, String> {
    // The version is in the archive's manifest, so unpack to a scratch folder.
    let scratch = bundles_dir.join("local.partial");
    let _ = fs::remove_dir_all(&scratch);
    fs::create_dir_all(&scratch).map_err(|e| format!("Failed to create a folder: {e}"))?;
    let status = tokio::process::Command::new("/usr/bin/tar")
        .args(["-xzf"])
        .arg(archive)
        .args(["-C"])
        .arg(&scratch)
        .arg("./manifest.json")
        .status()
        .await
        .map_err(|e| format!("Failed to read the engine bundle: {e}"))?;
    if !status.success() {
        return Err("The local engine bundle has no manifest".to_string());
    }
    let version = Bundle::load(&scratch)?.manifest.version;
    let _ = fs::remove_dir_all(&scratch);
    if !is_valid_version(&version) {
        return Err("The local engine bundle version is not valid".to_string());
    }
    if read_current(bundles_dir).as_deref() == Some(version.as_str()) {
        if let Some(bundle) = installed(bundles_dir) {
            return Ok(bundle);
        }
    }
    install(bundles_dir, archive, &version).await
}

/// The bundle to run: the newest one from the hub, downloaded when it is new.
/// When the hub cannot be reached, the installed bundle is used.
pub async fn ensure_bundle(
    app: &AppHandle,
    hub_url: &str,
    bundles_dir: &Path,
) -> Result<Bundle, String> {
    fs::create_dir_all(bundles_dir)
        .map_err(|e| format!("Failed to create {}: {e}", bundles_dir.display()))?;
    if let Some(local) = std::env::var_os(LOCAL_BUNDLE_ENV) {
        emit_progress(app, "prepare", "Using the local engine bundle");
        return install_local_archive(bundles_dir, Path::new(&local)).await;
    }
    let platform =
        current_platform().ok_or("The Deep X engine does not support this computer yet")?;
    let release = match fetch_release(hub_url, platform).await {
        Ok(release) => release,
        Err(error) => {
            return installed(bundles_dir).ok_or(error);
        }
    };
    if read_current(bundles_dir).as_deref() == Some(release.version.as_str()) {
        if let Some(bundle) = installed(bundles_dir) {
            return Ok(bundle);
        }
    }
    emit_progress(
        app,
        "download",
        &format!("Downloading engine {}", release.version),
    );
    let archive = bundles_dir.join("download.tar.gz");
    download(app, &release, &archive).await?;
    emit_progress(app, "download", "Checking the download");
    let actual = sha256_file(&archive)?;
    if actual != release.sha256 {
        let _ = fs::remove_file(&archive);
        return Err("The engine download is damaged (checksum mismatch). Try again.".to_string());
    }
    emit_progress(app, "download", "Unpacking the engine");
    let bundle = install(bundles_dir, &archive, &release.version).await;
    let _ = fs::remove_file(&archive);
    bundle
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    #[test]
    fn picks_the_release_for_the_platform() {
        let body = format!(
            r#"{{"version":"0.2.0","platforms":{{"darwin-aarch64":{{"url":"https://example.com/a.tar.gz","sha256":"{SHA}","size":1}}}}}}"#
        );
        let release = parse_release(&body, "darwin-aarch64").unwrap();
        assert_eq!(release.version, "0.2.0");
        assert_eq!(release.sha256, SHA);
        assert!(parse_release(&body, "darwin-x86_64").is_err());
    }

    #[test]
    fn refuses_unsafe_values() {
        for (version, url, sha) in [
            ("../x", "https://example.com/a", SHA),
            ("0.2.0", "http://example.com/a", SHA),
            ("0.2.0", "https://example.com/a", "abc"),
        ] {
            let body = format!(
                r#"{{"version":"{version}","platforms":{{"darwin-aarch64":{{"url":"{url}","sha256":"{sha}"}}}}}}"#
            );
            assert!(
                parse_release(&body, "darwin-aarch64").is_err(),
                "{version} {url}"
            );
        }
    }

    #[test]
    fn version_names_are_single_folder_names() {
        assert!(is_valid_version("0.2.0-dev"));
        assert!(!is_valid_version(".."));
        assert!(!is_valid_version("a/b"));
        assert!(!is_valid_version(""));
    }

    #[test]
    fn current_version_round_trips() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(read_current(dir.path()), None);
        write_current(dir.path(), "0.2.0").unwrap();
        assert_eq!(read_current(dir.path()).as_deref(), Some("0.2.0"));
    }

    #[test]
    fn sha256_of_a_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("f");
        fs::write(&path, b"abc").unwrap();
        assert_eq!(
            sha256_file(&path).unwrap(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
