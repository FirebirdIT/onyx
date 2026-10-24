//! The loopback callback server of the native sign-in. It accepts one code,
//! exchanges it once for an API key, and then closes.

use super::pkce::{parse_callback, Callback, CallbackError};
use serde::Serialize;
use std::future::Future;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::oneshot;

pub const SIGN_IN_TIMEOUT: Duration = Duration::from_mins(12);
const MAX_REQUEST_BYTES: usize = 8 * 1024;
const READ_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum SignInStatus {
    Connected,
    Cancelled,
    Expired,
    /// The exchange failed after the code was used. A key can exist on the
    /// Deep X Route side, so the user must check the console.
    Uncertain,
}

struct RequestHead {
    method: String,
    target: String,
    host: Option<String>,
}

async fn read_head(stream: &mut TcpStream) -> Option<RequestHead> {
    let mut buffer = Vec::with_capacity(1024);
    let mut chunk = [0u8; 1024];
    let read = async {
        while !buffer.windows(4).any(|w| w == b"\r\n\r\n") {
            let n = stream.read(&mut chunk).await.ok()?;
            if n == 0 || buffer.len() + n > MAX_REQUEST_BYTES {
                return None;
            }
            buffer.extend_from_slice(&chunk[..n]);
        }
        Some(())
    };
    tokio::time::timeout(READ_TIMEOUT, read).await.ok()??;

    let text = String::from_utf8_lossy(&buffer);
    let mut lines = text.split("\r\n");
    let mut request_line = lines.next()?.split(' ');
    let method = request_line.next()?.to_string();
    let target = request_line.next()?.to_string();
    let host = lines
        .take_while(|line| !line.is_empty())
        .filter_map(|line| line.split_once(':'))
        .find(|(key, _)| key.trim().eq_ignore_ascii_case("host"))
        .map(|(_, value)| value.trim().to_string());
    Some(RequestHead {
        method,
        target,
        host,
    })
}

async fn respond(stream: &mut TcpStream, status: u16, body: &str) {
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        409 => "Conflict",
        _ => "Bad Gateway",
    };
    let response = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes()).await;
    let _ = stream.shutdown().await;
}

/// Serve the callback until one valid result arrives, `cancel` fires, or
/// `timeout` ends. `exchange` runs at most once.
pub async fn serve_callback<F, Fut>(
    listener: TcpListener,
    port: u16,
    state: &str,
    timeout: Duration,
    mut cancel: oneshot::Receiver<()>,
    exchange: F,
) -> SignInStatus
where
    F: FnOnce(String) -> Fut,
    Fut: Future<Output = Result<(), String>>,
{
    let deadline = tokio::time::sleep(timeout);
    tokio::pin!(deadline);
    let mut exchange = Some(exchange);

    loop {
        let mut stream = tokio::select! {
            () = &mut deadline => return SignInStatus::Expired,
            _ = &mut cancel => return SignInStatus::Cancelled,
            accepted = listener.accept() => match accepted {
                Ok((stream, _)) => stream,
                Err(_) => continue,
            },
        };
        let Some(head) = read_head(&mut stream).await else {
            respond(&mut stream, 400, CallbackError::Request.message()).await;
            continue;
        };
        match parse_callback(
            &head.method,
            &head.target,
            head.host.as_deref(),
            port,
            state,
        ) {
            Err(error) => respond(&mut stream, 400, error.message()).await,
            Ok(Callback::Denied) => {
                respond(
                    &mut stream,
                    200,
                    "Connection cancelled. No key was created.",
                )
                .await;
                return SignInStatus::Cancelled;
            }
            Ok(Callback::Code(code)) => {
                let Some(run) = exchange.take() else {
                    respond(&mut stream, 409, "This connection has already finished.").await;
                    continue;
                };
                let (status, code, body) = if run(code).await.is_ok() {
                    (
                        SignInStatus::Connected,
                        200,
                        "Connected. Your key is saved on this computer. You can close this tab.",
                    )
                } else {
                    (
                        SignInStatus::Uncertain,
                        502,
                        "Connection outcome is uncertain. Check API keys in Deep X Route, revoke the app key if present, and start again.",
                    )
                };
                respond(&mut stream, code, body).await;
                return status;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    const STATE: &str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ";
    const CODE: &str = "CODEfghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ_";

    async fn send(port: u16, target: &str, host: &str) -> String {
        let mut stream = TcpStream::connect(("127.0.0.1", port)).await.unwrap();
        let request = format!("GET {target} HTTP/1.1\r\nHost: {host}\r\n\r\n");
        stream.write_all(request.as_bytes()).await.unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();
        response
    }

    #[tokio::test]
    async fn bad_state_is_refused_then_code_is_exchanged_once() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (_cancel_tx, cancel_rx) = oneshot::channel();
        let calls = Arc::new(AtomicUsize::new(0));
        let counter = calls.clone();
        let server = tokio::spawn(async move {
            serve_callback(
                listener,
                port,
                STATE,
                Duration::from_secs(10),
                cancel_rx,
                |code| async move {
                    assert_eq!(code, CODE);
                    counter.fetch_add(1, Ordering::SeqCst);
                    Ok(())
                },
            )
            .await
        });

        let host = format!("127.0.0.1:{port}");
        let wrong = "Xbcdefghijklmnopqrstuvwxyz0123456789ABCDEFG";
        let refused = send(port, &format!("/callback?code={CODE}&state={wrong}"), &host).await;
        assert!(refused.starts_with("HTTP/1.1 400"));
        let wrong_host = send(
            port,
            &format!("/callback?code={CODE}&state={STATE}"),
            "evil.test",
        )
        .await;
        assert!(wrong_host.starts_with("HTTP/1.1 400"));

        let ok = send(port, &format!("/callback?code={CODE}&state={STATE}"), &host).await;
        assert!(ok.starts_with("HTTP/1.1 200"));
        assert!(ok.contains("Cache-Control: no-store"));
        assert_eq!(server.await.unwrap(), SignInStatus::Connected);
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn failed_exchange_is_uncertain() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (_cancel_tx, cancel_rx) = oneshot::channel();
        let server = tokio::spawn(async move {
            serve_callback(
                listener,
                port,
                STATE,
                Duration::from_secs(10),
                cancel_rx,
                |_| async { Err("exchange failed".to_string()) },
            )
            .await
        });
        let host = format!("127.0.0.1:{port}");
        let reply = send(port, &format!("/callback?code={CODE}&state={STATE}"), &host).await;
        assert!(reply.starts_with("HTTP/1.1 502"));
        assert_eq!(server.await.unwrap(), SignInStatus::Uncertain);
    }

    #[tokio::test]
    async fn cancel_and_timeout_end_the_server() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let (cancel_tx, cancel_rx) = oneshot::channel();
        let task = tokio::spawn(async move {
            serve_callback(
                listener,
                1,
                STATE,
                Duration::from_secs(10),
                cancel_rx,
                |_| async { Ok(()) },
            )
            .await
        });
        cancel_tx.send(()).unwrap();
        assert_eq!(task.await.unwrap(), SignInStatus::Cancelled);

        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let (_cancel_tx, cancel_rx) = oneshot::channel();
        let status = serve_callback(
            listener,
            1,
            STATE,
            Duration::from_millis(50),
            cancel_rx,
            |_| async { Ok(()) },
        )
        .await;
        assert_eq!(status, SignInStatus::Expired);
    }
}
