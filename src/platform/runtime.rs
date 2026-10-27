//! Explicit dependency discovery. Never reads shell profiles or credentials.
use std::path::{Path, PathBuf};

pub fn canonical(path: &Path) -> Option<PathBuf> {
    let path = path.canonicalize().ok()?;
    #[cfg(windows)]
    {
        let text = path.to_str()?;
        if let Some(unc) = text.strip_prefix(r"\\?\UNC\") {
            return Some(PathBuf::from(format!(r"\\{unc}")));
        }
        if let Some(drive) = text.strip_prefix(r"\\?\") {
            return Some(drive.into());
        }
    }
    Some(path)
}

pub fn executable(candidates: &[&str], name: &str) -> Option<PathBuf> {
    let mut paths: Vec<PathBuf> = candidates.iter().map(PathBuf::from).collect();
    if let Some(path) = std::env::var_os("PATH") {
        paths.extend(
            std::env::split_paths(&path)
                .filter(|p| p.is_absolute())
                .map(|p| p.join(name)),
        );
    }
    paths.into_iter().find_map(|path| {
        let path = canonical(&path)?;
        let meta = std::fs::metadata(&path).ok()?;
        if !meta.is_file() {
            return None;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if meta.permissions().mode() & 0o111 == 0 {
                return None;
            }
        }
        Some(path)
    })
}

pub fn python() -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        executable(&["/usr/bin/python3"], "python3")
    }
    #[cfg(target_os = "linux")]
    {
        executable(&["/usr/bin/python3"], "python3")
    }
    #[cfg(windows)]
    {
        // Prefer the CASE venv, including the Windows ConPTY dependency.
        let private = crate::store::conf_dir().join("python/Scripts/python.exe");
        if private.is_file() {
            return canonical(&private);
        }
        executable(&[], "python.exe")
    }
}

pub fn is_redirected(meta: &std::fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes() & 0x400 != 0 {
            return true;
        }
    }
    meta.file_type().is_symlink()
}
