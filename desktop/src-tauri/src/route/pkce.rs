//! PKCE helpers for the Deep X Route native sign-in (RFC 7636, S256).
//! The flow is the same as `console/public/integrations/connected-app.mjs`
//! in the Deep X Route repo.

use crate::secure_random::random_bytes;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use url::Url;

pub const CALLBACK_PORT: u16 = 8787;
pub const CALLBACK_PATH: &str = "/callback";

/// 32 random bytes as base64url: 43 characters.
pub fn random_token() -> String {
    URL_SAFE_NO_PAD.encode(random_bytes::<32>())
}

/// `base64url(sha256(verifier))`.
pub fn code_challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

pub fn redirect_uri(port: u16) -> String {
    format!("http://127.0.0.1:{port}{CALLBACK_PATH}")
}

/// A token of exactly 43 base64url characters (32 bytes).
pub fn is_token(value: &str) -> bool {
    value.len() == 43
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// A UUID in the 8-4-4-4-12 hex form.
pub fn is_client_id(value: &str) -> bool {
    let groups: Vec<&str> = value.split('-').collect();
    groups.len() == 5
        && groups
            .iter()
            .zip([8, 4, 4, 4, 12])
            .all(|(group, len)| group.len() == len && group.bytes().all(|b| b.is_ascii_hexdigit()))
}

pub fn authorization_url(
    console_base: &str,
    client_id: &str,
    redirect_uri: &str,
    challenge: &str,
    state: &str,
) -> Result<String, String> {
    let mut url = Url::parse(console_base)
        .and_then(|base| base.join("/authorize"))
        .map_err(|e| format!("Console address is not valid: {e}"))?;
    url.query_pairs_mut()
        .append_pair("client_id", client_id)
        .append_pair("redirect_uri", redirect_uri)
        .append_pair("code_challenge", challenge)
        .append_pair("code_challenge_method", "S256")
        .append_pair("state", state);
    Ok(url.into())
}

/// What the browser sent to the callback address.
#[derive(Debug, PartialEq, Eq)]
pub enum Callback {
    Code(String),
    Denied,
}

/// Why the app refused a callback request. The text goes to the browser.
#[derive(Debug, PartialEq, Eq)]
pub enum CallbackError {
    Request,
    State,
    Code,
}

impl CallbackError {
    pub const fn message(&self) -> &'static str {
        match self {
            Self::Request => "Invalid callback.",
            Self::State => "Invalid callback state. Return to the app.",
            Self::Code => "Invalid authorization code.",
        }
    }
}

/// A query parameter that can be missing, present once, or repeated.
enum Param {
    Missing,
    One(String),
    Repeated,
}

fn single_param(url: &Url, name: &str) -> Param {
    let mut values = url
        .query_pairs()
        .filter(|(key, _)| key == name)
        .map(|(_, value)| value.into_owned());
    match (values.next(), values.next()) {
        (None, _) => Param::Missing,
        (Some(value), None) => Param::One(value),
        (Some(_), Some(_)) => Param::Repeated,
    }
}

