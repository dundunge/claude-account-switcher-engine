//! CASE — Claude Account Switcher Engine.
//!
//! A profile directory *is* an account: point Claude Desktop's userData at one
//! and the signed-in account lives there. Which knob does the pointing has
//! changed with Claude's builds — platform::launch_claude carries the current
//! answer. There is no single-instance lock in Claude, so profiles run side by
//! side.

// No console window behind a release build on Windows.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod archive;
mod chrome;
mod health;
mod local_sync;
mod locale;
mod native_usage;
mod platform;
mod store;
mod tray;
mod update;
mod usage;
mod usage_monitor;
mod usage_schedule;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex, MutexGuard};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use store::Account;
use tauri::webview::PageLoadEvent;
use tauri::{
    AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_notification::NotificationExt;

/// The bundle identifier, and on Windows the AppUserModelID toasts are matched by.
pub const APP_ID: &str = "local.launcher.case";

pub fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as i64)
}

/// The profile Claude uses when nothing points it elsewhere — Application
/// Support on a Mac and %APPDATA% on Windows.
fn default_profile() -> PathBuf {
    platform::app_data().join("Claude")
}

fn is_default(a: &Account) -> bool {
    a.dir == default_profile()
}

// ---------------------------------------------------------------- runtime ---

/// What the app knows that is not in accounts.json. Never held across a call
/// into a window, the tray or a plugin: those may wait on the main thread, which
/// may be waiting on this.
#[derive(Default)]
struct Runtime {
    /// The accelerator actually registered, which is not the setting: a shortcut
    /// another app owns stays in the settings but does nothing.
    hotkey: Option<String>,
    last_update: Option<update::Check>,
    seen_issues: HashSet<String>,
    issue_count: usize,
    /// Launched at login, the point is to be ready for the hotkey, not to appear.
    start_hidden: bool,
}

static RT: LazyLock<Mutex<Runtime>> = LazyLock::new(Default::default);

fn rt() -> MutexGuard<'static, Runtime> {
    RT.lock().unwrap_or_else(|e| e.into_inner())
}

// ----------------------------------------------------------------- probing ---

#[derive(Serialize)]
struct Status {
    id: String,
    running: bool,
    sessions: usize,
    exists: bool,
    usage: Option<usage::Usage>,
    monitor: usage_monitor::Snapshot,
}

fn session_count(dir: &Path) -> usize {
    let entries = |d: &Path| std::fs::read_dir(d).into_iter().flatten().flatten();
    entries(&dir.join("claude-code-sessions"))
        .flat_map(|acct| entries(&acct.path()).collect::<Vec<_>>())
        .flat_map(|org| entries(&org.path()).collect::<Vec<_>>())
        .filter(|f| {
            let n = f.file_name();
            let n = n.to_string_lossy();
            n.starts_with("local_") && n.ends_with(".json")
        })
        .count()
}

/// Running state, session counts and usage, one thread per account: on a Mac
/// each running check is a handful of lsof calls, the slowest thing this app does.
fn probe(accounts: &[Account]) -> Vec<Status> {
    std::thread::scope(|s| {
        let probes: Vec<_> = accounts
            .iter()
            .map(|a| {
                s.spawn(move || {
                    let monitor = usage_monitor::snapshot(a);
                    Status {
                        id: a.id.clone(),
                        running: platform::is_running(&a.dir),
                        sessions: session_count(&a.dir),
                        exists: a.dir.exists(),
                        usage: monitor.usage.clone(),
                        monitor,
                    }
                })
            })
            .collect();
        probes
            .into_iter()
            .map(|h| h.join().expect("probe thread"))
            .collect()
    })
}

/// Recency order, when it is switched on. The sort is stable, so accounts that
/// have never been opened keep their configured order at the bottom.
fn sorted(accounts: &[Account]) -> Vec<Account> {
    let mut list = accounts.to_vec();
    if store::settings().sort_by_recent {
        list.sort_by_key(|a| std::cmp::Reverse(a.last_used_at.unwrap_or(0)));
    }
    list
}

fn merged(a: &Account, extra: impl Serialize) -> Value {
    let mut v = serde_json::to_value(a).unwrap_or_default();
    if let (Some(o), Value::Object(e)) = (
        v.as_object_mut(),
        serde_json::to_value(extra).unwrap_or_default(),
    ) {
        o.extend(e);
    }
    v
}

/// Config order and flags only — no probing, so it returns immediately.
fn stored_list(accounts: &[Account]) -> Vec<Value> {
    sorted(accounts)
        .iter()
        .map(|a| merged(a, json!({ "isDefault": is_default(a) })))
        .collect()
}

/// The list with everything probed folded in.
fn decorated(accounts: &[Account]) -> Vec<Value> {
    let list = sorted(accounts);
    let status = probe(&list);
    list.iter()
        .zip(status)
        .map(|(a, s)| {
            let mut v = merged(a, s);
            v["isDefault"] = is_default(a).into();
            v
        })
        .collect()
}

fn find(id: &str) -> Option<Account> {
    store::accounts()?.into_iter().find(|a| a.id == id)
}

fn error(msg: impl Into<String>) -> Value {
    json!({ "error": msg.into() })
}

// Claude's own, and not an account: on Windows it keeps the third-party config
// library in %LOCALAPPDATA%\Claude-3p, which the prefix scan below would
// otherwise offer up as an account called "3p".
const NOT_A_PROFILE: &str = "Claude-3p";
pub const MAX_ACCOUNTS: usize = 8;
const DEFAULT_PROFILE_NAME: &str = "Claude";

fn valid_profile_name(name: &str) -> bool {
    name == DEFAULT_PROFILE_NAME || (name.starts_with("Claude-") && name.len() > "Claude-".len())
}

fn valid_profile_dir(parent: &Path, dir: &Path) -> bool {
    dir.parent() == Some(parent)
        && dir
            .file_name()
            .is_some_and(|n| valid_profile_name(&n.to_string_lossy()))
}

/// Profiles present on disk that the config does not know about yet.
fn discover_profiles() -> Vec<Value> {
    let mut found = Vec::new();
    if default_profile().exists() {
        found.push(json!({ "name": "Main", "dir": default_profile(), "isDefault": true }));
    }
    let mut roots = vec![platform::app_data()];
    if !roots.contains(&platform::profile_root()) {
        roots.push(platform::profile_root());
    }
    for root in roots {
        let mut names: Vec<String> = std::fs::read_dir(&root)
            .into_iter()
            .flatten()
            .flatten()
            .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
            .map(|e| e.file_name().to_string_lossy().into_owned())
            // An archived profile is deliberately set aside; offering it back here
            // would undo the archive by accident.
            .filter(|n| {
                n.starts_with("Claude-") && n != NOT_A_PROFILE && !archive::is_archive_path(n)
            })
            .collect();
        names.sort();
        for n in names {
            found.push(
                json!({ "name": &n["Claude-".len()..], "dir": root.join(&n), "isDefault": false }),
            );
        }
    }
    found
}

// ---------------------------------------------------------------- launching ---

/// Stamp an account as just used. Activating a running one counts.
fn touch(id: &str) {
    let _ = store::edit(|c| {
        if let Some(a) = c.accounts_mut().iter_mut().find(|a| a.id == id) {
            a.last_used_at = Some(now_ms());
        }
    });
}

