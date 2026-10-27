//! Account-scoped queries through the unmodified official Claude Code CLI.
//! CASE stores binding metadata only. Claude owns all authentication material.
use crate::{native_usage, store, usage};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{LazyLock, Mutex};

const HELPER: &str = include_str!("../tools/claude_usage_probe.py");
const AUTH_BROWSER: &str = include_str!("../tools/claude_auth_browser.cjs");
const ELECTRON_VERSION: &str = "42.4.0";
static VERSION: AtomicU64 = AtomicU64::new(1);
fn version() -> u64 {
    VERSION.fetch_add(1, Ordering::Relaxed)
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct Binding {
    profile: PathBuf,
    account_hash: String,
    organization_hash: String,
    revision: String,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    pub version: u64,
    pub configured: bool,
    pub status: String,
    pub code: Option<String>,
    pub usage: Option<usage::Usage>,
    pub last_attempt_at: Option<i64>,
}

impl Snapshot {
    fn empty(configured: bool, code: Option<&str>) -> Self {
        Self {
            version: version(),
            configured,
            status: "unavailable".into(),
            code: code.map(Into::into),
            usage: None,
            last_attempt_at: None,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
struct Scope {
    profile: PathBuf,
    identity: Option<(String, String)>,
    binding: Option<Binding>,
    invalid: bool,
}

impl Scope {
    fn matches(&self) -> bool {
        self.binding.as_ref().is_some_and(|b| {
            b.profile == self.profile
                && self.identity.as_ref()
                    == Some(&(b.account_hash.clone(), b.organization_hash.clone()))
        })
    }
}

struct Entry {
    scope: Scope,
    epoch: u64,
    busy: bool,
    view: Snapshot,
}

#[derive(Default)]
struct Tracker {
    entries: HashMap<String, Entry>,
    epoch: u64,
}

impl Tracker {
    fn entry(&mut self, id: &str, scope: Scope) -> &mut Entry {
        if self.entries.get(id).is_none_or(|e| e.scope != scope) {
            self.epoch += 1;
            let mut view = if scope.invalid {
                Snapshot::empty(true, Some("MONITOR_CONFIG_INVALID"))
            } else if scope.binding.is_some() {
                let mut view = Snapshot::empty(true, Some("QUERY_NOT_RUN"));
                if !scope.matches() {
                    view.status = "auth_failed".into();
                    view.code = Some("CLI_IDENTITY_MISMATCH".into());
                }
                view
            } else {
                Snapshot::empty(false, None)
            };
            if scope.binding.is_none() && !scope.invalid {
                view.usage = native_usage::read(id, &scope.profile);
                if view.usage.is_some() {
                    view.status = "success".into();
                }
            }
            self.entries.insert(
                id.into(),
                Entry {
                    scope,
                    epoch: self.epoch,
                    busy: false,
                    view,
                },
            );
        }
        self.entries.get_mut(id).expect("inserted entry")
    }

    fn begin(&mut self, id: &str, scope: Scope, now: i64) -> Option<u64> {
        let e = self.entry(id, scope);
        if !e.scope.matches() || e.busy {
            return None;
        }
        e.busy = true;
        e.view.version = version();
        e.view.usage = None;
        e.view.status = "in_progress".into();
        e.view.code = None;
        e.view.last_attempt_at = Some(now);
        Some(e.epoch)
    }

    fn begin_scheduled(&mut self, id: &str, scope: Scope, now: i64, interval: i64) -> Option<u64> {
        // Scheduled work must never fall back to opening/clicking a Desktop panel.
        if scope.invalid || !scope.matches() {
            return None;
        }
        let e = self.entry(id, scope.clone());
        if e.busy
            || e.view.status == "auth_failed"
            || matches!(
                e.view.code.as_deref(),
                Some("CLI_SETUP_REQUIRED" | "CLI_WORKSPACE_TRUST_REQUIRED")
            )
            || e.view
                .last_attempt_at
                .is_some_and(|at| at > now.saturating_sub(interval))
        {
            return None;
        }
        self.begin(id, scope, now)
    }

    fn finish(&mut self, id: &str, epoch: u64, result: ProbeResult, now: i64) {
        let Some(e) = self.entries.get_mut(id) else {
            return;
        };
        if e.epoch != epoch || !e.busy {
            return;
        }
        e.busy = false;
        e.view.version = version();
        let valid = result.ok
            && result.status == "success"
            && result.usage.as_ref().is_some_and(|u| {
                u.source == "claude-cli"
                    && u.at > 0
                    && u.at <= now
                    && u.fh.is_some()
                    && u.fh.is_none_or(|p| p <= 100)
                    && u.sd.is_none_or(|p| p <= 100)
            });
        if valid {
            e.view.status = "success".into();
            e.view.code = None;
            e.view.usage = result.usage;
        } else {
            e.view.usage = None;
            e.view.status = match result.status.as_str() {
                "auth_failed" | "unavailable" | "failed" | "timeout" => result.status,
                _ => "failed".into(),
            };
            let code = result.code.unwrap_or_else(|| "CLI_PROBE_FAILED".into());
            e.view.code = Some(code);
        }
    }
}

static TRACKER: LazyLock<Mutex<Tracker>> = LazyLock::new(Default::default);

fn hash(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

fn root(id: &str) -> PathBuf {
    store::conf_dir().join("usage-monitor").join(hash(id))
}

fn safe_path(path: &Path) -> bool {
    path.ancestors().all(|p| {
        std::fs::symlink_metadata(p).is_ok_and(|m| !crate::platform::runtime::is_redirected(&m))
            || !p.exists()
                && std::fs::symlink_metadata(p)
                    .is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound)
    })
}

fn binding(id: &str) -> Result<Option<Binding>, ()> {
    let file = root(id).join("binding.json");
    if !safe_path(&file) {
        return Err(());
    }
    match std::fs::metadata(&file) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Ok(m) if m.is_file() && m.len() <= 8192 => {
            serde_json::from_slice(&std::fs::read(file).map_err(|_| ())?)
                .map(Some)
                .map_err(|_| ())
        }
        _ => Err(()),
    }
}

fn scope(account: &store::Account) -> Scope {
    let binding = binding(&account.id);
    Scope {
        profile: account.dir.clone(),
        identity: usage::identity(&account.dir).map(|i| (hash(&i.account), hash(&i.organization))),
        invalid: binding.is_err(),
        binding: binding.ok().flatten(),
    }
}

pub fn snapshot(account: &store::Account) -> Snapshot {
    TRACKER
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .entry(&account.id, scope(account))
        .view
        .clone()
}

/// Manual Desktop/history reads share the same current-state authority. Status
/// polling can therefore never refill a failed attempt from the durable cache.
pub fn record_manual(
    account: &store::Account,
    status: &str,
    code: Option<&str>,
    value: Option<usage::Usage>,
) -> Snapshot {
    let mut tracker = TRACKER.lock().unwrap_or_else(|e| e.into_inner());
    let e = tracker.entry(&account.id, scope(account));
    if !e.view.configured {
        e.view.version = version();
        e.view.status = status.into();
        e.view.code = code.map(Into::into);
        e.view.usage = if status == "success" { value } else { None };
        e.view.last_attempt_at = Some(crate::now_ms());
    }
    e.view.clone()
}

fn private_write(path: &Path, data: &[u8]) -> Result<(), &'static str> {
    use std::io::Write;
    if !safe_path(path) {
        return Err("MONITOR_CONFIG_INVALID");
    }
    let parent = path.parent().ok_or("SAVE_FAILED")?;
    std::fs::create_dir_all(parent).map_err(|_| "SAVE_FAILED")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o700))
            .map_err(|_| "SAVE_FAILED")?;
    }
    let temp = parent.join(format!(".{}.tmp", uuid::Uuid::new_v4()));
    let result = (|| -> std::io::Result<()> {
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut out = opts.open(&temp)?;
        out.write_all(data)?;
        out.sync_all()?;
        std::fs::rename(&temp, path)
    })();
    let _ = std::fs::remove_file(temp);
    result.map_err(|_| "SAVE_FAILED")
}

