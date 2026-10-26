//! Opt-in quota refresh owned by one backend worker, independent of UI windows.
use crate::{store, usage_monitor};
use serde::{Deserialize, Serialize};
use std::sync::{Condvar, LazyLock, Mutex, Once};
use std::time::Duration;

pub const MIN_MINUTES: u32 = 5;
pub const MAX_MINUTES: u32 = 7 * 24 * 60;
const MINUTE_MS: i64 = 60_000;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", default)]
pub struct Config {
    pub enabled: bool,
    pub interval_minutes: u32,
    pub next_at: Option<i64>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            enabled: false,
            interval_minutes: 120,
            next_at: None,
        }
    }
}

impl Config {
    fn interval_ms(&self) -> Option<i64> {
        (self.enabled && valid_interval(self.interval_minutes))
            .then_some(i64::from(self.interval_minutes) * MINUTE_MS)
    }
}

pub fn valid_interval(minutes: u32) -> bool {
    (MIN_MINUTES..=MAX_MINUTES).contains(&minutes)
}

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunSummary {
    started_at: i64,
    finished_at: i64,
    succeeded: usize,
    failed: usize,
    skipped: usize,
    interrupted: bool,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    version: u64,
    #[serde(flatten)]
    config: Config,
    running: bool,
    last_run: Option<RunSummary>,
    code: Option<&'static str>,
}

#[derive(Default)]
struct Runtime {
    revision: u64,
    version: u64,
    running: bool,
    last_run: Option<RunSummary>,
    code: Option<&'static str>,
}

static RUNTIME: LazyLock<(Mutex<Runtime>, Condvar)> =
    LazyLock::new(|| (Mutex::new(Runtime::default()), Condvar::new()));
static START: Once = Once::new();

#[derive(Debug, PartialEq)]
enum Plan {
    Idle,
    Arm(i64),
    Run(i64),
}

fn plan(config: &Config, now: i64) -> Plan {
    let Some(interval) = config.interval_ms() else {
        return Plan::Idle;
    };
    let next = now.saturating_add(interval);
    match config.next_at {
        // An absent/invalid deadline is armed, never treated as an immediate query.
        // Also repair a deadline beyond a full interval after a backward clock jump.
        None => Plan::Arm(next),
        Some(at) if at <= 0 || at > next => Plan::Arm(next),
        Some(at) if at > now => Plan::Idle,
        // One overdue cycle, with the next deadline anchored to now. No catch-up loop.
        Some(_) => Plan::Run(next),
    }
}

pub fn snapshot() -> Snapshot {
    let runtime = RUNTIME.0.lock().unwrap_or_else(|e| e.into_inner());
    let mut config = store::settings().usage_schedule;
    if !valid_interval(config.interval_minutes) {
        config = Config::default();
    }
    Snapshot {
        version: runtime.version,
        config,
        running: runtime.running,
        last_run: runtime.last_run.clone(),
        code: runtime.code,
    }
}

pub fn configure(enabled: bool, interval_minutes: u32) -> Result<Snapshot, String> {
    if !valid_interval(interval_minutes) {
        return Err("USAGE_SCHEDULE_INTERVAL_INVALID".into());
    }
    let config = Config {
        enabled,
        interval_minutes,
        next_at: enabled.then(|| crate::now_ms() + i64::from(interval_minutes) * MINUTE_MS),
    };
    {
        let mut runtime = RUNTIME.0.lock().unwrap_or_else(|e| e.into_inner());
        // Persist before acknowledging or letting a worker use the new setting.
        store::edit(|c| c.settings.usage_schedule = config)?;
        runtime.revision += 1;
        runtime.version += 1;
        runtime.code = None;
    }
    RUNTIME.1.notify_one();
    Ok(snapshot())
}

// Save the next deadline BEFORE allowing any request. The injected save also
// makes write failure and stale-setting races verifiable without live config.
fn claim(
    runtime: &mut Runtime,
    observed: Config,
    now: i64,
    save: impl FnOnce(&Config, &Config) -> Result<bool, String>,
) -> Option<(u64, Config)> {
    if runtime.running {
        return None;
    }
    let action = plan(&observed, now);
    let next = match action {
        Plan::Idle => return None,
        Plan::Arm(at) | Plan::Run(at) => at,
    };
    let updated = Config {
        next_at: Some(next),
        ..observed.clone()
    };
    match save(&observed, &updated) {
        Err(_) => {
            runtime.code = Some("USAGE_SCHEDULE_SAVE_FAILED");
            runtime.version += 1;
            return None;
        }
        Ok(false) => return None,
        Ok(true) => runtime.code = None,
    }
    runtime.version += 1;
    if matches!(action, Plan::Run(_)) {
        runtime.running = true;
        Some((runtime.revision, updated))
    } else {
        None
    }
}

fn run_batch(
    ids: &[String],
    mut active: impl FnMut() -> bool,
    mut query: impl FnMut(&str) -> Option<bool>,
) -> RunSummary {
    let mut summary = RunSummary::default();
    for (index, id) in ids.iter().enumerate() {
        if !active() {
            summary.interrupted = true;
            summary.skipped += ids.len() - index;
            break;
        }
        // Synchronous calls preserve serial order; a failure never retries.
        match query(id) {
            Some(true) => summary.succeeded += 1,
            Some(false) => summary.failed += 1,
            None => summary.skipped += 1,
        }
    }
    summary
}

