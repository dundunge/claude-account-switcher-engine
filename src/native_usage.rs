//! Observe Claude's own quota panel. No credentials, network client or login.
//! A manual read may briefly open the usage popover; background probes only
//! read persisted observations. Never open or switch an inactive account.
use crate::{store, usage};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
#[cfg(target_os = "macos")]
use std::sync::Mutex;

#[cfg(target_os = "macos")]
#[path = "native_usage_mac.rs"]
mod mac;

#[cfg(target_os = "macos")]
static READING: Mutex<()> = Mutex::new(());

fn hash(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

fn identity(profile: &Path) -> Option<(String, String)> {
    let value = usage::identity(profile)?;
    Some((hash(&value.account), hash(&value.organization)))
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Saved {
    account_hash: String,
    #[serde(default)]
    organization_hash: Option<String>,
    profile: PathBuf,
    usage: usage::Usage,
}

fn cache_path(id: &str) -> PathBuf {
    store::conf_dir()
        .join("native-usage")
        .join(format!("{}.json", hash(id)))
}

fn cached_at(file: &Path, profile: &Path) -> Option<usage::Usage> {
    let metadata = std::fs::symlink_metadata(file).ok()?;
    if metadata.file_type().is_symlink()
        || file
            .parent()
            .and_then(|p| std::fs::symlink_metadata(p).ok())
            .is_some_and(|m| m.file_type().is_symlink())
        || metadata.len() > 32 * 1024
    {
        return None;
    }
    let saved: Saved = serde_json::from_slice(&std::fs::read(file).ok()?).ok()?;
    let value = saved.usage;
    (saved.profile == profile
        && identity(profile).is_some_and(|(account, organization)| {
            account == saved.account_hash
                && saved.organization_hash.as_deref() == Some(organization.as_str())
        })
        && value.source == "claude-ui"
        && value.at > 0
        && value.at <= crate::now_ms()
        && value.fh.is_none_or(|v| v <= 100)
        && value.sd.is_none_or(|v| v <= 100)
        && (value.fh.is_some() || value.sd.is_some()))
    .then_some(value)
}

pub fn read(id: &str, profile: &Path) -> Option<usage::Usage> {
    usage::newest_reading(usage::read(profile), cached_at(&cache_path(id), profile))
}

#[cfg(any(target_os = "macos", test))]
fn persist(
    id: &str,
    profile: &Path,
    expected: &(String, String),
    value: &usage::Usage,
) -> Result<(), &'static str> {
    if identity(profile).as_ref() != Some(expected) {
        return Err("IDENTITY_CHANGED");
    }
    let file = cache_path(id);
    let parent = file.parent().ok_or("SAVE_FAILED")?;
    if std::fs::symlink_metadata(parent)
        .ok()
        .is_some_and(|m| m.file_type().is_symlink())
        || std::fs::symlink_metadata(&file)
            .ok()
            .is_some_and(|m| m.file_type().is_symlink())
    {
        return Err("SAVE_FAILED");
    }
    std::fs::create_dir_all(parent).map_err(|_| "SAVE_FAILED")?;
    let temp = parent.join(format!(".{}.tmp", uuid::Uuid::new_v4()));
    let result = (|| {
        use std::io::Write;
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut out = options.open(&temp)?;
        let data = Saved {
            account_hash: expected.0.clone(),
            organization_hash: Some(expected.1.clone()),
            profile: profile.into(),
            usage: value.clone(),
        };
        out.write_all(&serde_json::to_vec(&data)?)?;
        out.sync_all()?;
        std::fs::rename(&temp, &file)
    })();
    let _ = std::fs::remove_file(&temp);
    result.map_err(|_: std::io::Error| "SAVE_FAILED")
}

pub fn capture(account: &store::Account) -> Result<usage::Usage, &'static str> {
    #[cfg(target_os = "macos")]
    {
        let _guard = READING.try_lock().map_err(|_| "REFRESH_IN_PROGRESS")?;
        let expected = identity(&account.dir).ok_or("DESKTOP_IDENTITY_MISSING")?;
        let value = mac::capture(&account.dir)?;
        persist(&account.id, &account.dir, &expected, &value)?;
        Ok(value)
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = account;
        Err("PLATFORM_UNSUPPORTED")
    }
}

#[cfg(any(target_os = "macos", test))]
#[derive(Debug)]
pub(super) struct Row {
    label: String,
    percent: f64,
    reset: Option<String>,
}

#[cfg(any(target_os = "macos", test))]
fn window(label: &str) -> Option<&'static str> {
    let text = label.to_lowercase();
    if [
        "session limit",
        "current session",
        "5-hour",
        "5 hour",
        "five hour",
        "5 小时",
        "5小时",
        "当前会话",
        "会话限额",
    ]
    .iter()
    .any(|word| text.contains(word))
    {
        Some("fh")
    } else if (text.contains("weekly") && text.contains("all models"))
        || (text.contains('周') && text.contains("所有模型"))
    {
        Some("sd")
    } else {
        None
    }
}

