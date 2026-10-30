//! Local installation hook; all account data stays in official Claude profiles.
use serde_json::Value;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::time::{Duration, Instant};

#[derive(Default)]
pub struct Outcome {
    pub bootstrap: bool,
}

fn read_stdout(child: &mut std::process::Child) -> Result<Vec<u8>, String> {
    let mut stdout = child.stdout.take().ok_or_else(|| {
        crate::locale::tr("同步没有返回结果", "The sync process returned no output").to_owned()
    })?;
    let mut output = Vec::new();
    stdout.read_to_end(&mut output).map_err(|_| {
        crate::locale::tr("无法读取同步结果", "Could not read the sync result").to_owned()
    })?;
    Ok(output)
}

fn stage_reader(child: &mut std::process::Child) -> Receiver<String> {
    let (sender, receiver) = mpsc::channel();
    let Some(stderr) = child.stderr.take() else {
        return receiver;
    };
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            if let Some(stage) = line.strip_prefix("CASE_STAGE ") {
                let _ = sender.send(stage.to_owned());
            }
        }
    });
    receiver
}

fn drain_stages<F: FnMut(&str)>(receiver: &Receiver<String>, on_stage: &mut F) {
    while let Ok(stage) = receiver.try_recv() {
        on_stage(&stage);
    }
}

fn discard_stages(receiver: &Receiver<String>) {
    while receiver.try_recv().is_ok() {}
}

fn checked_file(path: &Path) -> Result<(), String> {
    checked_file_with_limit(path, 1024 * 1024)
}

fn checked_file_with_limit(path: &Path, max_bytes: u64) -> Result<(), String> {
    if !path.is_absolute() {
        return Err(crate::locale::tr(
            "同步文件路径必须为绝对路径",
            "Sync file paths must be absolute.",
        )
        .into());
    }
    for p in path.ancestors() {
        let meta = std::fs::symlink_metadata(p).map_err(|_| {
            crate::locale::tr(
                "同步文件或目录不存在",
                "A sync file or directory is missing.",
            )
        })?;
        if crate::platform::runtime::is_redirected(&meta) {
            return Err(crate::locale::tr(
                "同步文件路径含符号链接，已停止打开账号",
                "A sync path contains a symlink. Account launch stopped.",
            )
            .into());
        }
    }
    let meta = std::fs::metadata(path)
        .map_err(|_| crate::locale::tr("无法读取同步文件", "Could not read a sync file."))?;
    if !meta.is_file() || meta.len() > max_bytes {
        return Err(
            crate::locale::tr("同步文件类型或大小无效", "Invalid sync file type or size.").into(),
        );
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        let system_python = path == Path::new("/usr/bin/python3") && meta.uid() == 0;
        if (!system_python && meta.nlink() != 1) || meta.permissions().mode() & 0o022 != 0 {
            return Err(crate::locale::tr(
                "同步文件权限不安全，已停止打开账号",
                "Unsafe sync file permissions. Account launch stopped.",
            )
            .into());
        }
    }
    Ok(())
}

fn config_path() -> Result<PathBuf, String> {
    Ok(crate::platform::app_data().join("ClaudeSessionSync/CASE/hook.json"))
}

/// As `before_launch`, with progress callbacks for the launcher UI.  The
/// callback runs on the sync polling thread and never touches Claude itself.
pub fn before_launch_with_stage<F: FnMut(&str)>(
    target: &Path,
    mut on_stage: F,
) -> Result<Outcome, String> {
    let config = config_path()?;
    before_launch_with_config_stage(target, &config, &mut on_stage)
}

/// Run the configured index sync without launching another Claude profile.
///
/// The bridge still requests a normal Claude quit before writing indexes. This
/// keeps an explicit sync subject to the same process guard and transaction
/// rules as an account switch, while leaving the user in control of which
/// profile (if any) they open next.
pub fn sync_now_with_stage<F: FnMut(&str)>(
    target: &Path,
    mut on_stage: F,
) -> Result<Value, String> {
    let config = config_path()?;
    match run_bridge_with_config_stage(target, &config, &mut on_stage)? {
        Some(result) => Ok(result),
        None => Err(crate::locale::tr(
            "同步配置未启用，无法主动同步会话。",
            "Sync is not enabled, so sessions cannot be synced manually.",
        )
        .into()),
    }
}