fn cli_binary() -> Option<PathBuf> {
    let name = if cfg!(windows) {
        "claude.exe"
    } else {
        "claude"
    };
    let local = dirs::home_dir()?.join(".local/bin").join(name);
    crate::platform::runtime::executable(
        &[
            local.to_str()?,
            "/opt/homebrew/bin/claude",
            "/usr/local/bin/claude",
        ],
        name,
    )
}

fn payload(account: &store::Account, binding: &Binding) -> Result<serde_json::Value, &'static str> {
    let binary = cli_binary().ok_or("CLI_NOT_INSTALLED")?;
    let python = crate::platform::runtime::python().ok_or("PYTHON_NOT_INSTALLED")?;
    let base = root(&account.id);
    if !safe_path(&base) {
        return Err("CLI_SCOPE_INVALID");
    }
    let base = crate::platform::runtime::canonical(&base).ok_or("CLI_SCOPE_INVALID")?;
    Ok(serde_json::json!({
        "binary": binary, "config": base.join("cli"), "workspace": base.join("workspace"),
        "accountHash": binding.account_hash, "organizationHash": binding.organization_hash,
        "label": account.name, "python": python,
    }))
}

fn electron_binary() -> Result<PathBuf, &'static str> {
    let executable = if cfg!(target_os = "macos") {
        "Electron.app/Contents/MacOS/Electron"
    } else if cfg!(windows) {
        "electron.exe"
    } else {
        "electron"
    };
    let path = store::conf_dir()
        .join("auth-runtime")
        .join(ELECTRON_VERSION)
        .join(executable);
    if !safe_path(&path) || !path.is_file() {
        return Err("AUTH_BROWSER_NOT_INSTALLED");
    }
    crate::platform::runtime::executable(
        &[path.to_str().ok_or("AUTH_BROWSER_NOT_INSTALLED")?],
        "case-no-fallback-electron",
    )
    .ok_or("AUTH_BROWSER_NOT_INSTALLED")
}