fn launch_account(app: &AppHandle, id: &str) -> Value {
    let Ok(_launch) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一次账号切换正在进行，请稍后重试。",
            "Another account switch is in progress. Try again shortly.",
        ));
    };
    let Some(a) = find(id) else {
        return error(crate::locale::tr("未找到账号。", "Account not found."));
    };
    let stage_app = app.clone();
    let stage_id = id.to_owned();
    let mut stage = |name: &str| {
        let _ = stage_app.emit(
            "account:launch-stage",
            json!({ "id": stage_id, "stage": name }),
        );
    };
    touch(&a.id);
    if platform::is_running(&a.dir) {
        // Two instances on one profile corrupt its LevelDB stores, so this fronts
        // the one already open rather than starting a second.
        platform::activate_profile(&a.dir);
        open_paired_chrome(&a);
        return json!({ "ok": true, "alreadyRunning": true });
    }
    let sync = match local_sync::before_launch_with_stage(&a.dir, &mut stage) {
        Ok(outcome) => outcome,
        Err(message) => {
            stage("error");
            return error(&message);
        }
    };
    stage("launching");
    // Snapshot the session index before handing control to Claude. This is the
    // cheap insurance against the failure mode health.rs exists to catch.
    let _ = health::backup_index(&a, &store::backup_root());
    let _ = std::fs::create_dir_all(&a.dir);
    let launched = platform::launch_claude(&a.dir, is_default(&a));
    open_paired_chrome(&a);
    if launched.is_ok() {
        stage("completed");
    } else {
        stage("error");
    }
    json!({ "ok": launched.is_ok(), "error": launched.err().flatten(), "alreadyRunning": false,
            "syncBootstrap": sync.bootstrap })
}

/// Bring the account's browser context along, if one is paired and enabled.
fn open_paired_chrome(a: &Account) {
    if let Some(c) = a
        .chrome
        .as_ref()
        .filter(|c| c.open_on_launch && !c.dir.is_empty())
    {
        chrome::launch(&c.dir, None);
    }
}

/// A folder name from an account name. Letters outside ASCII are kept rather
/// than folded: both filesystems take them, and folding without a Unicode
/// normaliser would only turn "Café" into "Caf".
fn slug(name: &str) -> String {
    let mut s = String::new();
    for c in name.chars() {
        if c.is_alphanumeric() {
            s.push(c);
        } else if !s.ends_with('-') {
            s.push('-');
        }
    }
    let s: String = s.trim_matches('-').chars().take(40).collect();
    if s.is_empty() { "account".into() } else { s }
}

fn unique_dir(name: &str) -> PathBuf {
    let base = format!("Claude-{}", slug(name));
    let root = platform::profile_root();
    (1..)
        .map(|i| {
            root.join(if i == 1 {
                base.clone()
            } else {
                format!("{base}-{i}")
            })
        })
        .find(|d| !d.exists())
        .expect("a free name")
}

/// A name not already taken, for restoring an archive alongside a namesake.
fn free_name(name: &str, accounts: &[Account]) -> String {
    let taken: HashSet<String> = accounts.iter().map(|a| a.name.to_lowercase()).collect();
    std::iter::once(name.to_owned())
        .chain((2..).map(|i| format!("{name} {i}")))
        .find(|c| !taken.contains(&c.to_lowercase()))
        .expect("a free name")
}

/// Tell both windows and the tray that the account list changed — the settings
/// window restores archives, the launcher adds and removes, and each shows the
/// other's work without being asked.
fn changed(app: &AppHandle) {
    tray::refresh(app);
    let _ = app.emit("accounts:changed", ());
}

/// Deleting asks for the name typed back. Checked here as well as in the page,
/// so nothing short of that — a stray click, a bug in the page — erases a profile.
fn confirmed(typed: Option<&str>, name: &str) -> bool {
    typed.is_some_and(|t| t.trim() == name)
}

// --------------------------------------------------------------- commands ---
//
// Sync functions marked async run on Tauri's thread pool rather than the main
// thread, which is what keeps lsof and du off the UI.

// Fast path: config only, no probing. The window must paint immediately.
#[tauri::command(async)]
fn accounts_list() -> Value {
    let Some(stored) = store::accounts() else {
        // First run: nothing configured. Offer what is already on disk.
        return json!({ "needsSetup": true, "discovered": discover_profiles(), "accounts": [] });
    };
    json!({
        "needsSetup": false,
        "discovered": [],
        "accounts": stored_list(&stored),
        "chromeProfiles": chrome::list_profiles(),
        "archived": archive::with_status(&store::load().archived),
    })
}

// Slow path: running state and session counts, folded in once they resolve.
#[tauri::command(async)]
fn accounts_status() -> Vec<Status> {
    probe(&store::accounts().unwrap_or_default())
}

/// Sync all configured profile indexes without opening a different account.
/// The selected target is only used to validate the CASE profile scope; the
/// bridge merges every configured partition in one transaction.
#[tauri::command(async)]
fn accounts_sync(app: AppHandle) -> Value {
    let Ok(_operation) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一个账号操作正在进行，请稍后重试。",
            "Another account operation is in progress. Try again shortly.",
        ));
    };
    let Some(target) = sorted(&store::accounts().unwrap_or_default())
        .first()
        .cloned()
    else {
        return error(crate::locale::tr(
            "还没有可同步的账号。",
            "There are no accounts to sync yet.",
        ));
    };
    let stage_app = app.clone();
    let mut stage = |name: &str| {
        let _ = stage_app.emit("sync:stage", json!({ "stage": name }));
    };
    match local_sync::sync_now_with_stage(&target.dir, &mut stage) {
        Ok(mut result) => {
            result["ok"] = Value::Bool(true);
            changed(&app);
            result
        }
        Err(message) => {
            stage("error");
            error(message)
        }
    }
}

#[derive(Deserialize)]
struct Picked {
    name: String,
    dir: PathBuf,
}

#[tauri::command(async)]
fn accounts_adopt(app: AppHandle, picked: Vec<Picked>) -> Value {
    let Ok(_operation) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一个账号操作正在进行，请稍后重试。",
            "Another account operation is in progress. Try again shortly.",
        ));
    };
    if picked.len() > MAX_ACCOUNTS {
        return error(crate::l10n!(
            "最多支持 {MAX_ACCOUNTS} 个账号，请先减少选择。",
            "CASE supports at most {MAX_ACCOUNTS} accounts. Remove a selection first."
        ));
    }
    let roots = [platform::app_data(), platform::profile_root()];
    let mut seen_dirs = HashSet::new();
    if picked.iter().any(|p| {
        !seen_dirs.insert(p.dir.clone())
            || !p.dir.is_dir()
            || !roots.iter().any(|root| valid_profile_dir(root, &p.dir))
    }) {
        return error(crate::locale::tr(
            "账号目录必须位于 Claude 目录下，并使用 Claude 或 Claude-* 名称。",
            "Profile folders must be inside Claude's profile root and use the Claude or Claude-* name form.",
        ));
    }
    let accounts: Vec<Account> = picked
        .into_iter()
        .map(|p| store::new_account(&p.name, p.dir))
        .collect();
    match store::edit(|c| c.accounts = Some(accounts.clone())) {
        Ok(()) => {
            changed(&app);
            json!({ "accounts": decorated(&accounts) })
        }
        Err(e) => error(e),
    }
}

