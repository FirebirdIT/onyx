//! Engine image information from the Deep X hub (`GET {hub}/api/engine`).

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;
use std::time::Duration;

const DEFAULT_BACKEND_IMAGE: &str = "ghcr.io/firebirdit/deepx-backend";
const DEFAULT_WEB_IMAGE: &str = "ghcr.io/firebirdit/deepx-web-server";
const DEFAULT_MODEL_SERVER_IMAGE: &str = "ghcr.io/firebirdit/deepx-model-server";
const FALLBACK_TAG: &str = "latest";
const HUB_TIMEOUT: Duration = Duration::from_secs(8);

/// The images that the engine runs. Each image value is a full reference with
/// a tag, because the compose file uses `ONYX_BACKEND_IMAGE` as it is.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EngineImages {
    pub version: Option<String>,
    pub image_tag: String,
    pub backend_image: String,
    pub web_image: String,
    pub model_server_image: String,
}

/// The `GET /api/engine` response body.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct HubEngineResponse {
    version: Option<String>,
    image_tag: Option<String>,
    backend_image: Option<String>,
    web_image: Option<String>,
    model_server_image: Option<String>,
}

fn is_valid_tag(tag: &str) -> bool {
    !tag.is_empty()
        && tag.len() <= 128
        && tag
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "._-".contains(c))
}

fn is_valid_image_ref(image: &str) -> bool {
    !image.is_empty()
        && image.len() <= 255
        && image
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || "._/:@-".contains(c))
}

/// Add `:tag` to an image name that has no tag or digest yet.
fn with_tag(image: &str, tag: &str) -> String {
    let last_segment = image.rsplit('/').next().unwrap_or(image);
    if last_segment.contains(':') || last_segment.contains('@') {
        image.to_string()
    } else {
        format!("{image}:{tag}")
    }
}

impl EngineImages {
    /// The images to use when the hub has no release or cannot be reached.
    pub fn fallback() -> Self {
        Self::from_parts(None, FALLBACK_TAG, None, None, None)
    }

    fn from_parts(
        version: Option<String>,
        tag: &str,
        backend: Option<&str>,
        web: Option<&str>,
        model: Option<&str>,
    ) -> Self {
        Self {
            version,
            image_tag: tag.to_string(),
            backend_image: with_tag(backend.unwrap_or(DEFAULT_BACKEND_IMAGE), tag),
            web_image: with_tag(web.unwrap_or(DEFAULT_WEB_IMAGE), tag),
            model_server_image: with_tag(model.unwrap_or(DEFAULT_MODEL_SERVER_IMAGE), tag),
        }
    }

    fn is_valid(&self) -> bool {
        is_valid_tag(&self.image_tag)
            && [
                &self.backend_image,
                &self.web_image,
                &self.model_server_image,
            ]
            .iter()
            .all(|image| is_valid_image_ref(image))
    }
}

/// Read a hub response. A missing or bad value falls back to the defaults.
pub fn parse_engine_response(body: &str) -> Result<EngineImages, String> {
    let parsed: HubEngineResponse =
        serde_json::from_str(body).map_err(|e| format!("Hub engine response is not valid: {e}"))?;
    let tag = parsed
        .image_tag
        .filter(|tag| is_valid_tag(tag))
        .unwrap_or_else(|| FALLBACK_TAG.to_string());
    let images = EngineImages::from_parts(
        parsed.version.filter(|v| is_valid_tag(v)),
        &tag,
        parsed.backend_image.as_deref(),
        parsed.web_image.as_deref(),
        parsed.model_server_image.as_deref(),
    );
    if images.is_valid() {
        Ok(images)
    } else {
        Err("Hub engine response has an image name that is not valid".to_string())
    }
}

/// Get the newest engine images from the hub.
pub async fn fetch_engine_images(hub_url: &str) -> Result<EngineImages, String> {
    let url = format!("{}/api/engine", hub_url.trim_end_matches('/'));
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
        .map_err(|e| format!("Failed to read hub response: {e}"))?;
    parse_engine_response(&body)
}

