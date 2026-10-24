//! Deep X Route connection: sign-in (PKCE), pasted API keys, key storage.
// Tauri command handlers take IPC arguments and extractors by value.
#![allow(clippy::needless_pass_by_value)]

pub mod api;
pub mod keystore;
pub mod loopback;
pub mod pkce;

use crate::config::ConfigState;
use crate::window::open_in_default_browser;
use loopback::{serve_callback, SignInStatus, SIGN_IN_TIMEOUT};
use serde::Serialize;
use std::sync::Mutex;
use tauri::{AppHandle, Manager, State};
use tokio::sync::oneshot;

/// The cancel handle of the sign-in that runs now, if any.
#[derive(Default)]
pub struct SignInState(Mutex<Option<oneshot::Sender<()>>>);

impl SignInState {
    fn replace(&self, sender: Option<oneshot::Sender<()>>) -> Option<oneshot::Sender<()>> {
        let mut guard = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        std::mem::replace(&mut *guard, sender)
    }
}

#[derive(Serialize)]
pub struct KeyStatus {
    saved: bool,
    sign_in_available: bool,
}

#[tauri::command]
pub fn route_key_status(state: State<ConfigState>) -> KeyStatus {
    KeyStatus {
        saved: keystore::load_key().is_some(),
        sign_in_available: pkce::is_client_id(state.config().deepx_route_client_id.trim()),
    }
}

/// Check a pasted key with Deep X Route, then save it.
#[tauri::command]
pub async fn route_save_key(key: String) -> Result<keystore::KeyLocation, String> {
    let key = key.trim();
    if !api::is_plausible_key(key) {
        return Err("This is not a Deep X Route API key. A key starts with \"sk-\".".to_string());
    }
    api::validate_key(api::API_BASE, key).await?;
    keystore::save_key(key)
}

#[tauri::command]
pub fn route_forget_key() {
    keystore::delete_key();
}

#[tauri::command]
pub fn route_sign_in_cancel(sign_in: State<SignInState>) {
    if let Some(sender) = sign_in.replace(None) {
        // The sign-in can have finished already.
        let _ = sender.send(());
    }
}

async fn bind_callback(port: u16) -> Result<tokio::net::TcpListener, String> {
    for _ in 0..10 {
        if let Ok(listener) = tokio::net::TcpListener::bind(("127.0.0.1", port)).await {
            return Ok(listener);
        }
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    }
    Err(format!(
        "Port {port} is in use. Close the program that uses it and try again."
    ))
}

/// Native PKCE sign-in with a loopback callback on 127.0.0.1:8787.
#[tauri::command]
pub async fn route_sign_in(app: AppHandle) -> Result<SignInStatus, String> {
    let client_id = app
        .state::<ConfigState>()
        .config()
        .deepx_route_client_id
        .trim()
        .to_string();
    if !pkce::is_client_id(&client_id) {
        return Err("Sign-in is not configured. Paste an API key.".to_string());
    }

    // A new sign-in ends the one before it, which frees the port.
    if let Some(previous) = app.state::<SignInState>().replace(None) {
        let _ = previous.send(());
    }
    let port = pkce::CALLBACK_PORT;
    let listener = bind_callback(port).await?;
    let redirect = pkce::redirect_uri(port);
    let verifier = pkce::random_token();
    let state = pkce::random_token();
    let url = pkce::authorization_url(
        api::CONSOLE_BASE,
        &client_id,
        &redirect,
        &pkce::code_challenge(&verifier),
        &state,
    )?;

    let (cancel_tx, cancel_rx) = oneshot::channel();
    app.state::<SignInState>().replace(Some(cancel_tx));
    if !open_in_default_browser(&url) {
        app.state::<SignInState>().replace(None);
        return Err("Could not open the browser for sign-in.".to_string());
    }

    let status = serve_callback(listener, port, &state, SIGN_IN_TIMEOUT, cancel_rx, |code| {
        let client_id = client_id.clone();
        let redirect = redirect.clone();
        async move {
            let key =
                api::exchange_code(api::API_BASE, &client_id, &redirect, &code, &verifier).await?;
            api::validate_key(api::API_BASE, &key).await?;
            keystore::save_key(&key).map(|_| ())
        }
    })
    .await;
    app.state::<SignInState>().replace(None);
    Ok(status)
}