/// Check one callback request: method, `Host`, path, `state` (in constant
/// time) and `code`.
pub fn parse_callback(
    method: &str,
    target: &str,
    host: Option<&str>,
    port: u16,
    expected_state: &str,
) -> Result<Callback, CallbackError> {
    let expected_host = format!("127.0.0.1:{port}");
    if method != "GET" || host != Some(expected_host.as_str()) {
        return Err(CallbackError::Request);
    }
    let url = Url::parse(&format!("http://{expected_host}"))
        .and_then(|base| base.join(target))
        .map_err(|_| CallbackError::Request)?;
    if url.path() != CALLBACK_PATH {
        return Err(CallbackError::State);
    }
    let Param::One(supplied) = single_param(&url, "state") else {
        return Err(CallbackError::State);
    };
    if !is_token(&supplied)
        || supplied.len() != expected_state.len()
        || !bool::from(supplied.as_bytes().ct_eq(expected_state.as_bytes()))
    {
        return Err(CallbackError::State);
    }

    match (single_param(&url, "code"), single_param(&url, "error")) {
        (Param::Missing, Param::One(error)) if error == "access_denied" => Ok(Callback::Denied),
        (Param::One(code), Param::Missing) if is_token(&code) => Ok(Callback::Code(code)),
        _ => Err(CallbackError::Code),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    const STATE: &str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ";
    const CODE: &str = "CODEfghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ_";
    const HOST: Option<&str> = Some("127.0.0.1:8787");

    #[test]
    fn challenge_matches_rfc7636_example() {
        // RFC 7636 appendix B.
        let verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
        assert_eq!(
            code_challenge(verifier),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
    }

    #[test]
    fn random_tokens_are_43_chars_and_differ() {
        let token = random_token();
        assert!(is_token(&token));
        assert_ne!(token, random_token());
    }

    #[test]
    fn client_id_must_be_a_uuid() {
        assert!(is_client_id("123e4567-e89b-12d3-a456-426614174000"));
        assert!(!is_client_id(""));
        assert!(!is_client_id("123e4567e89b12d3a456426614174000"));
        assert!(!is_client_id("123e4567-e89b-12d3-a456-42661417400z"));
    }

    #[test]
    fn authorization_url_has_pkce_params() {
        let url = authorization_url(
            "https://console.deepxroute.com",
            "123e4567-e89b-12d3-a456-426614174000",
            &redirect_uri(8787),
            "challenge",
            STATE,
        )
        .unwrap();
        let parsed = Url::parse(&url).unwrap();
        assert_eq!(parsed.path(), "/authorize");
        let pairs: std::collections::HashMap<_, _> = parsed.query_pairs().into_owned().collect();
        assert_eq!(pairs["redirect_uri"], "http://127.0.0.1:8787/callback");
        assert_eq!(pairs["code_challenge_method"], "S256");
        assert_eq!(pairs["state"], STATE);
    }

    #[test]
    fn callback_accepts_matching_state_and_code() {
        let target = format!("/callback?code={CODE}&state={STATE}");
        assert_eq!(
            parse_callback("GET", &target, HOST, 8787, STATE),
            Ok(Callback::Code(CODE.to_string()))
        );
    }

    #[test]
    fn callback_reports_denied() {
        let target = format!("/callback?error=access_denied&state={STATE}");
        assert_eq!(
            parse_callback("GET", &target, HOST, 8787, STATE),
            Ok(Callback::Denied)
        );
    }

    #[test]
    fn callback_rejects_bad_requests() {
        let other_state = "Xbcdefghijklmnopqrstuvwxyz0123456789ABCDEFG";
        let cases = [
            (
                "POST",
                format!("/callback?code={CODE}&state={STATE}"),
                HOST,
                CallbackError::Request,
            ),
            (
                "GET",
                format!("/callback?code={CODE}&state={STATE}"),
                Some("localhost:8787"),
                CallbackError::Request,
            ),
            (
                "GET",
                format!("/other?code={CODE}&state={STATE}"),
                HOST,
                CallbackError::State,
            ),
            (
                "GET",
                format!("/callback?code={CODE}&state={other_state}"),
                HOST,
                CallbackError::State,
            ),
            (
                "GET",
                format!("/callback?code={CODE}&state={STATE}&state={STATE}"),
                HOST,
                CallbackError::State,
            ),
            (
                "GET",
                format!("/callback?code=short&state={STATE}"),
                HOST,
                CallbackError::Code,
            ),
            (
                "GET",
                format!("/callback?code={CODE}&code={CODE}&state={STATE}"),
                HOST,
                CallbackError::Code,
            ),
            (
                "GET",
                format!("/callback?code={CODE}&error=x&state={STATE}"),
                HOST,
                CallbackError::Code,
            ),
            (
                "GET",
                format!("/callback?state={STATE}"),
                HOST,
                CallbackError::Code,
            ),
        ];
        for (method, target, host, expected) in cases {
            assert_eq!(
                parse_callback(method, &target, host, 8787, STATE),
                Err(expected),
                "{method} {target}"
            );
        }
    }
}