fn tick() {
    let started_at = crate::now_ms();
    let ticket = {
        let mut runtime = RUNTIME.0.lock().unwrap_or_else(|e| e.into_inner());
        let observed = store::settings().usage_schedule;
        claim(&mut runtime, observed, started_at, |before, after| {
            store::edit(|c| {
                if &c.settings.usage_schedule != before {
                    return false;
                }
                c.settings.usage_schedule = after.clone();
                true
            })
        })
    };
    let Some((revision, config)) = ticket else {
        return;
    };
    let ids: Vec<_> = store::load()
        .accounts()
        .iter()
        .map(|a| a.id.clone())
        .collect();
    let mut summary = run_batch(
        &ids,
        || {
            let runtime = RUNTIME.0.lock().unwrap_or_else(|e| e.into_inner());
            runtime.revision == revision && store::settings().usage_schedule == config
        },
        |id| {
            // Re-resolve every id: removed/archived accounts are never queried.
            let account = store::load()
                .accounts()
                .iter()
                .find(|a| a.id == id)
                .cloned()?;
            usage_monitor::refresh_scheduled(
                &account,
                i64::from(config.interval_minutes) * MINUTE_MS,
            )
            .map(|view| view.status == "success")
        },
    );
    summary.started_at = started_at;
    summary.finished_at = crate::now_ms();
    let mut runtime = RUNTIME.0.lock().unwrap_or_else(|e| e.into_inner());
    runtime.running = false;
    summary.interrupted |= runtime.revision != revision;
    runtime.last_run = Some(summary);
    runtime.version += 1;
}

pub fn start() {
    START.call_once(|| {
        std::thread::spawn(|| {
            loop {
                tick();
                let runtime = RUNTIME.0.lock().unwrap_or_else(|e| e.into_inner());
                let _ = RUNTIME
                    .1
                    .wait_timeout(runtime, Duration::from_secs(15))
                    .unwrap_or_else(|e| e.into_inner());
            }
        });
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn enabled(minutes: u32, next_at: Option<i64>) -> Config {
        Config {
            enabled: true,
            interval_minutes: minutes,
            next_at,
        }
    }

    #[test]
    fn upgrades_are_off_by_default_and_invalid_intervals_cannot_start_queries() {
        assert_eq!(plan(&Config::default(), 10_000), Plan::Idle);
        for minutes in [0, 1, 4, MAX_MINUTES + 1, u32::MAX] {
            assert!(!valid_interval(minutes));
            assert_eq!(plan(&enabled(minutes, Some(1)), 10_000), Plan::Idle);
        }
        for minutes in [5, 30, 120, 300, MAX_MINUTES] {
            assert!(valid_interval(minutes));
        }
    }

    #[test]
    fn a_new_or_repaired_schedule_waits_one_full_custom_interval() {
        let now = 1_000_000;
        for minutes in [5, 90, 120, 300, MAX_MINUTES] {
            let next = now + i64::from(minutes) * MINUTE_MS;
            for missing in [None, Some(0), Some(-1), Some(i64::MAX)] {
                assert_eq!(plan(&enabled(minutes, missing), now), Plan::Arm(next));
            }
            assert_eq!(plan(&enabled(minutes, Some(next)), now), Plan::Idle);
        }
    }

    #[test]
    fn sleep_or_restart_misses_only_one_cycle_and_persists_the_next_before_running() {
        let now = 10_000_000_000;
        let mut runtime = Runtime::default();
        let mut saved = None;
        let ticket = claim(&mut runtime, enabled(120, Some(1000)), now, |_, next| {
            saved = Some(next.clone());
            Ok(true)
        })
        .unwrap();
        assert_eq!(ticket.1, saved.unwrap());
        assert_eq!(ticket.1.next_at, Some(now + 120 * MINUTE_MS));
        assert!(runtime.running);
        assert_eq!(plan(&ticket.1, now + 1), Plan::Idle);
        assert!(
            claim(&mut runtime, enabled(120, Some(1000)), now, |_, _| panic!(
                "overlap"
            ))
            .is_none()
        );
    }

    #[test]
    fn failed_or_stale_schedule_save_cannot_authorize_a_query() {
        let mut runtime = Runtime::default();
        let config = enabled(300, Some(1));
        assert!(
            claim(&mut runtime, config.clone(), 1000, |_, _| Err(
                "disk full".into()
            ))
            .is_none()
        );
        assert!(!runtime.running);
        assert_eq!(runtime.code, Some("USAGE_SCHEDULE_SAVE_FAILED"));
        assert!(claim(&mut runtime, config, 1000, |_, _| Ok(false)).is_none());
        assert!(!runtime.running);
    }

    #[test]
    fn batch_continues_once_per_account_after_failures_and_skips() {
        let ids: Vec<String> = ["ok", "failed", "busy", "deleted", "ok2"]
            .map(Into::into)
            .into();
        let mut calls = Vec::new();
        let result = run_batch(
            &ids,
            || true,
            |id| {
                calls.push(id.to_owned());
                match id {
                    "ok" | "ok2" => Some(true),
                    "failed" => Some(false),
                    _ => None,
                }
            },
        );
        assert_eq!(calls, ids);
        assert_eq!((result.succeeded, result.failed, result.skipped), (2, 1, 2));
    }

    #[test]
    fn disabling_or_changing_the_schedule_stops_remaining_accounts() {
        use std::cell::Cell;
        let active = Cell::new(true);
        let ids: Vec<String> = ["a", "b", "c"].map(Into::into).into();
        let result = run_batch(
            &ids,
            || active.get(),
            |_| {
                active.set(false);
                Some(true)
            },
        );
        assert_eq!((result.succeeded, result.skipped), (1, 2));
        assert!(result.interrupted);
    }
}