#[tauri::command(async)]
fn accounts_add(app: AppHandle, name: String) -> Value {
    let name = name.trim().to_owned();
    if name.is_empty() {
        return error(crate::locale::tr(
            "账号名称不能为空。",
            "The account name cannot be empty.",
        ));
    }
    let Ok(_operation) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一个账号操作正在进行，请稍后重试。",
            "Another account operation is in progress. Try again shortly.",
        ));
    };
    let dir = unique_dir(&name);
    if let Err(e) = std::fs::create_dir_all(&dir) {
        return error(crate::l10n!(
            "无法创建账号目录：{e}",
            "Could not create the profile folder: {e}"
        ));
    }
    let account = store::new_account(&name, dir.clone());
    let added = store::edit(|c| {
        if c.accounts().len() >= MAX_ACCOUNTS {
            return Err(crate::l10n!(
                "最多支持 {MAX_ACCOUNTS} 个账号，请先归档或移除一个账号。",
                "CASE supports at most {MAX_ACCOUNTS} accounts. Archive or remove one first."
            ));
        }
        if c.accounts()
            .iter()
            .any(|a| a.name.to_lowercase() == name.to_lowercase())
        {
            return Err(crate::locale::tr(
                "已存在同名账号。",
                "An account with that name already exists.",
            )
            .to_owned());
        }
        c.accounts_mut().push(account.clone());
        Ok(c.accounts().to_vec())
    });
    match added {
        Ok(Ok(accounts)) => {
            changed(&app);
            json!({ "accounts": decorated(&accounts) })
        }
        Ok(Err(e)) => {
            let _ = std::fs::remove_dir_all(&dir);
            error(e)
        }
        Err(e) => {
            let _ = std::fs::remove_dir_all(&dir);
            error(e)
        }
    }
}

// Name, icon and colour move together, because they are edited together — the
// account sheet always sends all of them.
#[tauri::command(async)]
fn accounts_update(
    app: AppHandle,
    id: String,
    name: String,
    emoji: String,
    icon: String,
    hue: Option<f64>,
) -> Value {
    let name = name.trim().to_owned();
    if name.is_empty() {
        return error(crate::locale::tr(
            "账号名称不能为空。",
            "The account name cannot be empty.",
        ));
    }
    let Ok(_operation) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一个账号操作正在进行，请稍后重试。",
            "Another account operation is in progress. Try again shortly.",
        ));
    };
    let updated = store::edit(|c| {
        if c.accounts()
            .iter()
            .any(|x| x.id != id && x.name.to_lowercase() == name.to_lowercase())
        {
            return Err(crate::locale::tr(
                "已存在同名账号。",
                "An account with that name already exists.",
            ));
        }
        let a = c
            .accounts_mut()
            .iter_mut()
            .find(|x| x.id == id)
            .ok_or(crate::locale::tr("未找到账号。", "Account not found."))?;
        a.name = name;
        // Bounded rather than validated against a list: a ZWJ sequence is many
        // code points, and there is no reason to refuse one the picker does not offer.
        let e: String = emoji.trim().chars().take(16).collect();
        a.emoji = (!e.is_empty()).then_some(e);
        // One of the page's own line icons, by name. Anything else reads as none.
        let i = icon.trim();
        a.icon = (!i.is_empty()
            && i.len() <= 24
            && i.bytes().all(|b| b.is_ascii_lowercase() || b == b'-'))
        .then(|| i.to_owned());
        // None is "derive it from the name".
        a.hue = hue
            .filter(|h| h.is_finite())
            .map(|h| (h.round() as i64).rem_euclid(360));
        Ok(c.accounts().to_vec())
    });
    match updated {
        Ok(Ok(accounts)) => {
            changed(&app);
            // Deliberately not probed: lsof would put two seconds between pressing
            // Save and seeing the new name. The renderer keeps the status it has.
            json!({ "accounts": stored_list(&accounts) })
        }
        Ok(Err(e)) => error(e),
        Err(e) => error(e),
    }
}

#[tauri::command(async)]
fn accounts_remove(
    app: AppHandle,
    id: String,
    delete_data: bool,
    confirm: Option<String>,
) -> Value {
    let Ok(_operation) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一个账号操作正在进行，请稍后重试。",
            "Another account operation is in progress. Try again shortly.",
        ));
    };
    let Some(a) = find(&id) else {
        return error(crate::locale::tr("未找到账号。", "Account not found."));
    };
    if delete_data {
        if is_default(&a) {
            return error(crate::locale::tr(
                "Claude 默认数据目录不能删除。",
                "The default Claude profile cannot be deleted.",
            ));
        }
        if platform::is_running(&a.dir) {
            return error(crate::locale::tr(
                "此账号正在运行，请先正常退出 Claude 再试。",
                "This account is open. Quit Claude normally before trying again.",
            ));
        }
        if !confirmed(confirm.as_deref(), &a.name) {
            return error(crate::locale::tr(
                "请输入账号名称以确认删除。",
                "Type the account name to confirm deletion.",
            ));
        }
    }
    // Stage destructive deletion as an atomic rename first.  The list commit
    // and the final remove can then both roll back to the original directory.
    let staged = if delete_data {
        let metadata = std::fs::symlink_metadata(&a.dir);
        if metadata
            .as_ref()
            .is_ok_and(|m| m.file_type().is_symlink() || m.is_dir())
        {
            let temp = a.dir.with_file_name(format!(
                ".case-delete-{}-{}",
                a.id,
                uuid::Uuid::new_v4().simple()
            ));
            if let Err(e) = std::fs::rename(&a.dir, &temp) {
                return error(crate::l10n!(
                    "无法暂存账号目录：{e}",
                    "Could not stage the profile for deletion: {e}"
                ));
            }
            Some(temp)
        } else if metadata.is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound) {
            None
        } else {
            return error(crate::locale::tr(
                "账号目录类型无效，未删除。",
                "The profile path is not a directory, so it was not deleted.",
            ));
        }
    } else {
        None
    };
    // Commit the list change. If saving fails, put the staged directory back.
    let next = match store::edit(|c| {
        c.accounts_mut().retain(|x| x.id != id);
        c.accounts().to_vec()
    }) {
        Ok(accounts) => accounts,
        Err(e) => {
            let rollback = staged.as_ref().map(|temp| std::fs::rename(temp, &a.dir));
            return error(match rollback {
                Some(Err(r)) => crate::l10n!(
                    "配置保存失败且目录回滚失败：{e}；{r}",
                    "Could not save the configuration, and restoring the profile also failed: {e}; {r}"
                ),
                _ => e,
            });
        }
    };
    if let Some(temp) = staged
        && let Err(e) = std::fs::remove_dir_all(&temp)
    {
        let config_rollback = store::edit(|c| c.accounts_mut().push(a.clone()));
        let path_rollback = std::fs::rename(&temp, &a.dir);
        return error(match (config_rollback, path_rollback) {
            (Err(r), _) => crate::l10n!(
                "无法删除账号目录且列表回滚失败：{e}；{r}",
                "Could not delete the profile, and restoring the account list also failed: {e}; {r}"
            ),
            (Ok(()), Err(r)) => crate::l10n!(
                "无法删除账号目录且目录回滚失败：{e}；{r}",
                "Could not delete the profile, and restoring its folder also failed: {e}; {r}"
            ),
            (Ok(()), Ok(())) => {
                crate::l10n!("无法删除账号目录：{e}", "Could not delete the profile: {e}")
            }
        });
    }
    changed(&app);
    json!({ "accounts": decorated(&next) })
}