/// Images from the hub. If the hub fails, use the images of the last start,
/// and if there are none, use the `latest` tag.
pub async fn resolve_engine_images(hub_url: &str, cache_path: &Path) -> (EngineImages, String) {
    match fetch_engine_images(hub_url).await {
        Ok(images) => {
            if let Ok(json) = serde_json::to_vec_pretty(&images) {
                // A failed cache write only loses the offline fallback.
                let _ = fs::write(cache_path, json);
            }
            let note = format!(
                "Engine version: {}",
                images.version.as_deref().unwrap_or(&images.image_tag)
            );
            (images, note)
        }
        Err(e) => {
            let cached = fs::read_to_string(cache_path)
                .ok()
                .and_then(|text| serde_json::from_str::<EngineImages>(&text).ok())
                .filter(EngineImages::is_valid);
            cached.map_or_else(
                || {
                    (
                        EngineImages::fallback(),
                        format!("{e}. Using the latest engine images."),
                    )
                },
                |images| {
                    (
                        images,
                        format!("{e}. Using the engine version of the last start."),
                    )
                },
            )
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use crate::test_http::{serve_once, Reply};

    #[test]
    fn parses_hub_response_with_repo_names() {
        let images = parse_engine_response(
            r#"{"version":"1.2.3","imageTag":"1.2.3","backendImage":"ghcr.io/firebirdit/deepx-backend","webImage":"ghcr.io/firebirdit/deepx-web-server","modelServerImage":"ghcr.io/firebirdit/deepx-model-server"}"#,
        )
        .unwrap();
        assert_eq!(images.version.as_deref(), Some("1.2.3"));
        assert_eq!(
            images.backend_image,
            "ghcr.io/firebirdit/deepx-backend:1.2.3"
        );
        assert_eq!(
            images.web_image,
            "ghcr.io/firebirdit/deepx-web-server:1.2.3"
        );
    }

    #[test]
    fn keeps_full_references_and_falls_back_to_latest() {
        let images = parse_engine_response(
            r#"{"backendImage":"ghcr.io/firebirdit/deepx-backend:9.9.9","webImage":null}"#,
        )
        .unwrap();
        assert_eq!(images.image_tag, "latest");
        assert_eq!(
            images.backend_image,
            "ghcr.io/firebirdit/deepx-backend:9.9.9"
        );
        assert_eq!(
            images.web_image,
            "ghcr.io/firebirdit/deepx-web-server:latest"
        );
    }

    #[test]
    fn rejects_unsafe_image_names() {
        assert!(parse_engine_response(r#"{"backendImage":"evil image\nX=1"}"#).is_err());
        assert!(parse_engine_response("not json").is_err());
    }

    #[test]
    fn with_tag_ignores_registry_port() {
        assert_eq!(
            with_tag("localhost:5000/app", "1.0"),
            "localhost:5000/app:1.0"
        );
        assert_eq!(with_tag("app@sha256:abc", "1.0"), "app@sha256:abc");
    }

    #[tokio::test]
    async fn resolve_uses_hub_then_cache() {
        let dir = tempfile::tempdir().unwrap();
        let cache = dir.path().join("engine-info.json");
        let body = r#"{"version":"2.0.0","imageTag":"2.0.0"}"#;
        let hub = serve_once(Reply::json(200, body));
        let (images, _) = resolve_engine_images(&hub, &cache).await;
        assert_eq!(images.image_tag, "2.0.0");

        // The hub is gone now, so the cached images are used.
        let (cached, note) = resolve_engine_images("http://127.0.0.1:9", &cache).await;
        assert_eq!(cached.image_tag, "2.0.0");
        assert!(note.contains("last start"));
    }

    #[tokio::test]
    async fn resolve_falls_back_to_latest() {
        let dir = tempfile::tempdir().unwrap();
        let hub = serve_once(Reply::json(204, ""));
        let (images, _) = resolve_engine_images(&hub, &dir.path().join("none.json")).await;
        assert_eq!(images, EngineImages::fallback());
    }
}
