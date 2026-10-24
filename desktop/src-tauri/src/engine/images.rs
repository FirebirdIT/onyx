//! Removal of old Deep X engine images after an update.
//!
//! Only images of the Deep X engine repositories are removed. Other Docker
//! images on the computer are never touched.

use super::docker::{docker_command, find_docker, output_text};
use super::hub::EngineImages;

/// The image repositories that the Deep X engine pulls.
const ENGINE_REPOSITORIES: [&str; 3] = [
    "ghcr.io/firebirdit/deepx-backend",
    "ghcr.io/firebirdit/deepx-web-server",
    "ghcr.io/firebirdit/deepx-model-server",
];

/// `docker image ls` output format: repository, tag and ID, tab separated.
const LIST_FORMAT: &str = "{{.Repository}}\t{{.Tag}}\t{{.ID}}";

/// The images to remove from a `docker image ls` listing in [`LIST_FORMAT`].
///
/// An image stays when it is not a Deep X engine image, or when its reference
/// is one of `keep`. An untagged engine image is named by its ID.
pub fn stale_images(listing: &str, keep: &[&str]) -> Vec<String> {
    let mut stale: Vec<String> = Vec::new();
    for line in listing.lines() {
        let mut fields = line.trim().split('\t');
        let (Some(repository), Some(tag), Some(id)) = (fields.next(), fields.next(), fields.next())
        else {
            continue;
        };
        if !ENGINE_REPOSITORIES.contains(&repository) {
            continue;
        }
        let target = if tag == "<none>" {
            id.to_string()
        } else {
            let reference = format!("{repository}:{tag}");
            if keep.contains(&reference.as_str()) {
                continue;
            }
            reference
        };
        if !stale.contains(&target) {
            stale.push(target);
        }
    }
    stale
}

/// Remove the Deep X engine images that `current` does not use. Returns the
/// number of removed images. An image that a container still uses stays,
/// because `docker image rm` refuses it.
pub async fn remove_old_engine_images(current: &EngineImages) -> Result<usize, String> {
    let docker = find_docker().ok_or("Docker was not found")?;
    let mut list = docker_command(&docker);
    list.args(["image", "ls", "--format", LIST_FORMAT]);
    for repository in ENGINE_REPOSITORIES {
        list.args(["--filter", &format!("reference={repository}")]);
    }
    let listing = output_text(list).await?;
    let keep = [
        current.backend_image.as_str(),
        current.web_image.as_str(),
        current.model_server_image.as_str(),
    ];
    let mut removed = 0;
    for image in stale_images(&listing, &keep) {
        let mut remove = docker_command(&docker);
        remove.args(["image", "rm", &image]);
        if output_text(remove).await.is_ok() {
            removed += 1;
        }
    }
    Ok(removed)
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEEP: [&str; 3] = [
        "ghcr.io/firebirdit/deepx-backend:0.1.3",
        "ghcr.io/firebirdit/deepx-web-server:0.1.3",
        "ghcr.io/firebirdit/deepx-model-server:0.1.3",
    ];

    #[test]
    fn old_engine_tags_are_stale_and_current_tags_stay() {
        let listing = "ghcr.io/firebirdit/deepx-backend\t0.1.3\taaa\n\
                       ghcr.io/firebirdit/deepx-backend\t0.1.0\tbbb\n\
                       ghcr.io/firebirdit/deepx-web-server\t0.1.0\tccc\n\
                       ghcr.io/firebirdit/deepx-web-server\t0.1.3\tddd\n";
        assert_eq!(
            stale_images(listing, &KEEP),
            vec![
                "ghcr.io/firebirdit/deepx-backend:0.1.0",
                "ghcr.io/firebirdit/deepx-web-server:0.1.0",
            ]
        );
    }

    #[test]
    fn other_images_are_never_stale() {
        let listing = "onyxdotapp/onyx-backend\tlatest\teee\n\
                       postgres\t15.2-alpine\tfff\n\
                       ghcr.io/firebirdit/deepx-backend-extra\t0.1.0\tggg\n\
                       nginx\t<none>\thhh\n";
        assert_eq!(stale_images(listing, &KEEP), Vec::<String>::new());
    }

    #[test]
    fn untagged_engine_images_are_named_by_id_once() {
        let listing = "ghcr.io/firebirdit/deepx-backend\t<none>\tiii\n\
                       ghcr.io/firebirdit/deepx-backend\t<none>\tiii\n\
                       malformed line\n";
        assert_eq!(stale_images(listing, &KEEP), vec!["iii"]);
    }
}