#[tauri::command(async)]
fn accounts_size(id: String) -> Value {
    json!({ "bytes": find(&id).and_then(|a| platform::profile_size(&a.dir)) })
}

#[tauri::command(async)]
fn accounts_archive(app: AppHandle, id: String) -> Value {
    let Ok(_operation) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一个账号操作正在进行，请稍后重试。",
            "Another account operation is in progress. Try again shortly.",
        ));
    };
    let Some(a) = find(&id) else {
        return error(crate::locale::tr("未找到账号。", "Account not found."));
    };
    if is_default(&a) {
        return error(crate::locale::tr(
            "Claude 默认数据目录不能归档。",
            "The default Claude profile cannot be archived.",
        ));
    }
    // Renaming a directory out from under a running app is how you corrupt one.
    // Recheck after taking the operation lock so a launch cannot race this test.
    if platform::is_running(&a.dir) {
        return error(crate::locale::tr(
            "此账号正在运行，请先正常退出 Claude 再试。",
            "This account is open. Quit Claude normally before trying again.",
        ));
    }
    // No confirmation: archiving deletes nothing and restores in a click, and the
    // remove sheet already spelled out what it does.
    let record = match archive::archive(&a, platform::profile_size(&a.dir)) {
        Ok(r) => r,
        Err(e) => return error(e),
    };
    let moved_to = record
        .dir
        .file_name()
        .map(|n| n.to_string_lossy().into_owned());
    let (accounts, archived) = match store::edit(|c| {
        c.accounts_mut().retain(|x| x.id != id);
        c.archived.push(record.clone());
        (c.accounts().to_vec(), c.archived.clone())
    }) {
        Ok(value) => value,
        Err(e) => {
            let rollback = std::fs::rename(&record.dir, &record.original_dir);
            return error(if let Err(r) = rollback {
                crate::l10n!(
                    "配置保存失败且目录回滚失败：{e}；{r}",
                    "Could not save the configuration, and moving the profile back also failed: {e}; {r}"
                )
            } else {
                e
            });
        }
    };
    changed(&app);
    // Unprobed for the same reason as the edit path; the renderer polls right after.
    json!({ "accounts": stored_list(&accounts), "archived": archive::with_status(&archived), "movedTo": moved_to })
}

#[tauri::command(async)]
fn archive_restore(app: AppHandle, id: String) -> Value {
    let Ok(_operation) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一个账号操作正在进行，请稍后重试。",
            "Another account operation is in progress. Try again shortly.",
        ));
    };
    let Some(r) = store::load().archived.into_iter().find(|x| x.id == id) else {
        return error(crate::locale::tr(
            "此归档已不在列表中。",
            "That archive is no longer listed.",
        ));
    };
    let dir = match archive::restore(&r) {
        Ok(d) => d,
        Err(e) => return error(e),
    };
    let renamed = (dir != r.original_dir).then(|| dir.clone());
    let (accounts, archived) = match store::edit(|c| {
        // A fresh id: the archived one may since have been reused by a new account.
        let mut a = store::new_account(&free_name(&r.name, c.accounts()), dir.clone());
        (a.emoji, a.icon, a.hue, a.chrome) =
            (r.emoji.clone(), r.icon.clone(), r.hue, r.chrome.clone());
        c.accounts_mut().push(a);
        c.archived.retain(|x| x.id != id);
        (c.accounts().to_vec(), c.archived.clone())
    }) {
        Ok(value) => value,
        Err(e) => {
            let rollback = std::fs::rename(&dir, &r.dir);
            return error(if let Err(r_error) = rollback {
                crate::l10n!(
                    "配置保存失败且归档回滚失败：{e}；{r_error}",
                    "Could not save the configuration, and moving the profile back to the archive also failed: {e}; {r_error}"
                )
            } else {
                e
            });
        }
    };
    changed(&app);
    json!({ "accounts": stored_list(&accounts), "archived": archive::with_status(&archived), "renamed": renamed })
}

#[tauri::command(async)]
fn archive_delete(app: AppHandle, id: String, confirm: Option<String>) -> Value {
    let Ok(_operation) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一个账号操作正在进行，请稍后重试。",
            "Another account operation is in progress. Try again shortly.",
        ));
    };
    let Some(r) = store::load().archived.into_iter().find(|x| x.id == id) else {
        return error(crate::locale::tr(
            "此归档已不在列表中。",
            "That archive is no longer listed.",
        ));
    };
    if !confirmed(confirm.as_deref(), &r.name) {
        return error(crate::locale::tr(
            "请输入账号名称以确认删除。",
            "Type the account name to confirm deletion.",
        ));
    }
    let staged = match std::fs::symlink_metadata(&r.dir) {
        Ok(_) => {
            let temp = r.dir.with_file_name(format!(
                ".case-delete-archive-{}",
                uuid::Uuid::new_v4().simple()
            ));
            if let Err(e) = std::fs::rename(&r.dir, &temp) {
                return error(crate::l10n!(
                    "无法暂存归档：{e}",
                    "Could not stage the archive for deletion: {e}"
                ));
            }
            Some(temp)
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(e) => {
            return error(crate::l10n!(
                "无法读取归档目录：{e}",
                "Could not read the archive folder: {e}"
            ));
        }
    };
    let archived = match store::edit(|c| {
        c.archived.retain(|x| x.id != id);
        c.archived.clone()
    }) {
        Ok(archived) => archived,
        Err(e) => {
            let rollback = staged.as_ref().map(|temp| std::fs::rename(temp, &r.dir));
            return error(match rollback {
                Some(Err(r_error)) => crate::l10n!(
                    "配置保存失败且归档回滚失败：{e}；{r_error}",
                    "Could not save the configuration, and restoring the archive also failed: {e}; {r_error}"
                ),
                _ => e,
            });
        }
    };
    if let Some(temp) = staged {
        let mut doomed = r.clone();
        doomed.dir = temp.clone();
        if let Err(e) = archive::destroy(&doomed) {
            let config_rollback = store::edit(|c| c.archived.push(r.clone()));
            let path_rollback = std::fs::rename(&temp, &r.dir);
            return error(match (config_rollback, path_rollback) {
                (Err(r_error), _) => crate::l10n!(
                    "无法删除归档且列表回滚失败：{e}；{r_error}",
                    "Could not delete the archive, and restoring the archive list also failed: {e}; {r_error}"
                ),
                (Ok(()), Err(r_error)) => crate::l10n!(
                    "无法删除归档且目录回滚失败：{e}；{r_error}",
                    "Could not delete the archive, and restoring its folder also failed: {e}; {r_error}"
                ),
                (Ok(()), Ok(())) => e,
            });
        }
    }
    changed(&app);
    json!({ "archived": archive::with_status(&archived) })
}

#[tauri::command(async)]
fn accounts_launch(app: AppHandle, id: String) -> Value {
    launch_account(&app, &id)
}

#[tauri::command(async)]
fn accounts_reveal(id: String) {
    if let Some(a) = find(&id) {
        platform::reveal(&a.dir);
    }
}