fn open_terminal(
    account: &store::Account,
    binding: &Binding,
    operation: &str,
) -> Result<(), &'static str> {
    let mut data = payload(account, binding)?;
    let base =
        crate::platform::runtime::canonical(&root(&account.id)).ok_or("CLI_SCOPE_INVALID")?;
    let python = crate::platform::runtime::python().ok_or("PYTHON_NOT_INSTALLED")?;
    private_write(&base.join("probe.py"), HELPER.as_bytes())?;
    #[cfg(unix)]
    let quote = |s: &str| format!("'{}'", s.replace('\'', "'\\''"));
    if operation == "login" {
        let binary = electron_binary()?;
        let browser_script = base.join("auth-browser.cjs");
        let opener = base.join(if cfg!(windows) {
            "browser.cmd"
        } else {
            "browser.command"
        });
        private_write(&browser_script, AUTH_BROWSER.as_bytes())?;
        #[cfg(unix)]
        private_write(
            &opener,
            format!(
                "#!/bin/sh\nexec {} -I {} browser \"$@\"\n",
                quote(&python.to_string_lossy()),
                quote(&base.join("probe.py").to_string_lossy())
            )
            .as_bytes(),
        )?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&opener, std::fs::Permissions::from_mode(0o700))
                .map_err(|_| "SAVE_FAILED")?;
        }
        #[cfg(windows)]
        private_write(
            &opener,
            format!(
                "@echo off\r\n\"{}\" -I \"{}\" browser \"%~1\"\r\n",
                python.to_string_lossy().replace('%', "%%"),
                base.join("probe.py").to_string_lossy().replace('%', "%%")
            )
            .as_bytes(),
        )?;
        data["browser"] = serde_json::json!({
            "binary": binary, "script": browser_script, "opener": opener,
            "userData": base.join("browser"), "label": account.name,
        });
    }
    #[cfg(windows)]
    {
        // Data lives in a JSON file so account names cannot become cmd code.
        let payload_file = base.join(format!("{operation}.json"));
        private_write(&payload_file, data.to_string().as_bytes())?;
        let script_path = base.join(format!("{operation}.cmd"));
        let q = |p: &Path| p.to_string_lossy().replace('%', "%%");
        private_write(&script_path, format!("@echo off\r\n\"{}\" -I \"{}\" {} --payload-file \"{}\"\r\nif errorlevel 1 pause\r\n", q(&python), q(&base.join("probe.py")), operation, q(&payload_file)).as_bytes())?;
        // ShellExecute opens a visible console; the child never inherits a
        // developer shell's keys, injected settings or default account.
        let result = crate::platform::run(
            "powershell.exe",
            &[
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                &format!(
                    "Start-Process -FilePath {}",
                    crate::platform::pq(&script_path.to_string_lossy())
                ),
            ],
            crate::platform::secs(10),
        );
        if result.ok {
            Ok(())
        } else {
            Err("CLI_TERMINAL_FAILED")
        }
    }
    #[cfg(unix)]
    {
        let script = format!(
            "#!/bin/sh\n{} -I {} {} {}\n",
            quote(&python.to_string_lossy()),
            quote(&base.join("probe.py").to_string_lossy()),
            operation,
            quote(&data.to_string())
        );
        let script_path = base.join(if operation == "login" {
            "connect.command"
        } else {
            "setup.command"
        });
        private_write(&script_path, script.as_bytes())?;
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&script_path, std::fs::Permissions::from_mode(0o700))
            .map_err(|_| "SAVE_FAILED")?;
        #[cfg(target_os = "macos")]
        let result = std::process::Command::new("/usr/bin/open")
            .args(["-a", "Terminal"])
            .arg(script_path)
            .status()
            .map_err(|_| "CLI_TERMINAL_FAILED")?;
        #[cfg(target_os = "linux")]
        let result = std::process::Command::new("x-terminal-emulator")
            .arg("-e")
            .arg(&script_path)
            .spawn()
            .map(|_| ())
            .map_err(|_| "CLI_TERMINAL_FAILED");
        #[cfg(target_os = "linux")]
        {
            result
        }
        #[cfg(target_os = "macos")]
        {
            if !result.success() {
                return Err("CLI_TERMINAL_FAILED");
            }
            Ok(())
        }
    }
}

