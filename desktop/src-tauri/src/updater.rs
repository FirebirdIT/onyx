//! App updates from the hub feed `{hub_url}/api/desktop/latest.json`.

use crate::config::ConfigState;
use crate::debug_log::log_backend_error;
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;
use tokio::sync::oneshot;

pub const MENU_CHECK_UPDATES_ID: &str = "check_for_updates";
const DIALOG_TITLE: &str = "Deep X Update";

pub fn feed_url(hub_url: &str) -> Result<url::Url, String> {
    url::Url::parse(&format!(
        "{}/api/desktop/latest.json",
        hub_url.trim_end_matches('/')
    ))
    .map_err(|e| format!("The hub address is not valid: {e}"))
}

fn show_message(app: &AppHandle, text: &str, kind: MessageDialogKind) {
    app.dialog()
        .message(text)
        .title(DIALOG_TITLE)
        .kind(kind)
        .show(|_| {});
}

async fn ask(app: &AppHandle, text: String) -> bool {
    let (tx, rx) = oneshot::channel();
    app.dialog()
        .message(text)
        .title(DIALOG_TITLE)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Install and Restart".to_string(),
            "Later".to_string(),
        ))
        .show(move |answer| {
            let _ = tx.send(answer);
        });
    rx.await.unwrap_or(false)
}

async fn check(app: &AppHandle, interactive: bool) -> Result<(), String> {
    let hub_url = app.state::<ConfigState>().config().hub_url;
    let update = app
        .updater_builder()
        .endpoints(vec![feed_url(&hub_url)?])
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| e.to_string())?;

    let Some(update) = update else {
        if interactive {
            show_message(
                app,
                "You have the newest version of Deep X.",
                MessageDialogKind::Info,
            );
        }
        return Ok(());
    };

    let question = format!(
        "Deep X {} is available. You have version {}.\n\nInstall it now? The app restarts after the installation.",
        update.version, update.current_version
    );
    if !ask(app, question).await {
        return Ok(());
    }
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    app.restart();
}

/// Check for an update. `interactive` is true for the menu item: then the
/// user also sees "no update" and errors.
pub fn check_for_updates(app: &AppHandle, interactive: bool) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = check(&handle, interactive).await {
            log_backend_error(&handle, &format!("Update check failed: {e}"));
            if interactive {
                show_message(
                    &handle,
                    "Deep X could not check for updates. Try again later.",
                    MessageDialogKind::Warning,
                );
            }
        }
    });
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn feed_url_appends_path() {
        assert_eq!(
            feed_url("https://deepx-hub.vercel.app/").unwrap().as_str(),
            "https://deepx-hub.vercel.app/api/desktop/latest.json"
        );
    }
}