#[cfg(any(target_os = "macos", test))]
fn normalize(rows: Vec<Row>, now: i64) -> Result<usage::Usage, &'static str> {
    let mut result = usage::Usage {
        at: now,
        age: 0,
        fh: None,
        sd: None,
        source: "claude-ui".into(),
        fh_reset_at: None,
        sd_reset_at: None,
        fh_reset_estimated: false,
        sd_reset_estimated: false,
        fh_reset_text: None,
        sd_reset_text: None,
    };
    for row in rows {
        if !row.percent.is_finite() || !(0.0..=100.0).contains(&row.percent) {
            return Err("PANEL_UNRECOGNIZED");
        }
        let value = Some(row.percent.round() as u8);
        let hint = row
            .reset
            .filter(|s| s.len() <= 180 && !s.contains(['\n', '\r', '@']));
        let (target, reset) = match window(&row.label) {
            Some("fh") => (&mut result.fh, &mut result.fh_reset_text),
            Some("sd") => (&mut result.sd, &mut result.sd_reset_text),
            _ => continue,
        };
        if target.is_some() && *target != value {
            return Err("PANEL_UNRECOGNIZED");
        }
        *target = value;
        *reset = hint;
    }
    if result.fh.is_none() && result.sd.is_none() {
        return Err("PANEL_UNRECOGNIZED");
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn row(label: &str, percent: f64) -> Row {
        Row {
            label: label.into(),
            percent,
            reset: Some("Resets in 2 hr 4 min".into()),
        }
    }

    #[test]
    fn reads_official_panel_windows_without_substituting_model_limits() {
        let value = normalize(
            vec![
                row("Session limit", 61.0),
                row("Weekly · all models", 89.0),
                row("Weekly · Sonnet", 10.0),
            ],
            123,
        )
        .unwrap();
        assert_eq!((value.fh, value.sd), (Some(61), Some(89)));
        assert_eq!(value.at, 123);
        assert!(value.fh_reset_at.is_none());
        assert_eq!(value.fh_reset_text.as_deref(), Some("Resets in 2 hr 4 min"));
    }

    #[test]
    fn weekly_only_is_not_a_five_hour_limit() {
        let value = normalize(vec![row("Weekly · all models", 89.0)], 123).unwrap();
        assert_eq!((value.fh, value.sd), (None, Some(89)));
        assert!(normalize(vec![row("Context", 10.0)], 123).is_err());
    }

    #[test]
    fn maps_chinese_usage_windows_without_model_substitution() {
        let value = normalize(
            vec![row("当前会话", 41.0), row("周额度 · 所有模型", 82.0)],
            123,
        )
        .unwrap();
        assert_eq!((value.fh, value.sd), (Some(41), Some(82)));
    }

    #[test]
    fn malformed_or_conflicting_indicators_do_not_publish_false_usage() {
        for pct in [f64::NAN, f64::INFINITY, -1.0, 101.0] {
            assert!(normalize(vec![row("Session limit", pct)], 123).is_err());
        }
        assert!(
            normalize(
                vec![row("Session limit", 1.0), row("Session limit", 2.0)],
                123
            )
            .is_err()
        );
    }

    #[test]
    fn saved_panel_cannot_follow_a_different_desktop_login() {
        let dir = crate::platform::temp_dir("case-native-usage-")
            .unwrap()
            .canonicalize()
            .unwrap();
        let profile = dir.join("profile");
        std::fs::create_dir(&profile).unwrap();
        std::fs::write(
            profile.join("config.json"),
            r#"{"lastKnownAccountUuid":"a","lastKnownOrganizationUuid":"o"}"#,
        )
        .unwrap();
        let file = dir.join("cache.json");
        let saved = Saved {
            account_hash: hash("a"),
            organization_hash: Some(hash("o")),
            profile: profile.clone(),
            usage: normalize(vec![row("Session limit", 61.0)], crate::now_ms()).unwrap(),
        };
        std::fs::write(&file, serde_json::to_vec(&saved).unwrap()).unwrap();
        assert!(cached_at(&file, &profile).is_some());
        std::fs::write(
            profile.join("config.json"),
            r#"{"lastKnownAccountUuid":"b","lastKnownOrganizationUuid":"o"}"#,
        )
        .unwrap();
        assert!(cached_at(&file, &profile).is_none());
        assert_eq!(
            persist("unused", &profile, &(hash("a"), hash("o")), &saved.usage),
            Err("IDENTITY_CHANGED")
        );
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn a_cold_profile_is_never_started_for_a_usage_read() {
        let dir = crate::platform::temp_dir("case-native-cold-")
            .unwrap()
            .canonicalize()
            .unwrap();
        std::fs::write(
            dir.join("config.json"),
            r#"{"lastKnownAccountUuid":"a","lastKnownOrganizationUuid":"o"}"#,
        )
        .unwrap();
        let account = store::new_account("Cold", dir.clone());
        assert_eq!(capture(&account), Err("ACCOUNT_NOT_OPEN"));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