/// Open the account's CLI with its existing identity-bound official login.
/// The same entry point also lets the user complete first-run prompts.
/// This path neither replaces the binding nor starts another auth login.
pub fn setup(account: &store::Account) -> Result<Snapshot, &'static str> {
    {
        let current = scope(account);
        if current.invalid {
            return Err("MONITOR_CONFIG_INVALID");
        }
        if !current.matches() {
            return Err("CLI_IDENTITY_MISMATCH");
        }
        open_terminal(
            account,
            current
                .binding
                .as_ref()
                .expect("matching scope has binding"),
            "setup",
        )?;
        Ok(snapshot(account))
    }
}

pub fn connect(account: &store::Account) -> Result<Snapshot, &'static str> {
    {
        // Detect dependencies before creating or replacing any binding.
        cli_binary().ok_or("CLI_NOT_INSTALLED")?;
        electron_binary()?;
        crate::platform::runtime::python().ok_or("PYTHON_NOT_INSTALLED")?;
        let identity = usage::identity(&account.dir).ok_or("DESKTOP_IDENTITY_MISSING")?;
        let base = root(&account.id);
        for name in ["cli", "workspace"] {
            let dir = base.join(name);
            private_write(&dir.join(".case-scope"), b"CASE official CLI usage scope\n")?;
        }
        let b = Binding {
            profile: account.dir.clone(),
            account_hash: hash(&identity.account),
            organization_hash: hash(&identity.organization),
            revision: uuid::Uuid::new_v4().to_string(),
        };
        private_write(
            &base.join("binding.json"),
            &serde_json::to_vec(&b).map_err(|_| "SAVE_FAILED")?,
        )?;
        open_terminal(account, &b, "login")?;
        Ok(snapshot(account))
    }
}

