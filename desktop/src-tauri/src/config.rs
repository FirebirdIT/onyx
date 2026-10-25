// Config load/save runs before any `AppHandle` (and thus `log_backend_error`)
// exists, so failures here go straight to stderr instead.
#![allow(clippy::print_stderr)]

use directories::ProjectDirs;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::sync::{Mutex, RwLock};
use url::Url;

/// The Docker engine serves the app on this address.
pub const DOCKER_SERVER_URL: &str = "http://localhost:3000";
/// The native engine serves the app on this address.
pub const NATIVE_SERVER_URL: &str = "http://localhost:9999";
/// The local engine address of the default engine mode.
#[cfg(target_os = "macos")]
pub const DEFAULT_SERVER_URL: &str = NATIVE_SERVER_URL;
#[cfg(not(target_os = "macos"))]
pub const DEFAULT_SERVER_URL: &str = DOCKER_SERVER_URL;
pub const DEFAULT_HUB_URL: &str = "https://deepx-hub.vercel.app";
const CONFIG_FILE_NAME: &str = "config.json";

/// How the engine runs. Native runs the engine processes on this computer
/// with no Docker (macOS only for now). Lite and Full use Docker: Lite is
/// Postgres + API + web only, and Full adds connectors and search.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EngineMode {
    #[cfg_attr(target_os = "macos", default)]
    Native,
    #[cfg_attr(not(target_os = "macos"), default)]
    Lite,
    Full,
}

impl EngineMode {
    /// The address of the local engine in this mode.
    pub const fn local_server_url(self) -> &'static str {
        match self {
            Self::Native => NATIVE_SERVER_URL,
            Self::Lite | Self::Full => DOCKER_SERVER_URL,
        }
    }
}

/// True when `url` is the address of a local engine.
pub fn is_local_server_url(url: &str) -> bool {
    url == NATIVE_SERVER_URL || url == DOCKER_SERVER_URL
}

// Independent on/off settings read from config.json, not a state machine.
#[allow(clippy::struct_excessive_bools)]
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    #[serde(default = "default_server_url")]
    pub server_url: String,

    /// When true, the app starts and stops the local Docker engine and opens
    /// `server_url` after the engine is ready. When false, `server_url` is a
    /// remote server and the app only opens it.
    #[serde(default = "default_true")]
    pub local_engine: bool,

    #[serde(default)]
    pub engine_mode: EngineMode,

    #[serde(default = "default_true")]
    pub stop_engine_on_quit: bool,

    /// True after the first-run setup finishes once.
    #[serde(default)]
    pub setup_complete: bool,

    #[serde(default = "default_hub_url")]
    pub hub_url: String,

    /// Public client ID for the Deep X Route sign-in. Empty means the setup
    /// page shows only "paste API key".
    #[serde(default)]
    pub deepx_route_client_id: String,

    #[serde(default = "default_window_title")]
    pub window_title: String,

    #[serde(default = "default_show_menu_bar")]
    pub show_menu_bar: bool,

    #[serde(default)]
    pub hide_window_decorations: bool,

    /// Global shortcut that summons Deep X from any app, in Tauri accelerator
    /// syntax. Explicit `null` in config.json disables it.
    #[serde(default = "default_summon_shortcut")]
    pub summon_shortcut: Option<String>,

    #[serde(default = "default_summon_opens_new_chat")]
    pub summon_opens_new_chat: bool,
}

fn default_window_title() -> String {
    "Deep X".to_string()
}

fn default_server_url() -> String {
    DEFAULT_SERVER_URL.to_string()
}

fn default_hub_url() -> String {
    DEFAULT_HUB_URL.to_string()
}

const fn default_true() -> bool {
    true
}

const fn default_show_menu_bar() -> bool {
    true
}

// Space-with-modifiers chords are the only family major apps leave alone.
// Alt+Space is contested on Windows (system window menu, Copilot, PowerToys
// Run) and Ctrl+Alt+<letter> collides with AltGr typing on European layouts,
// so Ctrl+Alt+Space is the safe non-mac default.
#[allow(clippy::unnecessary_wraps)] // serde default for an `Option` field must return `Option`
fn default_summon_shortcut() -> Option<String> {
    let chord = if cfg!(target_os = "macos") {
        "Super+Shift+Space"
    } else {
        "Ctrl+Alt+Space"
    };
    Some(chord.to_string())
}

