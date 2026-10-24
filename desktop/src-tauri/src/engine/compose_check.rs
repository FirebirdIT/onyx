//! A check of the written engine files with the real Docker Compose CLI.
//! It needs Docker, so it is ignored by default:
//! `cargo test -- --ignored compose_config`.
#![allow(clippy::unwrap_used, clippy::expect_used)]

use super::docker::{find_docker, Compose};
use super::env::{write_env, EngineSecrets, EnvInput};
use super::files::{write_compose_files, EngineLayout};
use super::hub::EngineImages;
use crate::config::EngineMode;

fn write_engine(layout: &EngineLayout) {
    write_compose_files(layout).unwrap();
    let images = EngineImages::fallback();
    let secrets = EngineSecrets::generate();
    write_env(
        &layout.env_path(),
        &EnvInput {
            images: &images,
            secrets: &secrets,
            route_api_key: Some("sk-dxr-test0000000000000000000000000000000000000"),
            hub_url: "https://deepx-hub.vercel.app",
        },
    )
    .unwrap();
}

async fn services(compose: &Compose, extra: &[&str]) -> Vec<String> {
    let mut args = extra.to_vec();
    args.extend(["config", "--services"]);
    let text = compose
        .run_capture(args)
        .await
        .expect("compose config failed");
    let mut names: Vec<String> = text.lines().map(str::to_string).collect();
    names.sort();
    names
}

#[tokio::test]
#[ignore = "needs the Docker CLI"]
async fn compose_config_is_valid_for_both_modes() {
    let docker = find_docker().expect("docker CLI");
    let dir = tempfile::tempdir().unwrap();
    let layout = EngineLayout::new(dir.path());
    write_engine(&layout);

    for mode in [EngineMode::Lite, EngineMode::Full] {
        let compose = Compose::new(
            docker.clone(),
            layout.compose_dir(),
            layout.compose_files(mode),
        );
        let rendered = compose
            .run_capture(["config"])
            .await
            .expect("compose config failed");
        assert!(rendered.contains("ghcr.io/firebirdit/deepx-backend:latest"));
        assert!(rendered.contains("host_ip: 127.0.0.1"));
        assert!(rendered.contains("published: \"3000\""));
        assert!(!rendered.contains("published: \"80\""));
        assert!(rendered.contains("/data/nginx"));
    }

    let lite = Compose::new(
        docker.clone(),
        layout.compose_dir(),
        layout.compose_files(EngineMode::Lite),
    );
    let lite_services = services(&lite, &[]).await;
    assert!(lite_services.contains(&"api_server".to_string()));
    assert!(!lite_services.contains(&"opensearch".to_string()));
    assert!(!lite_services.contains(&"background".to_string()));

    let full = Compose::new(
        docker,
        layout.compose_dir(),
        layout.compose_files(EngineMode::Full),
    );
    let full_services = services(&full, &[]).await;
    assert!(full_services.contains(&"opensearch".to_string()));
    assert!(full_services.contains(&"background".to_string()));
    let all_services = services(&full, &["--profile", "*"]).await;
    assert!(all_services.contains(&"object-store".to_string()));
}