/// Run a health repair through the same Python engine transaction used before a
/// launch.  The caller already holds the shared CASE operation lock and must not
/// ask Claude to quit on the user's behalf; the engine's stopped-process guard
/// remains authoritative.
pub fn rebuild_session(
    account: &crate::store::Account,
    orphan: &crate::health::Orphan,
) -> Result<Value, String> {
    let config = config_path()?;
    checked_file(&config)?;
    let value: Value = serde_json::from_slice(&std::fs::read(&config).map_err(|_| {
        crate::locale::tr("无法读取同步配置", "Could not read the sync configuration.")
    })?)
    .map_err(|_| crate::locale::tr("同步配置无效", "Invalid sync configuration."))?;
    if value.get("hook_enabled") != Some(&Value::Bool(true)) {
        return Err(crate::locale::tr(
            "同步配置未启用，无法通过同步引擎恢复会话。",
            "Sync is not enabled, so the session cannot be repaired through the sync engine.",
        )
        .into());
    }
    let file = |key: &str| -> Result<PathBuf, String> {
        let path = value
            .get(key)
            .and_then(Value::as_str)
            .map(PathBuf::from)
            .ok_or(crate::locale::tr(
                "同步配置缺少文件路径",
                "A file path is missing from the sync configuration.",
            ))?;
        checked_file_with_limit(
            &path,
            if key == "python_path" {
                100 * 1024 * 1024
            } else {
                1024 * 1024
            },
        )?;
        Ok(path)
    };
    let python = file("python_path")?;
    let bridge = file("bridge_path")?;
    let mut child = Command::new(python)
        .arg(bridge)
        .args(["--config"])
        .arg(&config)
        .arg("--target")
        .arg(&account.dir)
        .arg("--repair")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("CASE_PROGRESS", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| crate::locale::tr("无法启动本地同步", "Could not start local sync"))?;
    if let Some(mut input) = child.stdin.take() {
        let data = serde_json::to_vec(orphan)
            .map_err(|_| crate::locale::tr("恢复数据无效", "The repair data is invalid."))?;
        input
            .write_all(&data)
            .map_err(|_| crate::locale::tr("无法发送恢复数据", "Could not send repair data."))?;
    }
    let stages = stage_reader(&mut child);
    let started = Instant::now();
    loop {
        // Repair has no visible stage consumer today, but drain the side
        // channel so the helper cannot block on a full stderr pipe.
        discard_stages(&stages);
        match child.try_wait() {
            Ok(Some(status)) => {
                discard_stages(&stages);
                let output = read_stdout(&mut child)?;
                let result: Value = serde_json::from_slice(&output).map_err(|_| {
                    crate::locale::tr(
                        "同步结果无效；请检查同步状态后重试",
                        "Invalid sync result. Review the sync state before trying again.",
                    )
                })?;
                if status.success() {
                    return Ok(result);
                }
                let code = result
                    .get("code")
                    .and_then(Value::as_str)
                    .unwrap_or("SYNC_FAILED");
                let detail = match code {
                    "CLAUDE_MUST_BE_FULLY_CLOSED" => crate::locale::tr(
                        "Claude 仍在运行。请正常退出后再恢复会话。",
                        "Claude is still running. Quit normally before repairing the session.",
                    ),
                    "SESSION_MARKED_DELETED" => crate::locale::tr(
                        "该会话已有主动删除标记，未恢复。",
                        "This session has an explicit deletion marker and was not rebuilt.",
                    ),
                    "RECOVERY_REQUIRED" => crate::locale::tr(
                        "存在未完成的同步，需要先按快照恢复。",
                        "An unfinished sync requires recovery from its snapshot first.",
                    ),
                    _ => crate::locale::tr(
                        "会话恢复未通过，请检查同步状态。",
                        "Session repair was blocked. Review the sync state.",
                    ),
                };
                return Err(format!("{detail}\n{code}"));
            }
            Ok(None) if started.elapsed() < Duration::from_secs(75) => {
                std::thread::sleep(Duration::from_millis(100));
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(crate::locale::tr(
                    "同步超时，未恢复会话；请检查同步状态。",
                    "Sync timed out. The session was not repaired; review the sync state.",
                )
                .into());
            }
        }
    }
}