const fn default_summon_opens_new_chat() -> bool {
    true
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            server_url: default_server_url(),
            local_engine: true,
            engine_mode: EngineMode::default(),
            stop_engine_on_quit: true,
            setup_complete: false,
            hub_url: default_hub_url(),
            deepx_route_client_id: String::new(),
            window_title: default_window_title(),
            show_menu_bar: true,
            hide_window_decorations: false,
            summon_shortcut: default_summon_shortcut(),
            summon_opens_new_chat: default_summon_opens_new_chat(),
        }
    }
}

fn project_dirs() -> Option<ProjectDirs> {
    ProjectDirs::from("com", "deepx", "deepx-desktop")
}

/// Get the config directory path
pub fn get_config_dir() -> Option<PathBuf> {
    project_dirs().map(|dirs| dirs.config_dir().to_path_buf())
}

/// The folder for engine files, secrets and backup work files.
pub fn get_data_dir() -> Option<PathBuf> {
    project_dirs().map(|dirs| dirs.data_dir().to_path_buf())
}

/// Get the full config file path
pub fn get_config_path() -> Option<PathBuf> {
    get_config_dir().map(|dir| dir.join(CONFIG_FILE_NAME))
}

/// Load config from file, or create default if it doesn't exist
pub fn load_config() -> (AppConfig, bool) {
    let Some(config_path) = get_config_path() else {
        return (AppConfig::default(), false);
    };

    if !config_path.exists() {
        return (AppConfig::default(), false);
    }

    match fs::read_to_string(&config_path) {
        Ok(contents) => match serde_json::from_str(&contents) {
            Ok(config) => (config, true),
            Err(e) => {
                eprintln!(
                    "[DEEPX ERROR] Failed to parse config file {}: {e}",
                    config_path.display()
                );
                (AppConfig::default(), false)
            }
        },
        Err(e) => {
            eprintln!(
                "[DEEPX ERROR] Failed to read config file {}: {e}",
                config_path.display()
            );
            (AppConfig::default(), false)
        }
    }
}

/// Save config to file
pub fn save_config(config: &AppConfig) -> Result<(), String> {
    let config_dir = get_config_dir().ok_or("Could not determine config directory")?;
    let config_path = config_dir.join(CONFIG_FILE_NAME);

    // Ensure config directory exists
    fs::create_dir_all(&config_dir).map_err(|e| format!("Failed to create config dir: {e}"))?;

    let json = serde_json::to_string_pretty(config)
        .map_err(|e| format!("Failed to serialize config: {e}"))?;

    fs::write(&config_path, json).map_err(|e| format!("Failed to write config: {e}"))?;

    Ok(())
}

/// Shared app state: the live config plus a few process-lifetime flags. All
/// fields are behind locks so this can be safely handed out as managed Tauri
/// state and accessed from any thread/command.
pub struct ConfigState {
    config: RwLock<AppConfig>,
    config_initialized: RwLock<bool>,
    app_base_url: RwLock<Option<Url>>,
    pub debug_mode: bool,
    pub debug_log_file: Mutex<Option<fs::File>>,
    /// Serializes update-then-persist-to-disk sequences. Without it, two
    /// concurrent `update_config` + `save_config` callers can interleave so
    /// the last disk write doesn't match the last in-memory update (A and B
    /// both update, B saves, then A's stale snapshot saves last).
    persist_lock: Mutex<()>,
}

impl ConfigState {
    pub const fn new(
        config: AppConfig,
        config_initialized: bool,
        debug_mode: bool,
        debug_log_file: Option<fs::File>,
    ) -> Self {
        Self {
            config: RwLock::new(config),
            config_initialized: RwLock::new(config_initialized),
            app_base_url: RwLock::new(None),
            debug_mode,
            debug_log_file: Mutex::new(debug_log_file),
            persist_lock: Mutex::new(()),
        }
    }