#[derive(Deserialize)]
struct ProbeResult {
    ok: bool,
    status: String,
    #[serde(default)]
    code: Option<String>,
    usage: Option<usage::Usage>,
}

impl ProbeResult {
    fn error(code: &str) -> Self {
        Self {
            ok: false,
            status: if code == "REFRESH_TIMEOUT" {
                "timeout"
            } else if code == "CLI_PROBE_FAILED" {
                "failed"
            } else {
                "unavailable"
            }
            .into(),
            code: Some(code.into()),
            usage: None,
        }
    }
}

fn execute(account: &store::Account, b: &Binding) -> ProbeResult {
    use std::io::Read;
    #[cfg(unix)]
    use std::os::unix::process::CommandExt;
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};
    let data = match payload(account, b) {
        Ok(v) => v,
        Err(code) => return ProbeResult::error(code),
    };
    let Some(python) = crate::platform::runtime::python() else {
        return ProbeResult::error("PYTHON_NOT_INSTALLED");
    };
    let mut command = Command::new(python);
    command
        .args(["-I", "-c", HELPER, "probe", &data.to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(unix)]
    command.process_group(0);
    crate::platform::hide_console(&mut command);
    let mut child = match command.spawn() {
        Ok(v) => v,
        Err(_) => return ProbeResult::error("CLI_PROBE_FAILED"),
    };
    // Every descendant of this helper stays in our new process group. Always
    // reap it, including after a normal exit or inherited-pipe timeout. Never
    // inspect or signal an existing Claude process.
    #[cfg(unix)]
    let pid = child.id() as i32;
    let stdout = child.stdout.take().expect("piped stdout");
    let (sender, receiver) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = stdout.take(65_537).read_to_end(&mut bytes);
        let _ = sender.send((result, bytes));
    });
    let deadline = Instant::now() + Duration::from_secs(55);
    let (mut timed_out, mut success) = (false, false);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                success = status.success();
                break;
            }
            Err(_) => break,
            _ if Instant::now() >= deadline => {
                timed_out = true;
                break;
            }
            _ => std::thread::sleep(Duration::from_millis(100)),
        }
    }
    // SAFETY: pid names only the process group created immediately above.
    #[cfg(unix)]
    unsafe {
        libc::kill(-pid, libc::SIGKILL);
    }
    #[cfg(windows)]
    {
        let _ = child.kill();
    } // Python's private Job Object owns only this probe tree.
    let _ = child.wait();
    let Ok((Ok(_), bytes)) = receiver.recv_timeout(Duration::from_secs(1)) else {
        return ProbeResult::error("CLI_PROBE_FAILED");
    };
    if timed_out {
        return ProbeResult::error("REFRESH_TIMEOUT");
    }
    if !success || bytes.len() > 65_536 {
        return ProbeResult::error("CLI_PROBE_FAILED");
    }
    serde_json::from_slice(&bytes).unwrap_or_else(|_| ProbeResult::error("CLI_PROBE_FAILED"))
}

/// Explicit user query. Status polling and opening a page never invoke this.
pub fn refresh(account: &store::Account) -> Snapshot {
    let current = scope(account);
    let ticket = TRACKER.lock().unwrap_or_else(|e| e.into_inner()).begin(
        &account.id,
        current.clone(),
        crate::now_ms(),
    );
    let Some(epoch) = ticket else {
        return snapshot(account);
    };
    finish_refresh(account, current, epoch)
}

/// The sole backend scheduler skips unconfigured, invalid, busy, recently
/// attempted or blocked accounts. It never authorizes or opens a terminal.
pub fn refresh_scheduled(account: &store::Account, interval: i64) -> Option<Snapshot> {
    let current = scope(account);
    let epoch = TRACKER
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .begin_scheduled(&account.id, current.clone(), crate::now_ms(), interval)?;
    Some(finish_refresh(account, current, epoch))
}

