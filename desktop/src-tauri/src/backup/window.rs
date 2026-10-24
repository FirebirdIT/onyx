//! The small window for "Back up now..." and "Restore from backup...".

use crate::debug_log::log_backend_error;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};

const WINDOW_LABEL: &str = "backup";

#[derive(Clone, Copy)]
pub enum BackupAction {
    Backup,
    Restore,
}

impl BackupAction {
    const fn page(self) -> &'static str {
        match self {
            Self::Backup => "backup.html?mode=backup",
            Self::Restore => "backup.html?mode=restore",
        }
    }

    const fn title(self) -> &'static str {
        match self {
            Self::Backup => "Back Up Deep X",
            Self::Restore => "Restore Deep X",
        }
    }
}

pub fn open_backup_window(app: &AppHandle, action: BackupAction) {
    // One backup window at a time: a second click shows the open window.
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        if let Err(e) = window.set_focus() {
            log_backend_error(app, &format!("Failed to focus backup window: {e}"));
        }
        return;
    }
    let result =
        WebviewWindowBuilder::new(app, WINDOW_LABEL, WebviewUrl::App(action.page().into()))
            .title(action.title())
            .inner_size(520.0, 560.0)
            .resizable(false)
            .build();
    if let Err(e) = result {
        log_backend_error(app, &format!("Failed to open backup window: {e}"));
    }
}
