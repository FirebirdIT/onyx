//! The hub backup API. Each call sends the Deep X Route key as the bearer
//! token and the backup token in `X-Backup-Token`. Part downloads go to blob
//! URLs on a different host, so they get no credentials.

use serde::{Deserialize, Serialize};
use std::time::Duration;

pub const MAX_PART_BYTES: usize = 4 * 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(120);

pub struct HubBackup<'a> {
    pub hub_url: &'a str,
    pub backup_id: &'a str,
    pub token: &'a str,
    pub route_key: &'a str,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct BackupInfo {
    pub created_at: String,
    pub size: u64,
    pub sha256: String,
    pub parts: Vec<String>,
}

#[derive(Serialize)]
struct StartRequest<'a> {
    parts: usize,
    size: u64,
    sha256: &'a str,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StartResponse {
    upload_id: String,
}

fn client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(TIMEOUT)
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {e}"))
}

fn status_error(status: u16) -> String {
    match status {
        400 => "The hub refused the request as not valid. Update Deep X and try again.".to_string(),
        503 => "The hub cannot check the Deep X Route key now. Try again later.".to_string(),
        401 => {
            "The hub does not accept the Deep X Route key. Connect Deep X Route again.".to_string()
        }
        403 => "The hub refused this backup. Check the email and the passphrase.".to_string(),
        404 => "No backup found for this email and passphrase.".to_string(),
        413 => "The backup is too large for the hub.".to_string(),
        _ => format!("The hub returned HTTP {status}. Try again later."),
    }
}

fn is_safe_segment(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

pub fn part_count(size: u64) -> usize {
    let max = MAX_PART_BYTES as u64;
    usize::try_from(size.div_ceil(max).max(1)).unwrap_or(usize::MAX)
}

impl HubBackup<'_> {
    fn url(&self, suffix: &str) -> String {
        format!(
            "{}/api/backup/{}{suffix}",
            self.hub_url.trim_end_matches('/'),
            self.backup_id
        )
    }

    fn request(
        &self,
        method: reqwest::Method,
        url: String,
    ) -> Result<reqwest::RequestBuilder, String> {
        Ok(client()?
            .request(method, url)
            .bearer_auth(self.route_key)
            .header("X-Backup-Token", self.token))
    }

    async fn send(builder: reqwest::RequestBuilder) -> Result<String, String> {
        let response = builder
            .send()
            .await
            .map_err(|_| "The hub is not reachable. Check the internet connection.".to_string())?;
        let status = response.status().as_u16();
        if !(200..300).contains(&status) {
            return Err(status_error(status));
        }
        response
            .text()
            .await
            .map_err(|e| format!("Failed to read the hub response: {e}"))
    }

    pub async fn start(&self, parts: usize, size: u64, sha256: &str) -> Result<String, String> {
        let body = serde_json::to_string(&StartRequest {
            parts,
            size,
            sha256,
        })
        .map_err(|e| format!("Failed to encode request: {e}"))?;
        let builder = self
            .request(reqwest::Method::POST, self.url("/start"))?
            .header("Content-Type", "application/json")
            .body(body);
        let text = Self::send(builder).await?;
        let parsed: StartResponse =
            serde_json::from_str(&text).map_err(|_| "The hub answer is not valid.".to_string())?;
        if is_safe_segment(&parsed.upload_id) {
            Ok(parsed.upload_id)
        } else {
            Err("The hub answer is not valid.".to_string())
        }
    }

    pub async fn put_part(
        &self,
        upload_id: &str,
        index: usize,
        bytes: Vec<u8>,
    ) -> Result<(), String> {
        if bytes.len() > MAX_PART_BYTES {
            return Err("A backup part is too large.".to_string());
        }
        let builder = self
            .request(
                reqwest::Method::PUT,
                self.url(&format!("/{upload_id}/{index}")),
            )?
            .header("Content-Type", "application/octet-stream")
            .body(bytes);
        Self::send(builder).await.map(|_| ())
    }

    pub async fn commit(&self, upload_id: &str) -> Result<(), String> {
        let builder = self.request(
            reqwest::Method::POST,
            self.url(&format!("/{upload_id}/commit")),
        )?;
        Self::send(builder).await.map(|_| ())
    }

    /// The current backup, or `None` if there is no backup.
    pub async fn info(&self) -> Result<Option<BackupInfo>, String> {
        let response = self
            .request(reqwest::Method::GET, self.url(""))?
            .send()
            .await
            .map_err(|_| "The hub is not reachable. Check the internet connection.".to_string())?;
        let status = response.status().as_u16();
        if status == 404 {
            return Ok(None);
        }
        if !(200..300).contains(&status) {
            return Err(status_error(status));
        }
        let text = response
            .text()
            .await
            .map_err(|e| format!("Failed to read the hub response: {e}"))?;
        serde_json::from_str(&text)
            .map(Some)
            .map_err(|_| "The hub answer is not valid.".to_string())
    }
}