#[tauri::command(async)]
fn accounts_quit(id: String) -> Value {
    let Some(a) = find(&id) else {
        return error(crate::locale::tr("未找到账号。", "Account not found."));
    };
    match platform::quit_profile(&a.dir) {
        Ok(count) => json!({ "ok": true, "count": count }),
        Err(e) => json!({ "ok": false, "error": e }),
    }
}

#[tauri::command(async)]
fn app_hide(app: AppHandle) {
    if let Some(w) = main_window(&app) {
        let _ = w.close();
    }
}

// After picking an account the launcher gets out of the way but stays running,
// so the next pick is instant instead of a cold start. Hidden rather than
// minimized: a minimized window leaves a second thumbnail in the Dock.
#[tauri::command(async)]
fn app_dismiss(app: AppHandle) {
    if let Some(w) = main_window(&app) {
        let _ = w.hide();
    }
}

#[tauri::command(async)]
fn app_show_health(app: AppHandle) {
    reveal_health(&app);
}

/// The three places the About section links to. A name rather than a URL, so the
/// page cannot ask for anything else to be opened.
#[tauri::command(async)]
fn app_open_link(which: String) {
    let base = update::releases_page().replace("/releases/latest", "");
    let url = match which.as_str() {
        "source" => base,
        "releases" => update::releases_page(),
        "licence" => format!("{base}/blob/main/LICENSE"),
        "upstream" => "https://github.com/pstarostka/claude-account-switcher-engine".into(),
        "accessibility" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility".into()
        }
        "claude-code" => "https://code.claude.com/docs/en/setup".into(),
        _ => return,
    };
    platform::open_url(&url);
}

#[tauri::command(async)]
fn settings_open(app: AppHandle, section: Option<String>) {
    open_settings(&app, section.as_deref());
}

// The reason travels with the answer: "not installed" and "installed, but from
// the Microsoft Store, which cannot be given its own profile" need different
// words, and only the platform knows which applies.
#[tauri::command(async)]
fn app_claude_installed() -> Value {
    json!({ "installed": platform::claude_installed(), "hint": platform::claude_hint() })
}

#[tauri::command(async)]
fn settings_get() -> Value {
    let mut v = serde_json::to_value(store::settings()).unwrap_or_default();
    v["version"] = update::VERSION.into();
    v["syncEdition"] = "CASE_LOCAL_SYNC_EDITION_V1".into();
    v["openAtLogin"] = platform::open_at_login().into();
    v["defaultHotkey"] = platform::DEFAULT_HOTKEY.into();
    v["dataDir"] = json!(store::conf_dir());
    v["backupsDir"] = json!(store::backup_root());
    v["usageSchedule"] = json!(usage_schedule::snapshot());
    v
}

#[tauri::command(async)]
fn usage_schedule_status() -> Value {
    json!(usage_schedule::snapshot())
}

#[tauri::command(async)]
fn settings_set_usage_schedule(enabled: bool, interval_minutes: u32) -> Value {
    match usage_schedule::configure(enabled, interval_minutes) {
        Ok(schedule) => json!({ "ok": true, "schedule": schedule }),
        Err(code) => json!({ "ok": false, "code": code }),
    }
}

#[tauri::command(async)]
fn settings_set_language(app: AppHandle, language: String) -> Value {
    if !matches!(language.as_str(), "en" | "zh-CN") {
        return error(locale::tr(
            "不支持这种界面语言。",
            "Unsupported interface language.",
        ));
    }
    if let Err(e) = store::edit(|c| c.settings.language = language.clone()) {
        return error(e);
    }
    if store::settings().language != language {
        return error(locale::tr(
            "无法保存语言设置。",
            "Could not save the language preference.",
        ));
    }
    locale::set(&language);
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        let _ = locale::install(&handle);
        tray::reload(&handle);
        if let Some(window) = handle.get_webview_window("settings") {
            let _ = window.set_title(locale::tr("CASE 设置", "CASE Settings"));
        }
    });
    let _ = app.emit("language:changed", &language);
    json!({ "ok": true, "language": language })
}

fn usage_failure_status(code: &str) -> &'static str {
    match code {
        "DESKTOP_IDENTITY_MISSING" | "IDENTITY_CHANGED" => "auth_failed",
        "REFRESH_TIMEOUT" | "TIMEOUT" => "timeout",
        // The global panel lock is held by another reader. This account did
        // not start a read and must not remain marked as an active query.
        "REFRESH_IN_PROGRESS"
        | "ACCOUNT_NOT_OPEN"
        | "PROFILE_PROCESS_AMBIGUOUS"
        | "PANEL_UNAVAILABLE"
        | "PANEL_UNRECOGNIZED" => "unavailable",
        _ => "failed",
    }
}

#[tauri::command(async)]
fn usage_refresh(id: String, native: bool) -> Value {
    let Some(account) = find(&id) else {
        return json!({
            "ok": false,
            "status": "failed",
            "code": "ACCOUNT_NOT_FOUND",
            "usage": Value::Null,
            "error": locale::tr("未找到账号。", "Account not found.")
        });
    };
    if usage_monitor::snapshot(&account).configured {
        let monitor = usage_monitor::refresh(&account);
        return json!({ "ok": monitor.status == "success", "status": monitor.status, "code": monitor.code, "usage": monitor.usage, "monitor": monitor });
    }
    if !native {
        usage::invalidate(&account.dir);
        let value = native_usage::read(&account.id, &account.dir);
        let monitor = usage_monitor::record_manual(&account, "success", None, value.clone());
        return json!({ "ok": true, "status": "success", "usage": value, "monitor": monitor, "localOnly": true });
    }
    match native_usage::capture(&account) {
        Ok(value) => {
            let monitor =
                usage_monitor::record_manual(&account, "success", None, Some(value.clone()));
            json!({ "ok": true, "status": "success", "usage": value, "monitor": monitor })
        }
        Err(code) => {
            let status = usage_failure_status(code);
            // A failed live read is never allowed to masquerade as a fresh
            // observation.  The UI must clear the displayed percentages and
            // show the explicit status; the persisted cache remains untouched
            // for the next successful probe.
            let monitor = usage_monitor::record_manual(&account, status, Some(code), None);
            json!({ "ok": false, "status": status, "code": code, "usage": Value::Null, "monitor": monitor })
        }
    }
}

#[tauri::command(async)]
fn usage_monitor_connect(id: String) -> Value {
    let Some(account) = find(&id) else {
        return json!({ "ok": false, "status": "failed", "code": "ACCOUNT_NOT_FOUND", "usage": Value::Null });
    };
    match usage_monitor::connect(&account) {
        Ok(monitor) => json!({ "ok": true, "monitor": monitor }),
        Err(code) => {
            json!({ "ok": false, "status": "unavailable", "code": code, "usage": Value::Null })
        }
    }
}

#[tauri::command(async)]
fn usage_monitor_setup(id: String) -> Value {
    let Some(account) = find(&id) else {
        return json!({ "ok": false, "status": "failed", "code": "ACCOUNT_NOT_FOUND", "usage": Value::Null });
    };
    match usage_monitor::setup(&account) {
        Ok(monitor) => json!({ "ok": true, "monitor": monitor }),
        Err(code) => {
            let status = if code == "CLI_IDENTITY_MISMATCH" {
                "auth_failed"
            } else {
                "unavailable"
            };
            json!({ "ok": false, "status": status, "code": code, "usage": Value::Null })
        }
    }
}

