//! Calls to the Deep X Route API. No call here costs money. Error texts
//! never hold the key.

use serde::{Deserialize, Serialize};
use std::time::Duration;

pub const API_BASE: &str = "https://api.deepxroute.com";
pub const CONSOLE_BASE: &str = "https://console.deepxroute.com";
const TIMEOUT: Duration = Duration::from_secs(15);

fn client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(TIMEOUT)
        // Never follow a redirect with a key or a code in the request.
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {e}"))
}

/// A key that the user pasted: `sk-` and then base64url characters.
pub fn is_plausible_key(key: &str) -> bool {
    key.starts_with("sk-")
        && (20..=200).contains(&key.len())
        && key
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// A key from the sign-in exchange: `sk-dxr-` and 43 base64url characters.
pub fn is_issued_key(key: &str) -> bool {
    key.strip_prefix("sk-dxr-")
        .is_some_and(super::pkce::is_token)
}

/// `GET /v1/key`. Ok when Deep X Route accepts the key.
pub async fn validate_key(api_base: &str, key: &str) -> Result<(), String> {
    let url = format!("{}/v1/key", api_base.trim_end_matches('/'));
    let response = client()?
        .get(url)
        .bearer_auth(key)
        .send()
        .await
        .map_err(|_| "Deep X Route is not reachable. Check the internet connection.".to_string())?;
    match response.status().as_u16() {
        200 => Ok(()),
        401 | 403 => Err("Deep X Route does not accept this API key.".to_string()),
        status => Err(format!(
            "Deep X Route returned HTTP {status}. Try again later."
        )),
    }
}

#[derive(Serialize)]
struct ExchangeRequest<'a> {
    client_id: &'a str,
    redirect_uri: &'a str,
    code: &'a str,
    code_verifier: &'a str,
}

#[derive(Deserialize)]
struct ExchangeResponse {
    key: String,
}

/// `POST /v1/auth/keys`: exchange the authorization code for an API key.
pub async fn exchange_code(
    api_base: &str,
    client_id: &str,
    redirect_uri: &str,
    code: &str,
    code_verifier: &str,
) -> Result<String, String> {
    let url = format!("{}/v1/auth/keys", api_base.trim_end_matches('/'));
    let body = serde_json::to_string(&ExchangeRequest {
        client_id,
        redirect_uri,
        code,
        code_verifier,
    })
    .map_err(|e| format!("Failed to encode the exchange: {e}"))?;
    let response = client()?
        .post(url)
        .header("Content-Type", "application/json")
        .body(body)
        .send()
        .await
        .map_err(|_| "Exchange failed.".to_string())?;
    if !response.status().is_success() {
        return Err("Exchange failed.".to_string());
    }
    let text = response
        .text()
        .await
        .map_err(|_| "Exchange failed.".to_string())?;
    let parsed: ExchangeResponse =
        serde_json::from_str(&text).map_err(|_| "Invalid key response.".to_string())?;
    if is_issued_key(&parsed.key) {
        Ok(parsed.key)
    } else {
        Err("Invalid key response.".to_string())
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use crate::test_http::{serve, Reply};

    const KEY: &str = "sk-dxr-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ";

    #[test]
    fn key_formats() {
        assert!(is_issued_key(KEY));
        assert!(!is_issued_key("sk-dxr-short"));
        assert!(is_plausible_key(KEY));
        assert!(is_plausible_key("sk-or-v1-0123456789abcdef"));
        assert!(!is_plausible_key("sk-has space 0123456789"));
        assert!(!is_plausible_key("pk-0123456789abcdefghij"));
    }

    #[tokio::test]
    async fn validate_key_sends_bearer_and_maps_status() {
        let (base, requests) = serve(|request| {
            if request.header("authorization") == Some(&format!("Bearer {KEY}")) {
                Reply::json(200, "{}")
            } else {
                Reply::json(401, "{}")
            }
        });
        validate_key(&base, KEY).await.unwrap();
        let err = validate_key(&base, "sk-wrong-0123456789abc")
            .await
            .unwrap_err();
        assert!(!err.contains("sk-wrong"));
        let seen = requests.lock().unwrap().clone();
        assert_eq!(seen[0].method, "GET");
        assert_eq!(seen[0].path, "/v1/key");
    }

    #[tokio::test]
    async fn exchange_posts_pkce_fields_and_checks_key() {
        let (base, requests) =
            serve(|_| Reply::json(200, &format!("{{\"id\":\"k1\",\"key\":\"{KEY}\"}}")));
        let key = exchange_code(
            &base,
            "cid",
            "http://127.0.0.1:8787/callback",
            "code1",
            "verifier1",
        )
        .await
        .unwrap();
        assert_eq!(key, KEY);
        let seen = requests.lock().unwrap().clone();
        assert_eq!(seen[0].method, "POST");
        assert_eq!(seen[0].path, "/v1/auth/keys");
        let body: serde_json::Value = serde_json::from_slice(&seen[0].body).unwrap();
        assert_eq!(body["code_verifier"], "verifier1");
        assert_eq!(body["redirect_uri"], "http://127.0.0.1:8787/callback");
    }

    #[tokio::test]
    async fn exchange_rejects_bad_key_shape() {
        let base = crate::test_http::serve_once(Reply::json(200, "{\"key\":\"nope\"}"));
        assert!(exchange_code(&base, "c", "r", "x", "v").await.is_err());
    }
}
