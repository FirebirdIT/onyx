//! File helpers for files that hold secrets.

use std::fs;
use std::io::Write;
use std::path::Path;

/// Write `contents` to `path` so that only the current user can read it
/// (mode 0600 on Unix). The data goes to a temporary file first and then
/// replaces `path`, so a crash never leaves a half-written file.
pub fn write_private(path: &Path, contents: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("No parent folder for {}", path.display()))?;
    fs::create_dir_all(parent)
        .map_err(|e| format!("Failed to create {}: {e}", parent.display()))?;

    let file_name = path
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_default();
    let tmp_path = parent.join(format!(".{file_name}.tmp"));

    let mut options = fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }

    let mut file = options
        .open(&tmp_path)
        .map_err(|e| format!("Failed to open {}: {e}", tmp_path.display()))?;
    // `mode` applies only when the file is new, so set it again for a file
    // that a crash left behind.
    set_private_permissions(&tmp_path)?;
    file.write_all(contents)
        .and_then(|()| file.sync_all())
        .map_err(|e| format!("Failed to write {}: {e}", tmp_path.display()))?;
    drop(file);

    fs::rename(&tmp_path, path).map_err(|e| format!("Failed to replace {}: {e}", path.display()))
}

#[cfg(unix)]
fn set_private_permissions(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|e| format!("Failed to set permissions on {}: {e}", path.display()))
}

// On Windows the app data folder is in the user profile, which only that
// user can read.
#[cfg(not(unix))]
#[allow(clippy::unnecessary_wraps)]
const fn set_private_permissions(_path: &Path) -> Result<(), String> {
    Ok(())
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn write_private_replaces_contents() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nested").join("secret.txt");
        write_private(&path, b"one").unwrap();
        write_private(&path, b"two").unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"two");
    }

    #[cfg(unix)]
    #[test]
    fn write_private_sets_owner_only_mode() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("secret.txt");
        write_private(&path, b"value").unwrap();
        let mode = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
    }
}