#[tauri::command(async)]
fn settings_set_hotkey(app: AppHandle, hotkey: String, enabled: bool) -> Value {
    if let Err(e) = register_hotkey(&app, enabled.then_some(hotkey.as_str())) {
        apply_hotkey_from_settings(&app); // keep whatever was working before
        return error(e);
    }
    if let Err(e) = store::edit(|c| {
        c.settings.hotkey = hotkey;
        c.settings.hotkey_enabled = enabled;
    }) {
        return error(e);
    }
    json!({ "ok": true })
}

#[tauri::command(async)]
fn settings_set_presentation(app: AppHandle, menu_bar: bool, hide_dock: bool) -> Value {
    // No menu bar means the Dock icon has to stay, or the app becomes unreachable.
    let hide_dock = menu_bar && hide_dock;
    if let Err(e) =
        store::edit(|c| (c.settings.menu_bar, c.settings.hide_dock) = (menu_bar, hide_dock))
    {
        return error(e);
    }
    apply_presentation(&app);
    json!({ "menuBar": menu_bar, "hideDock": hide_dock })
}

#[tauri::command(async)]
fn settings_set_sorting(app: AppHandle, sort_by_recent: bool) -> Value {
    if let Err(e) = store::edit(|c| c.settings.sort_by_recent = sort_by_recent) {
        return error(e);
    }
    changed(&app);
    json!({ "sortByRecent": sort_by_recent })
}

#[tauri::command(async)]
fn settings_set_login_item(enabled: bool) -> Value {
    platform::set_open_at_login(enabled);
    json!({ "openAtLogin": platform::open_at_login() })
}

#[tauri::command(async)]
fn settings_set_auto_update(enabled: bool) -> Value {
    if let Err(e) = store::edit(|c| c.settings.auto_update_check = enabled) {
        return error(e);
    }
    json!({ "autoUpdateCheck": enabled })
}

// Both platforms answer both: pinning to the Dock and adding a Start menu entry
// are the same offer in different words.
#[tauri::command(async)]
fn shortcut_status() -> Value {
    let s = platform::shortcut_status();
    json!({ "supported": true, "present": s.present, "blurb": s.blurb, "cta": s.cta, "tip": s.tip })
}

#[tauri::command(async)]
fn shortcut_create() -> Value {
    match platform::shortcut_create() {
        Ok(already) => json!({ "ok": true, "already": already }),
        Err(e) => json!({ "ok": false, "error": e }),
    }
}

#[tauri::command(async)]
fn chrome_list(account_id: String) -> Value {
    let profiles = chrome::list_profiles();
    let a = find(&account_id);
    json!({
        "installed": chrome::installed(),
        "suggestion": a.as_ref().and_then(|a| chrome::suggest_for(&a.name, &profiles)).map(|p| p.dir.clone()),
        "current": a.and_then(|a| a.chrome),
        "profiles": profiles,
    })
}

#[tauri::command(async)]
fn chrome_pair(
    app: AppHandle,
    account_id: String,
    dir: Option<String>,
    open_on_launch: Option<bool>,
) -> Value {
    let Ok(_operation) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一个账号操作正在进行，请稍后重试。",
            "Another account operation is in progress. Try again shortly.",
        ));
    };
    let paired = store::edit(|c| {
        let a = c.accounts_mut().iter_mut().find(|x| x.id == account_id)?;
        a.chrome = dir.filter(|d| !d.is_empty()).map(|dir| store::ChromePair {
            dir,
            open_on_launch: open_on_launch != Some(false),
        });
        Some(c.accounts().to_vec())
    });
    match paired {
        Ok(Some(accounts)) => {
            changed(&app);
            json!({ "accounts": stored_list(&accounts) })
        }
        Ok(None) => error(crate::locale::tr("未找到账号。", "Account not found.")),
        Err(e) => error(e),
    }
}

#[tauri::command(async)]
fn chrome_extension(dir: String) {
    chrome::open_extension_page(&dir);
}

#[tauri::command(async)]
fn chrome_new_profile() -> Value {
    json!({ "dir": chrome::next_profile_dir() })
}

#[tauri::command(async)]
fn health_scan() -> health::Scan {
    health::scan(
        &store::accounts().unwrap_or_default(),
        &store::backup_root(),
        &health::Places::system(),
    )
}

#[tauri::command(async)]
fn health_rebuild(account_id: String, orphan: health::Orphan) -> Value {
    let Ok(_operation) = store::OPERATION_LOCK.try_lock() else {
        return error(crate::locale::tr(
            "另一个账号操作正在进行，请稍后重试。",
            "Another account operation is in progress. Try again shortly.",
        ));
    };
    let Some(a) = find(&account_id) else {
        return error(crate::locale::tr("未找到账号。", "Account not found."));
    };
    match local_sync::rebuild_session(&a, &orphan) {
        Ok(result) => json!({ "ok": true, "result": result }),
        Err(e) => error(e),
    }
}

#[tauri::command(async)]
fn health_reveal(target: Option<PathBuf>) {
    if let Some(t) = target {
        platform::reveal(&t);
    }
}

// -------------------------------------------------------------- self-update ---

#[tauri::command(async)]
fn update_check() -> Value {
    let res = update::check();
    rt().last_update = Some(res.clone());
    if res.available {
        let _ = store::edit(|c| c.settings.last_update_check = now_ms());
    }
    merged_check(&res)
}

fn merged_check(res: &update::Check) -> Value {
    let mut v = serde_json::to_value(res).unwrap_or_default();
    v["packaged"] = (!cfg!(debug_assertions)).into();
    v
}

#[tauri::command(async)]
fn update_install(app: AppHandle) -> Value {
    // This edition's sync runtime is separate from its application bundle.
    // Release instructions cover both parts until an atomic installer exists.
    if option_env!("CASE_LOCAL_SYNC_BUILD") == Some("1") {
        return error(crate::locale::tr(
            "这是独立维护版，同步引擎单独安装。请按自有仓库的发行说明升级。",
            "This independent edition uses a separate sync engine. Follow the instructions in our release before updating.",
        ));
    }
    let Some(info) = rt().last_update.clone().filter(|u| u.available) else {
        return error(crate::locale::tr(
            "没有可安装的更新。",
            "No update is available to install.",
        ));
    };
    let staged = match update::download(&info, &|p| {
        let _ = app.emit("update:progress", p);
    }) {
        Ok(s) => s,
        Err(e) => return error(e),
    };
    if let Err(e) = update::apply(&staged) {
        // A download that never reached the swap script is a release's worth of nothing.
        update::discard(&staged);
        return error(e);
    }
    // The swap script waits for this process to exit before replacing the app.
    let app2 = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(250));
        app2.exit(0);
    });
    json!({ "ok": true, "relaunching": true })
}

const DAY_MS: i64 = 24 * 60 * 60 * 1000;

/// A quiet daily check. It only ever *looks* — installing stays a decision, and
/// a launcher that cannot reach GitHub should behave exactly as it always did.
fn update_tick(app: &AppHandle) {
    let s = store::settings();
    if cfg!(debug_assertions) || !s.auto_update_check || now_ms() - s.last_update_check < DAY_MS {
        return;
    }
    let res = update::check();
    rt().last_update = Some(res.clone());
    if !res.ok {
        return;
    }
    let _ = store::edit(|c| c.settings.last_update_check = now_ms());
    if res.available {
        let _ = app.emit("update:available", &res);
    }
}

