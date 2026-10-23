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
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime};

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
}

// The file is ~280 KB and is rewritten every five minutes, but the status poll
// runs every 2.5 seconds. Keyed on mtime and size so the parse happens at
// Claude's rate.
type Cached = (Option<SystemTime>, u64, Option<Sample>);
static CACHE: Mutex<Option<HashMap<PathBuf, Cached>>> = Mutex::new(None);

fn pct(v: &Value) -> Option<u8> {
    v.as_f64()
        .filter(|n| n.is_finite())
        .map(|n| n.round().clamp(0.0, 100.0) as u8)
}

/// The most recent sample in the file, whichever org it belongs to.
fn newest(file: &Path) -> Option<Sample> {
    let raw: Value = serde_json::from_slice(&std::fs::read(file).ok()?).ok()?;
    let mut last: Option<(f64, &Value)> = None;
    for s in raw["samples"].as_array()? {
        if let Some(t) = s["t"].as_f64()
            && last.is_none_or(|(lt, _)| t > lt)
        {
            last = Some((t, s));
        }
    }
    last.map(|(t, s)| Sample {
        at: t as i64,
        fh: pct(&s["u"]["fh"]),
        sd: pct(&s["u"]["sd"]),
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
    }
}

/// Latest usage for a profile, or None if Claude has never recorded any.
fn read_local(dir: &Path) -> Option<Usage> {
    let file = dir.join("plan-usage-history.json");
    let st = std::fs::metadata(&file).ok()?;
    let key = (st.modified().ok(), st.len());

    let mut cache = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    let cache = cache.get_or_insert_default();
    if let Some((m, n, v)) = cache.get(dir)
        && (*m, *n) == key
    {
        return v.map(as_of_now);
    }
    let value = newest(&file);
    cache.insert(dir.to_path_buf(), (key.0, key.1, value));
    value.map(as_of_now)
}

#[derive(Default)]
struct LiveState {
    values: HashMap<PathBuf, Usage>,
    attempts: HashMap<PathBuf, Instant>,
    in_flight: HashSet<PathBuf>,
}

static LIVE: Mutex<Option<LiveState>> = Mutex::new(None);

pub fn invalidate(dir: &Path) {
    if let Some(cache) = CACHE.lock().unwrap_or_else(|e| e.into_inner()).as_mut() {
        cache.remove(dir);
    }
}

pub fn read(dir: &Path) -> Option<Usage> {
    let local = read_local(dir);
    let live = LIVE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .as_ref()
        .and_then(|s| s.values.get(dir))
        .cloned();
    newest_reading(local, live)
}

fn newest_reading(local: Option<Usage>, live: Option<Usage>) -> Option<Usage> {
    let mut picked = match (local, live) {
        (Some(a), Some(b)) => Some(if a.at > b.at { a } else { b }),
        (a, b) => a.or(b),
    };
    if let Some(value) = picked.as_mut() {
        value.age = (crate::now_ms() - value.at).max(0);
    }
    picked
}

#[cfg(any(target_os = "macos", test))]
fn parse_live(value: Value) -> Result<Usage, String> {
    if value["ok"] != true {
        let code = value["code"].as_str().unwrap_or("INVALID_RESPONSE");
        return Err(
            if code.len() <= 48
                && code
                    .bytes()
                    .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
            {
                code.into()
            } else {
                "INVALID_RESPONSE".into()
            },
        );
    }
    let mut usage: Usage =
        serde_json::from_value(value["usage"].clone()).map_err(|_| "INVALID_RESPONSE")?;
    if usage.source != "official"
        || (usage.fh.is_none() && usage.sd.is_none())
        || usage.fh.is_some_and(|v| v > 100)
        || usage.sd.is_some_and(|v| v > 100)
        || crate::now_ms().abs_diff(usage.at) > 120_000
    {
        return Err("INVALID_RESPONSE".into());
    }
    usage.age = (crate::now_ms() - usage.at).max(0);
    Ok(usage)
}

pub fn refresh(dir: &Path) -> Result<Usage, String> {
    refresh_with(dir, || query_official(dir))
}

fn refresh_with(
    dir: &Path,
    query: impl FnOnce() -> Result<Usage, String>,
) -> Result<Usage, String> {
    {
        let mut state = LIVE.lock().unwrap_or_else(|e| e.into_inner());
        let state = state.get_or_insert_default();
        if state.in_flight.contains(dir) {
            return Err("REFRESH_IN_PROGRESS".into());
        }
        if state
            .attempts
            .get(dir)
            .is_some_and(|t| t.elapsed() < Duration::from_secs(60))
        {
            return Err("REFRESH_COOLDOWN".into());
        }
        state.attempts.insert(dir.into(), Instant::now());
        state.in_flight.insert(dir.into());
    }
    let result = query();
    let mut state = LIVE.lock().unwrap_or_else(|e| e.into_inner());
    let state = state.get_or_insert_default();
    state.in_flight.remove(dir);
    if let Ok(value) = &result {
        state.values.insert(dir.into(), value.clone());
    }
    result
}

#[cfg(target_os = "macos")]
fn query_official(dir: &Path) -> Result<Usage, String> {
    let args: Vec<std::ffi::OsString> = vec![
        "-I".into(),
        "-c".into(),
        include_str!("../support/usage_refresh.py").into(),
        "--profile".into(),
        dir.as_os_str().into(),
    ];
    let output = crate::platform::run("/usr/bin/python3", &args, Duration::from_secs(32));
    if !output.ok {
        return Err("HELPER_FAILED".into());
    }
    parse_live(serde_json::from_str(&output.out).map_err(|_| "INVALID_RESPONSE")?)
}

#[cfg(not(target_os = "macos"))]
fn query_official(_dir: &Path) -> Result<Usage, String> {
    Err("PLATFORM_UNSUPPORTED".into())
}

#[cfg(test)]
mod tests {
    //! The non-obvious part is not reading the file, it is what a sample still
    //! means some time after it was taken.

    use super::*;
    use crate::now_ms;

    fn official(at: i64) -> Usage {
        Usage {
            at,
            age: 0,
            fh: Some(72),
            sd: None,
            source: "official".into(),
            fh_reset_at: None,
            sd_reset_at: None,
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
    fn an_official_failure_keeps_the_last_successful_reading() {
        let dir = profile(serde_json::json!([]));
        let value = official(now_ms());
        LIVE.lock()
            .unwrap()
            .get_or_insert_default()
            .values
            .insert(dir.clone(), value.clone());
        assert_eq!(
            refresh_with(&dir, || Err("HTTP_403".into())),
            Err("HTTP_403".into())
        );
        assert_eq!(read(&dir).unwrap().fh, value.fh);
    }

    #[test]
    fn cooldown_and_inflight_guards_are_isolated_by_profile() {
        let a = profile(serde_json::json!([]));
        let b = profile(serde_json::json!([]));
        let value = official(now_ms());
        assert!(refresh_with(&a, || Ok(value.clone())).is_ok());
        assert_eq!(
            refresh_with(&a, || panic!("cooldown must not query")),
            Err("REFRESH_COOLDOWN".into())
        );
        assert!(refresh_with(&b, || Ok(value.clone())).is_ok());
        let c = profile(serde_json::json!([]));
        LIVE.lock()
            .unwrap()
            .get_or_insert_default()
            .in_flight
            .insert(c.clone());
        assert_eq!(
            refresh_with(&c, || panic!("inflight must not query")),
            Err("REFRESH_IN_PROGRESS".into())
        );
    }

    #[test]
    fn live_responses_reject_stale_data_invalid_numbers_and_unsanitized_errors() {
        let valid = serde_json::json!({"ok":true, "usage": official(now_ms())});
        assert!(parse_live(valid.clone()).is_ok());
        let mut bad = valid.clone();
        bad["usage"]["at"] = i64::MIN.into();
        assert!(parse_live(bad).is_err());
        let mut bad = valid;
        bad["usage"]["fh"] = 101.into();
        assert!(parse_live(bad).is_err());
        assert_eq!(
            parse_live(serde_json::json!({"ok":false, "code":"secret:private"})),
            Err("INVALID_RESPONSE".into())
        );
    }

    #[test]
    fn the_latest_observation_wins_across_sources() {
        let live = official(now_ms());
        let local = Usage {
            at: live.at - 1,
            source: "local".into(),
            ..live.clone()
        };
        assert_eq!(
            newest_reading(Some(local), Some(live.clone()))
                .unwrap()
                .source,
            "official"
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
