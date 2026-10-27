//! Official Claude Desktop for Linux (Ubuntu/Debian beta). No proxy or wrapper.
use super::{run, secs};
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use tauri::{Runtime, WebviewWindow, image::Image};

pub const DEFAULT_HOTKEY: &str = "Alt+Shift+C";
pub const ASSET_TAG: &str = "-linux-";

pub fn app_data() -> PathBuf {
    dirs::config_dir().unwrap_or_default()
}

pub fn profile_root() -> PathBuf {
    app_data()
}

fn claude_exe() -> Option<PathBuf> {
    runtime::executable(
        &["/usr/bin/claude-desktop", "/opt/Claude/claude-desktop"],
        "claude-desktop",
    )
}

pub fn claude_installed() -> bool {
    claude_exe().is_some()
}

pub fn claude_hint() -> String {
    crate::locale::tr(
        "请先安装官方 Claude Desktop Linux Beta（Ubuntu / Debian），并确保 claude-desktop 位于 PATH。",
        "Install official Claude Desktop for Linux (Ubuntu / Debian beta); claude-desktop must be on PATH.",
    ).into()
}

fn profile_pids(dir: &Path) -> Vec<u32> {
    let Some(exe) = claude_exe() else {
        return vec![];
    };
    std::fs::read_dir("/proc")
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|entry| {
            let pid: u32 = entry.file_name().to_str()?.parse().ok()?;
            if std::fs::read_link(entry.path().join("exe")).ok()? != exe {
                return None;
            }
            let raw = std::fs::read(entry.path().join("cmdline")).ok()?;
            let args: Vec<_> = raw
                .split(|b| *b == 0)
                .map(String::from_utf8_lossy)
                .collect();
            if args.iter().any(|s| s.starts_with("--type=")) {
                return None;
            }
            let profile = args
                .iter()
                .find_map(|s| s.strip_prefix("--user-data-dir="))
                .map(PathBuf::from)
                .or_else(|| {
                    args.windows(2)
                        .find(|p| p[0] == "--user-data-dir")
                        .map(|p| PathBuf::from(p[1].as_ref()))
                })
                .unwrap_or_else(|| app_data().join("Claude"));
            (profile == dir).then_some(pid)
        })
        .collect()
}

pub fn is_running(dir: &Path) -> bool {
    // Chromium's singleton symlink also covers a launch before /proc is ready.
    !profile_pids(dir).is_empty()
        || std::fs::read_link(dir.join("SingletonLock"))
            .ok()
            .and_then(|p| p.to_str()?.rsplit_once('-')?.1.parse::<u32>().ok())
            .is_some_and(|pid| Path::new("/proc").join(pid.to_string()).exists())
}

pub fn launch_claude(dir: &Path, _is_default: bool) -> Result<(), Option<String>> {
    use std::os::unix::process::CommandExt;
    let exe = claude_exe().ok_or_else(|| Some(claude_hint()))?;
    let mut arg = OsString::from("--user-data-dir=");
    arg.push(dir);
    Command::new(exe)
        .arg(arg)
        .env("CLAUDE_USER_DATA_DIR", dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()
        .map(drop)
        .map_err(|e| Some(e.to_string()))
}

fn windows(dir: &Path) -> Vec<String> {
    let pids = profile_pids(dir);
    run("wmctrl", &["-lp"], secs(4))
        .out
        .lines()
        .filter_map(|line| {
            let cols: Vec<_> = line.split_whitespace().take(3).collect();
            (cols.len() == 3
                && cols[2]
                    .parse::<u32>()
                    .ok()
                    .is_some_and(|pid| pids.contains(&pid)))
            .then(|| cols[0].to_owned())
        })
        .collect()
}

pub fn activate_profile(dir: &Path) {
    if let Some(id) = windows(dir).first() {
        run("wmctrl", &["-ia", id], secs(4));
    }
}

pub fn quit_profile(dir: &Path) -> Result<usize, String> {
    let ids = windows(dir);
    if ids.is_empty() {
        return Err(crate::locale::tr(
            "请在 Claude 中正常退出；自动请求关闭窗口需要 X11 / wmctrl。",
            "Quit normally in Claude. Requesting window close requires X11 / wmctrl.",
        )
        .into());
    }
    for id in &ids {
        if !run("wmctrl", &["-ic", id], secs(4)).ok {
            return Err("Claude window close was declined. Quit normally in Claude.".into());
        }
    }
    Ok(ids.len())
}

pub fn install_path() -> Option<PathBuf> {
    std::env::current_exe().ok()
}
pub fn install_looks_sane(_p: &Path) -> bool {
    false
}
pub fn unpack(_zip: &Path, _dest: &Path) -> Result<(), String> {
    Err("Install the Linux package from Releases.".into())
}
pub fn find_fresh(_dir: &Path) -> Result<PathBuf, String> {
    Err("Install the Linux package from Releases.".into())
}
pub fn swap_and_relaunch(
    _dest: &Path,
    _fresh: &Path,
    _stage: &Path,
    _pid: u32,
) -> Result<(), String> {
    Err("Install the Linux package from Releases.".into())
}
pub fn curl() -> &'static str {
    "curl"
}
pub fn set_badge<R: Runtime>(_win: &WebviewWindow<R>, _n: usize) {}
pub fn forget_theme() {}
pub fn tray_image() -> (Image<'static>, bool) {
    (
        Image::from_bytes(include_bytes!("../../icons/32x32.png")).expect("bundled icon"),
        false,
    )
}