fn finish_refresh(account: &store::Account, current: Scope, epoch: u64) -> Snapshot {
    let result = execute(
        account,
        current.binding.as_ref().expect("begin needs binding"),
    );
    // Deletion, reauthorization and Desktop identity changes all
    // invalidate late results before publication.
    let still_exists = store::accounts()
        .unwrap_or_default()
        .iter()
        .any(|a| a.id == account.id && a.dir == account.dir);
    let mut tracker = TRACKER.lock().unwrap_or_else(|e| e.into_inner());
    if still_exists {
        tracker.entry(&account.id, scope(account));
        tracker.finish(&account.id, epoch, result, crate::now_ms());
        tracker.entries[&account.id].view.clone()
    } else {
        tracker.entries.remove(&account.id);
        let mut missing = Snapshot::empty(false, Some("ACCOUNT_NOT_FOUND"));
        missing.status = "failed".into();
        missing
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scope(name: &str) -> Scope {
        Scope {
            profile: format!("/synthetic/{name}").into(),
            identity: Some((name.into(), "org".into())),
            invalid: false,
            binding: Some(Binding {
                profile: format!("/synthetic/{name}").into(),
                account_hash: name.into(),
                organization_hash: "org".into(),
                revision: "1".into(),
            }),
        }
    }

    fn good() -> ProbeResult {
        ProbeResult {
            ok: true,
            status: "success".into(),
            code: None,
            usage: Some(usage::Usage {
                at: 1000,
                age: 0,
                fh: Some(21),
                sd: Some(45),
                source: "claude-cli".into(),
                fh_reset_at: None,
                sd_reset_at: None,
                fh_reset_estimated: false,
                sd_reset_estimated: false,
                fh_reset_text: None,
                sd_reset_text: None,
            }),
        }
    }

    #[test]
    fn three_closed_profiles_query_independently_and_never_reenter() {
        let mut t = Tracker::default();
        for id in ["a", "b", "c"] {
            let ticket = t.begin(id, scope(id), 1000).unwrap();
            assert!(t.begin(id, scope(id), 1001).is_none());
            t.finish(id, ticket, good(), 1002);
            assert_eq!(
                t.entry(id, scope(id)).view.usage.as_ref().unwrap().fh,
                Some(21)
            );
            assert!(t.begin(id, scope(id), 1003).is_some());
        }
        assert_eq!(t.entries.len(), 3);
    }

    #[test]
    fn scheduled_queries_skip_unbound_mismatched_and_busy_profiles() {
        let mut t = Tracker::default();
        let mut unbound = scope("a");
        unbound.binding = None;
        assert!(t.begin_scheduled("a", unbound, 1000, 300_000).is_none());
        let mut mismatched = scope("a");
        mismatched.identity = Some(("other".into(), "org".into()));
        assert!(t.begin_scheduled("a", mismatched, 1000, 300_000).is_none());
        assert!(t.entries.is_empty(), "no native cache or panel fallback");
        let ticket = t.begin("a", scope("a"), 1000).unwrap();
        assert!(t.begin_scheduled("a", scope("a"), 1001, 300_000).is_none());
        assert!(t.entries["a"].busy);
        t.finish("a", ticket, good(), 1002);
        assert_eq!(t.entries["a"].view.status, "success");
    }

    #[test]
    fn scheduled_queries_skip_recent_manual_attempts_without_clearing_them() {
        let mut t = Tracker::default();
        let ticket = t.begin("a", scope("a"), 1000).unwrap();
        t.finish("a", ticket, good(), 1002);
        assert!(
            t.begin_scheduled("a", scope("a"), 300_999, 300_000)
                .is_none()
        );
        assert!(t.entries["a"].view.usage.is_some());
        assert!(
            t.begin_scheduled("a", scope("a"), 301_000, 300_000)
                .is_some()
        );
        assert!(t.entries["a"].view.usage.is_none());
        assert_eq!(t.entries["a"].view.status, "in_progress");
    }

    #[test]
    fn auth_and_setup_failures_require_manual_action_before_scheduling_resumes() {
        for code in [
            "CLI_AUTH_REQUIRED",
            "CLI_SETUP_REQUIRED",
            "CLI_WORKSPACE_TRUST_REQUIRED",
        ] {
            let mut t = Tracker::default();
            let ticket = t.begin("a", scope("a"), 1000).unwrap();
            let mut failure = ProbeResult::error(code);
            if code == "CLI_AUTH_REQUIRED" {
                failure.status = "auth_failed".into();
            }
            t.finish("a", ticket, failure, 1002);
            assert!(
                t.begin_scheduled("a", scope("a"), 1_000_000, 300_000)
                    .is_none()
            );
            // Explicit retry still works after the user finishes the required setup.
            let manual = t.begin("a", scope("a"), 1_000_001).unwrap();
            t.finish("a", manual, good(), 1_000_002);
            assert!(
                t.begin_scheduled("a", scope("a"), 1_300_001, 300_000)
                    .is_some()
            );
        }
    }

    #[test]
    fn scheduled_failure_keeps_usage_null_without_immediate_retry() {
        let mut t = Tracker::default();
        let ticket = t.begin_scheduled("a", scope("a"), 1000, 300_000).unwrap();
        t.finish("a", ticket, ProbeResult::error("REFRESH_TIMEOUT"), 1002);
        assert_eq!(t.entries["a"].view.status, "timeout");
        assert!(t.entries["a"].view.usage.is_none());
        assert!(t.begin_scheduled("a", scope("a"), 1003, 300_000).is_none());
        assert!(
            t.begin_scheduled("a", scope("a"), 301_000, 300_000)
                .is_some()
        );
    }

    #[test]
    fn failure_remains_null_across_status_reads_until_another_click() {
        let mut t = Tracker::default();
        let first = t.begin("a", scope("a"), 1000).unwrap();
        t.finish("a", first, good(), 1002);
        let second = t.begin("a", scope("a"), 301_002).unwrap();
        t.finish("a", second, ProbeResult::error("REFRESH_TIMEOUT"), 301_003);
        for _ in 0..3 {
            let e = t.entry("a", scope("a"));
            assert!(e.view.usage.is_none());
            assert_eq!(e.view.status, "timeout");
        }
        let third = t.begin("a", scope("a"), 301_005).unwrap();
        t.finish("a", third, good(), 301_006);
        assert_eq!(t.entries["a"].view.status, "success");
    }

    #[test]
    fn reconnect_org_and_profile_changes_discard_late_results() {
        for variant in 0..3 {
            let mut t = Tracker::default();
            let ticket = t.begin("a", scope("a"), 1000).unwrap();
            let mut changed = scope("a");
            match variant {
                0 => changed.binding.as_mut().unwrap().revision = "2".into(),
                1 => changed.identity.as_mut().unwrap().1 = "other-org".into(),
                _ => changed.profile = "/synthetic/different".into(),
            }
            t.entry("a", changed);
            t.finish("a", ticket, good(), 1002);
            assert!(t.entries["a"].view.usage.is_none());
        }
    }

    #[test]
    fn missing_identity_and_malformed_results_never_publish_percentages() {
        let mut t = Tracker::default();
        let mut missing = scope("a");
        missing.identity = None;
        assert!(t.begin("a", missing, 1000).is_none());
        assert_eq!(t.entries["a"].view.status, "auth_failed");
        let ticket = t.begin("a", scope("a"), 1001).unwrap();
        let mut result = good();
        result.usage.as_mut().unwrap().fh = Some(101);
        t.finish("a", ticket, result, 1002);
        assert!(t.entries["a"].view.usage.is_none());
        assert_eq!(t.entries["a"].view.status, "failed");
    }

    #[test]
    fn rate_limiting_never_changes_another_account() {
        let mut t = Tracker::default();
        let a = t.begin("a", scope("a"), 1000).unwrap();
        let b = t.begin("b", scope("b"), 1000).unwrap();
        t.finish("a", a, ProbeResult::error("CLI_RATE_LIMITED"), 1002);
        t.finish("b", b, good(), 1002);
        assert_eq!(
            t.entries["a"].view.code.as_deref(),
            Some("CLI_RATE_LIMITED")
        );
        assert!(t.entries["a"].view.usage.is_none());
        assert_eq!(t.entries["b"].view.status, "success");
    }
}
