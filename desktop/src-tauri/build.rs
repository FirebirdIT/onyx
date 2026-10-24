// The app commands are listed here so that each one needs a permission in a
// capability. Without this list, every page that a capability covers (also
// the remote server page) could call every command.
const APP_COMMANDS: &[&str] = &[
    "get_server_url",
    "get_bootstrap_state",
    "set_server_url",
    "get_config_path_cmd",
    "check_server_reachable",
    "open_in_browser",
    "open_config_file",
    "open_config_directory",
    "navigate_to",
    "reload_page",
    "go_back",
    "go_forward",
    "new_window",
    "reset_config",
    "start_drag_window",
    "toggle_menu_bar",
    "log_from_frontend",
    "engine_check_docker",
    "engine_open_docker",
    "engine_status",
    "engine_start",
    "engine_stop",
    "engine_restart",
    "engine_set_mode",
    "engine_set_stop_on_quit",
    "use_local_engine",
    "complete_setup",
    "route_key_status",
    "route_save_key",
    "route_forget_key",
    "route_sign_in",
    "route_sign_in_cancel",
    "backup_now",
    "backup_restore",
];

// A build script reports failure by panicking; `tauri_build::build` does the same.
#[allow(clippy::panic)]
fn main() {
    let attributes = tauri_build::Attributes::new()
        .app_manifest(tauri_build::AppManifest::new().commands(APP_COMMANDS));
    if let Err(error) = tauri_build::try_build(attributes) {
        panic!("tauri build script failed: {error:#}");
    }
}
