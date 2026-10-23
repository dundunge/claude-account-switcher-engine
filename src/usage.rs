//! Plan usage, read from Claude's own bookkeeping.
//!
//! Every profile carries `plan-usage-history.json`: `{version, samples}`, where
//! each sample is `{t, org, u:{fh, sd}}` — a timestamp, an org UUID, and two
//! integers from 0 to 100.
//!
//! The key names are suggestive but were not taken on trust. Across 25 days and
//! 3283 samples of one profile, runs of non-zero `fh` cap at 4.83–5.14 hours and
//! then drop to zero within a single 5-minute sampling interval — a fixed
//! five-hour window that resets, not a rolling one. Runs of non-zero `sd` last
//! 4.9–6.9 days. So `fh` is percent of the five-hour limit and `sd` percent of
//! the weekly one.
//!
//! Read-only, and the file is Claude's to write. We never touch it.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::SystemTime;

#[cfg(test)]
const FIVE_HOURS: i64 = 5 * 60 * 60 * 1000;
#[cfg(test)]
const ONE_WEEK: i64 = 7 * 24 * 60 * 60 * 1000;

#[derive(Clone, Copy)]
struct Sample {
    at: i64,
    fh: Option<u8>,
    sd: Option<u8>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ProfileIdentity {
    pub(crate) account: String,
    pub(crate) organization: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Usage {
    pub at: i64,
    #[serde(default)]
    pub age: i64,
    pub fh: Option<u8>,
    pub sd: Option<u8>,
    pub source: String,
    pub fh_reset_at: Option<i64>,
    pub sd_reset_at: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fh_reset_text: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sd_reset_text: Option<String>,
}

// The file is ~280 KB and is rewritten every five minutes, but the status poll
// runs every 2.5 seconds. Keyed on mtime and size so the parse happens at
// Claude's rate.
type Cached = (Option<SystemTime>, u64, String, String, Option<Sample>);
static CACHE: Mutex<Option<HashMap<PathBuf, Cached>>> = Mutex::new(None);

fn pct(v: &Value) -> Option<u8> {
    v.as_f64()
        .filter(|n| n.is_finite())
        .map(|n| n.round().clamp(0.0, 100.0) as u8)
}

fn find_string(value: &Value, keys: &[&str]) -> Option<String> {
    match value {
        Value::Object(map) => {
            for key in keys {
                if let Some(value) = map.get(*key).and_then(Value::as_str)
                    && !value.is_empty()
                {
                    return Some(value.to_owned());
                }
            }
            map.values().find_map(|value| find_string(value, keys))
        }
        Value::Array(values) => values.iter().find_map(|value| find_string(value, keys)),
        _ => None,
    }
}

/// Resolve the profile, account and organization that a local observation can
/// be trusted to describe.  A missing or ambiguous organization is unknown;
/// showing another organization's percentage is worse than showing no value.
pub(crate) fn identity(profile: &Path) -> Option<ProfileIdentity> {
    // Check the profile itself and the files we read.  Some normal macOS
    // paths (notably /var -> /private/var) contain a symlinked parent; that
    // must not make every temporary or system profile unreadable.  A symlink
    // at the profile/config boundary is still rejected so usage cannot be
    // silently read from a different account tree.
    if std::fs::symlink_metadata(profile)
        .ok()
        .is_some_and(|m| m.file_type().is_symlink())
    {
        return None;
    }
    let config_file = profile.join("config.json");
    let metadata = std::fs::symlink_metadata(&config_file).ok()?;
    if metadata.file_type().is_symlink() {
        return None;
    }
    if !metadata.is_file() || metadata.len() > 1024 * 1024 {
        return None;
    }
    let config: Value = serde_json::from_slice(&std::fs::read(config_file).ok()?).ok()?;
    let account = find_string(
        &config,
        &["lastKnownAccountUuid", "accountUuid", "account_uuid"],
    )?;
    let organization = find_string(
        &config,
        &[
            "lastKnownOrganizationUuid",
            "organizationUuid",
            "organization_uuid",
        ],
    )
    .or_else(|| {
        let root = profile.join("claude-code-sessions").join(&account);
        let mut names = std::fs::read_dir(root)
            .ok()?
            .filter_map(Result::ok)
            .filter_map(|entry| {
                entry
                    .file_type()
                    .ok()
                    .filter(|t| t.is_dir())
                    .map(|_| entry.file_name())
            })
            .filter_map(|name| name.into_string().ok())
            .filter(|name| uuid::Uuid::parse_str(name).is_ok())
            .collect::<Vec<_>>();
        names.sort();
        (names.len() == 1).then(|| names.remove(0))
    })?;
    Some(ProfileIdentity {
        account,
        organization,
    })
}

/// The most recent sample for the current organization. Samples from another
/// organization, and legacy samples without an organization, remain unknown.
fn newest(file: &Path, organization: &str) -> Option<Sample> {
    let raw: Value = serde_json::from_slice(&std::fs::read(file).ok()?).ok()?;
    let mut last: Option<(f64, &Value)> = None;
    if !matches!(raw["version"].as_u64(), Some(1 | 2)) {
        return None;
    }
    for s in raw["samples"].as_array()? {
        if s["org"].as_str() != Some(organization) {
            continue;
        }
        if let Some(t) = s["t"].as_f64()
            && t.is_finite()
            && t > 0.0
            && t <= crate::now_ms() as f64
            && last.is_none_or(|(lt, _)| t > lt)
        {
            last = Some((t, s));
        }
    }
    last.map(|(t, s)| Sample {
        at: t as i64,
        fh: pct(if raw["version"] == 1 && s.get("u").is_none() {
            &s["fh"]
        } else {
            &s["u"]["fh"]
        }),
        sd: pct(if raw["version"] == 1 && s.get("u").is_none() {
            &s["sd"]
        } else {
            &s["u"]["sd"]
        }),
    })
}

/// Keep the actual observation. A closed local profile cannot observe usage on
/// other devices or know the current reset time, so age must never invent 0%.
fn as_of_now(s: Sample) -> Usage {
    let age = crate::now_ms() - s.at;
    Usage {
        at: s.at,
        age,
        fh: s.fh,
        sd: s.sd,
        source: "local".into(),
        fh_reset_at: None,
        sd_reset_at: None,
        fh_reset_text: None,
        sd_reset_text: None,
    }
}

/// Latest usage for a profile, or None if Claude has never recorded any.
fn read_local(dir: &Path) -> Option<Usage> {
    let identity = identity(dir)?;
    let file = dir.join("plan-usage-history.json");
    let st = std::fs::symlink_metadata(&file).ok()?;
    if st.file_type().is_symlink() {
        return None;
    }
    let key = (st.modified().ok(), st.len());

    let mut cache = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    let cache = cache.get_or_insert_default();
    if let Some((m, n, account, org, v)) = cache.get(dir)
        && (*m, *n) == key
        && account == &identity.account
        && org == &identity.organization
    {
        return v.map(as_of_now);
    }
    let value = newest(&file, &identity.organization);
    cache.insert(
        dir.to_path_buf(),
        (key.0, key.1, identity.account, identity.organization, value),
    );
    value.map(as_of_now)
}

pub fn invalidate(dir: &Path) {
    if let Some(cache) = CACHE.lock().unwrap_or_else(|e| e.into_inner()).as_mut() {
        cache.remove(dir);
    }
}

pub fn read(dir: &Path) -> Option<Usage> {
    read_local(dir)
}

pub fn newest_reading(local: Option<Usage>, live: Option<Usage>) -> Option<Usage> {
    let mut picked = match (local, live) {
        (Some(a), Some(b)) => Some(if a.at > b.at { a } else { b }),
        (a, b) => a.or(b),
    };
    if let Some(value) = picked.as_mut() {
        value.age = (crate::now_ms() - value.at).max(0);
    }
    picked
}

#[cfg(test)]
mod tests {
    //! The non-obvious part is not reading the file, it is what a sample still
    //! means some time after it was taken.

    use super::*;
    use crate::now_ms;

    fn panel(at: i64) -> Usage {
        Usage {
            at,
            age: 0,
            fh: Some(72),
            sd: None,
            source: "claude-ui".into(),
            fh_reset_at: None,
            sd_reset_at: None,
            fh_reset_text: None,
            sd_reset_text: None,
        }
    }

    #[test]
    fn missing_limits_are_unknown_and_not_zero() {
        let u = read(&profile(
            serde_json::json!([{ "t": now_ms(), "u": { "fh": 9 } }]),
        ))
        .unwrap();
        assert_eq!((u.fh, u.sd), (Some(9), None));
    }

    #[test]
    fn the_latest_observation_wins_across_sources() {
        let live = panel(now_ms());
        let local = Usage {
            at: live.at - 1,
            source: "local".into(),
            ..live.clone()
        };
        assert_eq!(
            newest_reading(Some(local), Some(live.clone()))
                .unwrap()
                .source,
            "claude-ui"
        );
        let local = Usage {
            at: live.at + 1,
            source: "local".into(),
            ..live.clone()
        };
        assert_eq!(
            newest_reading(Some(local), Some(live)).unwrap().source,
            "local"
        );
    }

    /// A throwaway profile directory holding the given samples.
    fn profile(samples: Value) -> PathBuf {
        let dir = crate::platform::temp_dir("case-usage-").unwrap();
        std::fs::write(
            dir.join("config.json"),
            r#"{"lastKnownAccountUuid":"account-a","lastKnownOrganizationUuid":"o"}"#,
        )
        .unwrap();
        let mut samples = samples;
        if let Some(rows) = samples.as_array_mut() {
            for row in rows {
                if let Some(object) = row.as_object_mut() {
                    object
                        .entry("org")
                        .or_insert_with(|| Value::String("o".into()));
                }
            }
        }
        std::fs::write(
            dir.join("plan-usage-history.json"),
            serde_json::json!({ "version": 1, "samples": samples }).to_string(),
        )
        .unwrap();
        dir
    }

    #[test]
    fn a_fresh_sample_is_reported_as_it_was_recorded() {
        let u = read(&profile(
            serde_json::json!([{ "t": now_ms() - 60_000, "org": "o", "u": { "fh": 42, "sd": 7 } }]),
        ))
        .unwrap();
        assert_eq!((u.fh, u.sd), (Some(42), Some(7)));
    }

    #[test]
    fn accepts_both_native_history_versions_and_ignores_future_observations() {
        let dir = profile(serde_json::json!([]));
        let file = dir.join("plan-usage-history.json");
        let now = now_ms();
        for (version, sample) in [
            (1, serde_json::json!({"t":now,"org":"o","fh":12,"sd":34})),
            (
                2,
                serde_json::json!({"t":now,"org":"o","u":{"fh":12,"sd":34,"so":90}}),
            ),
        ] {
            std::fs::write(
                &file,
                serde_json::json!({"version":version,"samples":[sample,
                {"t":now+600_000,"u":{"fh":0,"sd":0}}]})
                .to_string(),
            )
            .unwrap();
            invalidate(&dir);
            let value = read(&dir).unwrap();
            assert_eq!((value.fh, value.sd), (Some(12), Some(34)));
            assert_eq!(value.at, now);
        }
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_sample_from_another_organization_is_not_selected() {
        let u = read(&profile(serde_json::json!([
            { "t": now_ms() - 1_000, "org": "other", "u": { "fh": 98, "sd": 98 } },
            { "t": now_ms() - 2_000, "org": "o", "u": { "fh": 12, "sd": 34 } }
        ])));
        assert_eq!(
            (u.as_ref().and_then(|v| v.fh), u.as_ref().and_then(|v| v.sd)),
            (Some(12), Some(34))
        );
    }

    #[test]
    fn an_old_five_hour_sample_does_not_invent_an_unused_current_quota() {
        let u = read(&profile(
            serde_json::json!([{ "t": now_ms() - FIVE_HOURS - 1000, "u": { "fh": 90, "sd": 30 } }]),
        ))
        .unwrap();
        assert_eq!((u.fh, u.sd), (Some(90), Some(30)));
        assert!(u.age >= FIVE_HOURS, "the UI must label this as old data");
    }

    #[test]
    fn an_old_weekly_sample_keeps_its_observed_value_and_age() {
        let u = read(&profile(
            serde_json::json!([{ "t": now_ms() - ONE_WEEK - 1000, "u": { "fh": 90, "sd": 30 } }]),
        ))
        .unwrap();
        assert_eq!((u.fh, u.sd), (Some(90), Some(30)));
        assert!(u.age >= ONE_WEEK);
    }

    #[test]
    fn the_newest_sample_wins_wherever_it_sits_in_the_file() {
        let now = now_ms();
        let u = read(&profile(serde_json::json!([
            { "t": now - 600_000, "u": { "fh": 10, "sd": 1 } },
            { "t": now - 60_000, "u": { "fh": 55, "sd": 5 } },
            { "t": now - 300_000, "u": { "fh": 30, "sd": 3 } }
        ])))
        .unwrap();
        assert_eq!(u.fh, Some(55));
    }

    #[test]
    fn readings_outside_0_to_100_are_clamped_rather_than_shown() {
        let u = read(&profile(
            serde_json::json!([{ "t": now_ms(), "u": { "fh": 140, "sd": -5 } }]),
        ))
        .unwrap();
        assert_eq!((u.fh, u.sd), (Some(100), Some(0)));
    }

    #[test]
    fn a_profile_claude_has_never_recorded_usage_for_reads_as_nothing() {
        assert_eq!(
            read(&crate::platform::temp_dir("case-usage-").unwrap()),
            None
        );
    }

    #[test]
    fn a_corrupt_history_file_is_not_fatal() {
        let dir = crate::platform::temp_dir("case-usage-").unwrap();
        std::fs::write(dir.join("plan-usage-history.json"), "{ not json").unwrap();
        assert_eq!(read(&dir), None);
    }
}