#[cfg(all(test, target_os = "macos"))]
fn before_launch_with_config(target: &Path, config: &Path) -> Result<Outcome, String> {
    before_launch_with_config_stage(target, config, &mut |_| {})
}

fn before_launch_with_config_stage<F: FnMut(&str)>(
    target: &Path,
    config: &Path,
    on_stage: &mut F,
) -> Result<Outcome, String> {
    let Some(result) = run_bridge_with_config_stage(target, config, on_stage)? else {
        return Ok(Outcome::default());
    };
    let bootstrap = result
        .get("bootstrap")
        .and_then(Value::as_bool)
        .ok_or_else(|| {
            crate::locale::tr(
                "同步结果无效，目标账号没有打开。",
                "Invalid sync result. The target account was not opened.",
            )
            .to_owned()
        })?;
    Ok(Outcome { bootstrap })
}

/// Invoke the reviewed Python bridge and return its final JSON result. `None`
/// means the installation explicitly disabled the hook, which is a valid
/// no-op for account launching but an error for a manual sync request.
fn run_bridge_with_config_stage<F: FnMut(&str)>(
    target: &Path,
    config: &Path,
    on_stage: &mut F,
) -> Result<Option<Value>, String> {
    checked_file(config)?;
    let value: Value = serde_json::from_slice(&std::fs::read(config).map_err(|_| {
        crate::locale::tr("无法读取同步配置", "Could not read the sync configuration.")
    })?)
    .map_err(|_| crate::locale::tr("同步配置无效", "Invalid sync configuration."))?;
    if value.get("hook_enabled") == Some(&Value::Bool(false)) {
        return Ok(None);
    }
    if value.get("hook_enabled") != Some(&Value::Bool(true)) {
        return Err(crate::locale::tr(
            "同步配置未启用，已停止打开账号",
            "Sync is not enabled. Account launch stopped.",
        )
        .into());
    }
    let file = |key: &str| -> Result<PathBuf, String> {
        let path = value
            .get(key)
            .and_then(Value::as_str)
            .map(PathBuf::from)
            .ok_or(crate::locale::tr(
                "同步配置缺少文件路径",
                "A file path is missing from the sync configuration.",
            ))?;
        checked_file_with_limit(
            &path,
            if key == "python_path" {
                100 * 1024 * 1024
            } else {
                1024 * 1024
            },
        )?;
        Ok(path)
    };
    let python = file("python_path")?;
    let bridge = file("bridge_path")?;
    let mut child = Command::new(python)
        .arg(bridge)
        .args(["--config"])
        .arg(config)
        .arg("--target")
        .arg(target)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("CASE_PROGRESS", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| {
            crate::locale::tr(
                "无法启动本地同步，已停止打开账号",
                "Could not start local sync. Account launch stopped.",
            )
        })?;
    let stages = stage_reader(&mut child);
    let start = Instant::now();
    loop {
        drain_stages(&stages, on_stage);
        match child.try_wait() {
            Ok(Some(status)) => {
                drain_stages(&stages, on_stage);
                let output = read_stdout(&mut child)?;
                let result: Value = serde_json::from_slice(&output).map_err(|_| {
                    crate::locale::tr(
                        "同步结果无效；请检查同步状态后重试",
                        "Invalid sync result. Review the sync state before trying again.",
                    )
                })?;
                if status.success() {
                    let valid = matches!(
                        result.get("status").and_then(Value::as_str),
                        Some("completed" | "unchanged")
                    );
                    if !valid {
                        return Err(crate::locale::tr(
                            "同步结果无效；请检查同步状态后重试",
                            "Invalid sync result. Review the sync state before trying again.",
                        )
                        .into());
                    }
                    return Ok(Some(result));
                }
                let code = result
                    .get("code")
                    .and_then(Value::as_str)
                    .unwrap_or("SYNC_FAILED");
                let detail = match code {
                    "CLAUDE_STILL_RUNNING" | "CLAUDE_MUST_BE_FULLY_CLOSED" => crate::locale::tr(
                        "Claude 仍在运行。请完成任务并正常退出后重试。",
                        "Claude is still running. Finish your work and quit normally before trying again.",
                    ),
                    "CLAUDE_QUIT_REQUEST_FAILED" => crate::locale::tr(
                        "无法请求 Claude 正常退出。请手动退出后重试。",
                        "Could not request a normal Claude quit. Quit it manually and try again.",
                    ),
                    "RECOVERY_REQUIRED" => crate::locale::tr(
                        "存在未完成的同步，需要先按快照恢复。",
                        "An unfinished sync requires recovery from its snapshot first.",
                    ),
                    "MULTIPLE_PROFILE_PARTITIONS" => crate::locale::tr(
                        "一个登录目录内发现多个账号或组织，需要先明确同步范围。",
                        "Multiple accounts or organizations were found in one profile. Specify the sync scope first.",
                    ),
                    "PROFILE_SCOPE_CHANGED_RETRY" => crate::locale::tr(
                        "登录目录刚完成初始化，请重试打开账号。",
                        "The profile has just been initialized. Try opening it again.",
                    ),
                    _ => crate::locale::tr(
                        "本地会话同步未通过，目标账号没有打开。请检查同步记录。",
                        "Local session sync failed. The target account was not opened. Review the sync log.",
                    ),
                };
                let safe_code = if code
                    .bytes()
                    .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_')
                    && code.len() <= 80
                {
                    code
                } else {
                    "SYNC_FAILED"
                };
                return Err(format!("{detail}\n{safe_code}"));
            }
            Ok(None) if start.elapsed() < Duration::from_secs(75) => {
                std::thread::sleep(Duration::from_millis(100))
            }
            _ => {
                let _ = child.kill(); // Only our hook child; never a Claude process.
                let _ = child.wait();
                return Err(crate::locale::tr("同步超时，已停止打开账号；请检查是否需要按快照恢复。", "Sync timed out. Account launch stopped. Check whether snapshot recovery is required.").into());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_relative_hook_path_is_rejected() {
        assert!(checked_file(Path::new("relative.json")).is_err());
    }

    #[test]
    fn a_missing_hook_file_is_rejected() {
        assert!(checked_file(&std::env::temp_dir().join("case-missing-hook-62a224bb")).is_err());
    }

    #[cfg(target_os = "macos")]
    fn hook_fixture(body: &str) -> Result<Outcome, String> {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("case-hook-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&dir).unwrap();
        let script = dir.join("bridge.py");
        std::fs::write(&script, body).unwrap();
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o600)).unwrap();
        let config = dir.join("hook.json");
        std::fs::write(
            &config,
            serde_json::to_vec(&serde_json::json!({
                "hook_enabled": true, "python_path": "/usr/bin/python3", "bridge_path": script
            }))
            .unwrap(),
        )
        .unwrap();
        std::fs::set_permissions(&config, std::fs::Permissions::from_mode(0o600)).unwrap();
        let result = before_launch_with_config(&dir, &config);
        std::fs::remove_dir_all(dir).unwrap();
        result
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn successful_bootstrap_is_reported_to_the_launcher() {
        assert!(
            hook_fixture("print('{\"status\":\"unchanged\",\"bootstrap\":true}')")
                .unwrap()
                .bootstrap
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn failed_sync_blocks_the_launcher_with_a_safe_code() {
        let result = hook_fixture(
            "import sys\nprint('{\"status\":\"blocked\",\"code\":\"CLAUDE_STILL_RUNNING\"}')\nsys.exit(2)",
        );
        assert!(result.err().unwrap().contains("CLAUDE_STILL_RUNNING"));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn malformed_or_incomplete_results_block_the_launcher() {
        assert!(hook_fixture("print('not JSON')").is_err());
        assert!(hook_fixture("print('{\"status\":\"blocked\"}')").is_err());
    }
}