// ---------------------------------------------------------------- hotkey ---

fn register_hotkey(app: &AppHandle, accel: Option<&str>) -> Result<(), String> {
    let gs = app.global_shortcut();
    let current = rt().hotkey.take();
    if let Some(cur) = current {
        let _ = gs.unregister(cur.as_str());
    }
    let Some(accel) = accel else { return Ok(()) };
    // Refused when the combination cannot be parsed or another app owns it.
    gs.register(accel).map_err(|_| {
        crate::locale::tr(
            "快捷键已被占用或格式无效，请换一组。",
            "That shortcut is taken or invalid. Choose another combination.",
        )
        .to_owned()
    })?;
    rt().hotkey = Some(accel.to_owned());
    Ok(())
}

/// A stored accelerator this platform can actually register. A config written on
/// a Mac names Command, which does not exist on a Windows keyboard — register()
/// would refuse it and report the shortcut as taken by another app, which is both
/// wrong and unfixable from the settings sheet.
fn usable_hotkey(accel: &str) -> &str {
    let foreign = if cfg!(target_os = "macos") {
        "Super"
    } else {
        "Command"
    };
    if accel.split('+').any(|k| k == foreign) {
        platform::DEFAULT_HOTKEY
    } else {
        accel
    }
}

fn apply_hotkey_from_settings(app: &AppHandle) {
    let s = store::settings();
    let _ = register_hotkey(app, s.hotkey_enabled.then(|| usable_hotkey(&s.hotkey)));
}

// ------------------------------------------------------------------ window ---

fn main_window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("main")
}

pub fn open_window(app: &AppHandle) {
    if let Some(w) = main_window(app) {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

/// Summon the launcher, or put it away if it is already in front.
fn toggle_window(app: &AppHandle) {
    match main_window(app) {
        Some(w) if w.is_visible().unwrap_or(false) && w.is_focused().unwrap_or(false) => {
            let _ = w.hide();
        }
        _ => open_window(app),
    }
}

pub fn reveal_health(app: &AppHandle) {
    open_window(app);
    let _ = app.emit("health:show", ());
}

/// The settings window: one at a time, fronted if it is already open. Created on
/// demand rather than kept hidden, since most launches never open it. Building a
/// window waits on the main thread, so callers there hand this to a thread.
pub fn open_settings(app: &AppHandle, section: Option<&str>) {
    if let Some(w) = app.get_webview_window("settings") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
        if let Some(s) = section {
            let _ = app.emit_to("settings", "settings:section", s);
        }
        return;
    }
    let start = serde_json::to_string(section.unwrap_or("general")).unwrap_or_default();
    let builder =
        WebviewWindowBuilder::new(app, "settings", WebviewUrl::App("settings.html".into()))
            .title(crate::locale::tr("CASE 设置", "CASE Settings"))
            .inner_size(760.0, 540.0)
            .min_inner_size(640.0, 440.0)
            .visible(false)
            .initialization_script(format!(
                "window.CASE_SECTION = {start}; window.CASE_LOCALE = {};",
                serde_json::to_string(&store::settings().language).unwrap_or_default()
            ))
            .on_page_load(|w, p| {
                if p.event() == PageLoadEvent::Finished {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            });
    #[cfg(target_os = "macos")]
    let builder = builder
        .title_bar_style(tauri::TitleBarStyle::Overlay)
        .hidden_title(true)
        .traffic_light_position(tauri::LogicalPosition::new(18.0, 20.0));
    let _ = builder.build();
}

/// With a global hotkey, a menu bar or launch at login there is still a way back
/// in, so closing the window only hides it — the app has to outlive its window,
/// or the shortcut dies the first time the window is dismissed.
fn keeps_running() -> bool {
    let s = store::settings();
    rt().hotkey.is_some() || s.menu_bar || platform::open_at_login()
}

fn create_window(app: &AppHandle) -> tauri::Result<()> {
    let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("CASE")
        // A fixed size the page lays itself out in. It never resizes itself:
        // a window that grows and shrinks under the pointer is the jank this
        // layout exists to avoid.
        .inner_size(440.0, 600.0)
        .min_inner_size(380.0, 420.0)
        .visible(false)
        .initialization_script(format!(
            "window.CASE_LOCALE = {};",
            serde_json::to_string(&store::settings().language).unwrap_or_default()
        ))
        .on_page_load(|w, p| {
            if p.event() == PageLoadEvent::Finished && !std::mem::take(&mut rt().start_hidden) {
                let _ = w.show();
                let _ = w.set_focus();
            }
        });
    #[cfg(target_os = "macos")]
    let builder = builder
        .title_bar_style(tauri::TitleBarStyle::Overlay)
        .hidden_title(true)
        .traffic_light_position(tauri::LogicalPosition::new(16.0, 16.0));
    let win = builder.build()?;

    let app = app.clone();
    win.on_window_event(move |e| match e {
        WindowEvent::CloseRequested { api, .. } if keeps_running() => {
            api.prevent_close();
            if let Some(w) = main_window(&app) {
                let _ = w.hide();
            }
        }
        // Windows picks its tray icon by taskbar theme; macOS recolours a template.
        WindowEvent::ThemeChanged(_) => {
            platform::forget_theme();
            tray::reload(&app);
        }
        _ => {}
    });
    Ok(())
}

/// Apply the menu-bar / Dock presentation. Hiding the Dock icon is only offered
/// alongside the menu bar — without either, the app would have no way back. The
/// Windows equivalent of a hidden Dock tile is a window kept off the taskbar.
fn apply_presentation(app: &AppHandle) {
    let s = store::settings();
    if s.menu_bar {
        tray::enable(app);
    } else {
        tray::disable(app);
    }
    let hide = s.menu_bar && s.hide_dock;
    #[cfg(target_os = "macos")]
    let _ = app.set_dock_visibility(!hide);
    #[cfg(not(target_os = "macos"))]
    if let Some(w) = main_window(app) {
        let _ = w.set_skip_taskbar(hide);
    }
}

// ------------------------------------------------------------ health watch ---

// A scan costs ~50 ms, so checking every couple of minutes is free. The point is
// latency: the failure this catches is invisible while the app runs, so the gap
// between "it started failing" and "you were told" is the whole value.
// CLAUDE_ACCOUNTS_WATCH_MS shortens this for testing.
fn watch_interval() -> Duration {
    let ms = std::env::var("CLAUDE_ACCOUNTS_WATCH_MS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(120_000);
    Duration::from_millis(ms)
}

fn issue_keys(data: &health::Scan) -> HashSet<String> {
    let mut keys = HashSet::new();
    for a in &data.accounts {
        // Keyed on the account, not the last failure timestamp: a continuing
        // failure must not re-notify on every tick.
        if a.failing() {
            keys.insert(format!("fail:{}", a.id));
        }
        for o in &a.orphans {
            keys.insert(format!("orphan:{}:{}", a.id, o.cli_session_id));
        }
    }
    keys
}

fn notify(app: &AppHandle, title: &str, body: &str) {
    let _ = app.notification().builder().title(title).body(body).show();
}

fn health_tick(app: &AppHandle, first: bool) {
    let data = health_scan();
    let n = data.issue_count();
    let keys = issue_keys(&data);
    let fresh: Vec<String> = {
        let mut rt = rt();
        rt.issue_count = n;
        let fresh = keys.difference(&rt.seen_issues).cloned().collect();
        rt.seen_issues = keys;
        fresh
    };
    if let Some(w) = main_window(app) {
        platform::set_badge(&w, n);
    }
    tray::refresh(app);
    let _ = app.emit("health:update", &data);

    let failing: Vec<&str> = data
        .accounts
        .iter()
        .filter(|a| a.failing())
        .map(|a| a.name.as_str())
        .collect();
    if !failing.is_empty() && (first || fresh.iter().any(|k| k.starts_with("fail:"))) {
        // Worth interrupting for: while this lasts, every session is memory-only.
        notify(
            app,
            crate::locale::tr("Claude 无法保存会话", "Claude is not saving sessions"),
            &crate::l10n!(
                "{}：会话可能在 Claude 重启后丢失。",
                "{}: sessions may be lost when Claude restarts.",
                failing.join(", ")
            ),
        );
        return;
    }

    // Pre-existing orphans are old news at startup; the badge already carries them.
    let lost = if first {
        0
    } else {
        fresh.iter().filter(|k| k.starts_with("orphan:")).count()
    };
    if lost > 0 {
        let title = if lost == 1 {
            crate::locale::tr(
                "Claude 列表中有一条会话缺失",
                "A session is missing from the Claude list",
            )
            .to_owned()
        } else {
            crate::l10n!(
                "Claude 列表中有 {lost} 条会话缺失",
                "{lost} sessions are missing from the Claude list"
            )
        };
        notify(
            app,
            &title,
            crate::locale::tr(
                "正文仍在，请打开 CASE 恢复索引。",
                "The transcripts survived. Open CASE to restore their index entries.",
            ),
        );
    }
}

fn start_watching(app: AppHandle) {
    let app2 = app.clone();
    std::thread::spawn(move || {
        health_tick(&app, true);
        loop {
            std::thread::sleep(watch_interval());
            health_tick(&app, false);
        }
    });
    // Behind the first paint: an update check is never worth delaying the window.
    // Asked hourly, acted on daily, so a launcher left running for weeks still looks.
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(4));
        loop {
            update_tick(&app2);
            std::thread::sleep(Duration::from_secs(3600));
        }
    });
}