    /// A snapshot of the current config. A panic elsewhere while holding the
    /// write lock poisons it; recovering via `into_inner` means one bad
    /// mutation can't take down every future read.
    pub fn config(&self) -> AppConfig {
        self.config
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }

    /// Apply `f` to the config and return the resulting snapshot.
    pub fn update_config(&self, f: impl FnOnce(&mut AppConfig)) -> AppConfig {
        let mut guard = self
            .config
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        f(&mut guard);
        guard.clone()
    }

    /// Apply `f` and persist the result to disk as one atomic step, so a
    /// concurrent caller can't save its own update in between this update and
    /// this save (which would otherwise leave `config.json` not matching
    /// whichever update actually happened last in memory).
    ///
    /// A failed save rolls the in-memory config back to its prior value, so a
    /// setting can't appear to change for the session and then silently revert
    /// on the next launch.
    pub fn update_and_persist(&self, f: impl FnOnce(&mut AppConfig)) -> Result<AppConfig, String> {
        let _guard = self
            .persist_lock
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let previous = self.config();
        let config = self.update_config(f);
        if let Err(e) = save_config(&config) {
            self.update_config(|current| *current = previous);
            return Err(e);
        }
        Ok(config)
    }

    pub fn is_config_initialized(&self) -> bool {
        *self
            .config_initialized
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub fn set_config_initialized(&self, value: bool) {
        *self
            .config_initialized
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = value;
    }

    pub fn app_base_url(&self) -> Option<Url> {
        self.app_base_url
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }

    pub fn set_app_base_url(&self, url: Option<Url>) {
        *self
            .app_base_url
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = url;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[allow(clippy::unwrap_used)]
    fn parse(json: &str) -> AppConfig {
        serde_json::from_str(json).unwrap()
    }

    #[test]
    fn empty_config_gets_local_engine_defaults() {
        let config = parse("{}");
        assert_eq!(config.server_url, DEFAULT_SERVER_URL);
        assert!(config.local_engine);
        assert_eq!(config.engine_mode, EngineMode::default());
        assert_eq!(config.engine_mode.local_server_url(), DEFAULT_SERVER_URL);
        assert!(!config.setup_complete);
        assert_eq!(config.hub_url, DEFAULT_HUB_URL);
        assert_eq!(config.deepx_route_client_id, "");
    }

    #[test]
    fn engine_mode_uses_lowercase_names() {
        let config = parse(r#"{"engine_mode": "full"}"#);
        assert_eq!(config.engine_mode, EngineMode::Full);
        let config = parse(r#"{"engine_mode": "native"}"#);
        assert_eq!(config.engine_mode, EngineMode::Native);
    }

    #[test]
    fn each_mode_has_its_local_address() {
        assert_eq!(EngineMode::Native.local_server_url(), NATIVE_SERVER_URL);
        assert_eq!(EngineMode::Lite.local_server_url(), DOCKER_SERVER_URL);
        assert!(is_local_server_url(NATIVE_SERVER_URL));
        assert!(is_local_server_url(DOCKER_SERVER_URL));
        assert!(!is_local_server_url("https://deepx.example.com"));
    }

    #[test]
    fn missing_summon_fields_get_defaults() {
        let config = parse(r#"{"server_url": "https://cloud.onyx.app"}"#);
        assert_eq!(config.summon_shortcut, default_summon_shortcut());
        assert!(config.summon_shortcut.is_some());
        assert!(config.summon_opens_new_chat);
    }

    #[test]
    fn explicit_null_disables_summon_shortcut() {
        let config = parse(r#"{"server_url": "https://cloud.onyx.app", "summon_shortcut": null}"#);
        assert_eq!(config.summon_shortcut, None);
    }

    #[test]
    fn custom_summon_settings_are_preserved() {
        let config = parse(
            r#"{"server_url": "https://cloud.onyx.app", "summon_shortcut": "F19", "summon_opens_new_chat": false}"#,
        );
        assert_eq!(config.summon_shortcut.as_deref(), Some("F19"));
        assert!(!config.summon_opens_new_chat);
    }
}