/// Download one part from its blob URL. Only HTTPS (or loopback HTTP in
/// tests) is permitted, and no credentials are sent.
pub async fn download_part(url: &str) -> Result<Vec<u8>, String> {
    let parsed = url::Url::parse(url).map_err(|_| "A backup part URL is not valid.".to_string())?;
    let loopback = parsed.host_str() == Some("127.0.0.1");
    if parsed.scheme() != "https" && !(cfg!(test) && loopback) {
        return Err("A backup part URL is not HTTPS.".to_string());
    }
    let response = client()?
        .get(parsed)
        .send()
        .await
        .map_err(|_| "Failed to download the backup.".to_string())?;
    if !response.status().is_success() {
        return Err(format!(
            "Failed to download the backup (HTTP {}).",
            response.status().as_u16()
        ));
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|_| "Failed to download the backup.".to_string())?;
    if bytes.len() > MAX_PART_BYTES {
        return Err("A backup part is too large.".to_string());
    }
    Ok(bytes.to_vec())
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use crate::test_http::{serve, Reply};

    const ID: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const TOKEN: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

    #[test]
    fn status_errors_have_guidance() {
        assert!(status_error(503).contains("Try again later"));
        assert!(status_error(400).contains("not valid"));
        assert!(status_error(500).contains("500"));
    }

    #[test]
    fn part_count_rounds_up() {
        assert_eq!(part_count(0), 1);
        assert_eq!(part_count(1), 1);
        assert_eq!(part_count(MAX_PART_BYTES as u64), 1);
        assert_eq!(part_count(MAX_PART_BYTES as u64 + 1), 2);
    }

    #[tokio::test]
    async fn upload_flow_sends_auth_headers() {
        let (base, requests) = serve(|request| {
            if request.path.ends_with("/start") {
                Reply::json(200, r#"{"uploadId":"up1"}"#)
            } else {
                Reply::json(200, "{}")
            }
        });
        let hub = HubBackup {
            hub_url: &base,
            backup_id: ID,
            token: TOKEN,
            route_key: "sk-dxr-key",
        };
        let upload = hub.start(1, 3, "abc").await.unwrap();
        assert_eq!(upload, "up1");
        hub.put_part(&upload, 0, vec![1, 2, 3]).await.unwrap();
        hub.commit(&upload).await.unwrap();

        let seen = requests.lock().unwrap().clone();
        let paths: Vec<String> = seen
            .iter()
            .map(|r| format!("{} {}", r.method, r.path))
            .collect();
        assert_eq!(
            paths,
            [
                format!("POST /api/backup/{ID}/start"),
                format!("PUT /api/backup/{ID}/up1/0"),
                format!("POST /api/backup/{ID}/up1/commit"),
            ]
        );
        for request in &seen {
            assert_eq!(request.header("authorization"), Some("Bearer sk-dxr-key"));
            assert_eq!(request.header("x-backup-token"), Some(TOKEN));
        }
        let start: serde_json::Value = serde_json::from_slice(&seen[0].body).unwrap();
        assert_eq!(start["parts"], 1);
        assert_eq!(start["size"], 3);
        assert_eq!(seen[1].body, vec![1, 2, 3]);
    }

    #[tokio::test]
    async fn info_maps_404_to_none_and_403_to_error() {
        let (base, _) = serve(|request| {
            if request.header("x-backup-token") == Some(TOKEN) {
                Reply::json(404, "{}")
            } else {
                Reply::json(403, "{}")
            }
        });
        let mut hub = HubBackup {
            hub_url: &base,
            backup_id: ID,
            token: TOKEN,
            route_key: "k",
        };
        assert_eq!(hub.info().await.unwrap(), None);
        hub.token = "other";
        assert!(hub.info().await.unwrap_err().contains("refused"));
    }

    #[tokio::test]
    async fn download_part_sends_no_credentials() {
        let (base, requests) = serve(|_| Reply::bytes(vec![9, 9]));
        let bytes = download_part(&format!("{base}/blob/part0")).await.unwrap();
        assert_eq!(bytes, vec![9, 9]);
        assert!(requests.lock().unwrap()[0]
            .header("authorization")
            .is_none());
        assert!(download_part("http://example.com/part").await.is_err());
    }
}