// -------------------------------------------------------------------- main ---

fn main() {
    // Notifications are dropped outright for an app Windows cannot tie to a Start
    // menu shortcut, and it matches them up by this id. Set before any window.
    #[cfg(windows)]
    platform::set_app_user_model_id();
    store::migrate_legacy();
    locale::set(&store::settings().language);

    tauri::Builder::default()
        // Windows has no `activate` event, so without this a second click on the
        // Start menu entry starts a second launcher — two processes writing
        // accounts.json. First, so it runs before anything else does.
        // A second copy started at login, by an entry an older build left behind,
        // stays as quiet as the first.
        .plugin(tauri_plugin_single_instance::init(|app, args, _| {
            if !args.iter().any(|a| a == "--hidden") {
                open_window(app);
            }
        }))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _, event| {
                    if event.state == ShortcutState::Pressed {
                        toggle_window(app);
                    }
                })
                .build(),
        )
        .plugin(tauri_plugin_notification::init())
        .invoke_handler(tauri::generate_handler![
            accounts_list,
            accounts_status,
            accounts_sync,
            accounts_adopt,
            accounts_add,
            accounts_update,
            accounts_remove,
            accounts_size,
            accounts_archive,
            archive_restore,
            archive_delete,
            accounts_launch,
            accounts_reveal,
            accounts_quit,
            app_hide,
            app_dismiss,
            app_show_health,
            app_open_link,
            app_claude_installed,
            settings_open,
            settings_get,
            settings_set_language,
            settings_set_usage_schedule,
            usage_schedule_status,
            usage_refresh,
            usage_monitor_connect,
            usage_monitor_setup,
            settings_set_hotkey,
            settings_set_presentation,
            settings_set_sorting,
            settings_set_login_item,
            settings_set_auto_update,
            shortcut_status,
            shortcut_create,
            chrome_list,
            chrome_pair,
            chrome_extension,
            chrome_new_profile,
            health_scan,
            health_rebuild,
            health_reveal,
            update_check,
            update_install,
        ])
        .setup(|app| {
            // The LaunchAgent and the Run key both start CASE with --hidden.
            rt().start_hidden = std::env::args().any(|a| a == "--hidden");
            let handle = app.handle().clone();
            locale::install(&handle)?;
            handle.on_menu_event(|app, e| tray::on_menu(app, e.id().as_ref()));
            create_window(&handle)?;
            apply_hotkey_from_settings(&handle);
            apply_presentation(&handle);
            // A click on a notification opens the panel it is about.
            let h = handle.clone();
            app.notification().on_action(move |_| reveal_health(&h))?;
            start_watching(handle);
            usage_schedule::start();
            Ok(())
        })
        .build(tauri::generate_context!())
        .unwrap_or_else(|_| {
            panic!(
                "{}",
                crate::locale::tr("CASE 无法启动", "CASE could not start")
            )
        })
        .run(|app, event| {
            // The window survives a pick (hidden, not closed), so clicking the Dock
            // icon has to reveal it.
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen { .. } = event {
                open_window(app);
            }
            #[cfg(not(target_os = "macos"))]
            let _ = (app, event);
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_rejected_panel_read_is_not_an_in_progress_account_query() {
        assert_eq!(usage_failure_status("REFRESH_IN_PROGRESS"), "unavailable");
        assert_eq!(usage_failure_status("REFRESH_TIMEOUT"), "timeout");
    }

    #[test]
    fn folder_names_keep_letters_and_collapse_the_rest() {
        assert_eq!(slug("Work"), "Work");
        assert_eq!(slug("  Side / project!! "), "Side-project");
        assert_eq!(slug("Café"), "Café");
        assert_eq!(slug("!!!"), "account");
        assert_eq!(slug(&"x".repeat(60)).len(), 40);
    }

    #[test]
    fn a_restored_name_steps_around_its_namesakes() {
        let taken = [
            store::new_account("Work", "/a".into()),
            store::new_account("work 2", "/b".into()),
        ];
        assert_eq!(free_name("Work", &taken), "Work 3");
        assert_eq!(free_name("Home", &taken), "Home");
    }

    #[test]
    fn only_the_name_typed_back_confirms_a_deletion() {
        assert!(confirmed(Some(" Work "), "Work"));
        assert!(
            !confirmed(Some("work"), "Work"),
            "case matters: it is the name, read back"
        );
        assert!(!confirmed(Some(""), "Work"));
        assert!(!confirmed(None, "Work"));
    }

    #[test]
    fn a_hotkey_from_the_other_platform_falls_back_to_this_ones_default() {
        let foreign = if cfg!(target_os = "macos") {
            "Super+Alt+C"
        } else {
            "Alt+Command+C"
        };
        assert_eq!(usable_hotkey(foreign), platform::DEFAULT_HOTKEY);
        assert_eq!(usable_hotkey("Alt+Shift+K"), "Alt+Shift+K");
    }
}