fn desktop_file(autostart: bool) -> PathBuf {
    if autostart {
        app_data().join("autostart/CASE.desktop")
    } else {
        dirs::data_local_dir()
            .unwrap_or_default()
            .join("applications/CASE.desktop")
    }
}

fn write_desktop(path: &Path, hidden: bool) -> Result<(), String> {
    let exe = std::env::var_os("APPIMAGE")
        .map(PathBuf::from)
        .or_else(install_path)
        .ok_or("Missing executable")?;
    let raw = exe.to_str().ok_or("Invalid executable path")?;
    if raw.contains(['\n', '\r']) {
        return Err("Invalid executable path".into());
    }
    // Desktop Entry Exec is its own format, not a shell command.
    let quoted = raw
        .replace('\\', "\\\\\\\\")
        .replace('"', "\\\\\"")
        .replace('`', "\\\\`")
        .replace('$', "\\\\$")
        .replace('%', "%%");
    let text = format!(
        "[Desktop Entry]\nType=Application\nName=CASE\nComment=Claude Account Switcher Engine\nExec=\"{quoted}\"{}\nTerminal=false\nCategories=Utility;\n",
        if hidden { " --hidden" } else { "" }
    );
    std::fs::create_dir_all(path.parent().ok_or("Missing directory")?)
        .map_err(|e| e.to_string())?;
    std::fs::write(path, text).map_err(|e| e.to_string())
}

pub struct ShortcutStatus {
    pub present: bool,
    pub blurb: &'static str,
    pub cta: &'static str,
    pub tip: &'static str,
}
pub fn shortcut_status() -> ShortcutStatus {
    ShortcutStatus {
        present: desktop_file(false).is_file(),
        blurb: crate::locale::tr(
            "在应用菜单中添加 CASE。",
            "Add CASE to the application menu.",
        ),
        cta: crate::locale::tr("添加快捷方式", "Add shortcut"),
        tip: crate::locale::tr("创建用户级应用菜单入口", "Create a user application entry"),
    }
}
pub fn shortcut_create() -> Result<bool, String> {
    let path = desktop_file(false);
    let existed = path.exists();
    write_desktop(&path, false)?;
    Ok(existed)
}
pub fn open_at_login() -> bool {
    desktop_file(true).is_file()
}
pub fn set_open_at_login(on: bool) {
    if on {
        let _ = write_desktop(&desktop_file(true), true);
    } else {
        let _ = std::fs::remove_file(desktop_file(true));
    }
}
pub fn profile_size(dir: &Path) -> Option<u64> {
    let out = run(
        "du",
        &[OsString::from("-sk"), OsString::from("--"), dir.into()],
        secs(20),
    );
    out.ok
        .then(|| {
            out.out
                .split_whitespace()
                .next()?
                .parse::<u64>()
                .ok()?
                .checked_mul(1024)
        })
        .flatten()
}
pub fn reveal(path: &Path) {
    let dir = if path.is_dir() {
        path
    } else {
        path.parent().unwrap_or(path)
    };
    let _ = Command::new("xdg-open")
        .arg(dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
}
pub fn open_url(url: &str) {
    let _ = Command::new("xdg-open")
        .arg(url)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
}
pub fn can_write(dir: &Path) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let Ok(path) = std::ffi::CString::new(dir.as_os_str().as_bytes()) else {
        return false;
    };
    // SAFETY: NUL-terminated path; access only checks permissions.
    unsafe { libc::access(path.as_ptr(), libc::W_OK) == 0 }
}

pub mod chrome {
    use super::*;
    fn exe() -> Option<PathBuf> {
        runtime::executable(
            &["/usr/bin/google-chrome", "/opt/google/chrome/google-chrome"],
            "google-chrome",
        )
    }
    pub fn installed() -> bool {
        exe().is_some()
    }
    pub fn root() -> PathBuf {
        app_data().join("google-chrome")
    }
    pub fn launch(profile: &str, url: Option<&str>) {
        if let Some(exe) = exe() {
            let _ = Command::new(exe)
                .arg(format!("--profile-directory={profile}"))
                .args(url)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn();
        }
    }
}

use super::runtime;
