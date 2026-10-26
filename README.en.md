# CASE Independent Edition

[中文](README.md)

Based on [CASE by Patryk Starostka](https://github.com/pstarostka/claude-account-switcher-engine) 4.1.0, preserving the original MIT license. For original baselines and credits, please refer to [NOTICE.md](NOTICE.md).

This repository is maintained independently: source code, in-app update checks, and releases exclusively point to **[dundunge/claude-account-switcher-engine](https://github.com/dundunge/claude-account-switcher-engine)**. Upstream code is never merged automatically.

---

### Why We Built This

If you use multiple Claude accounts and prefer the official **Claude Desktop** application, repeatedly signing out and in is frustrating, and managing each account's local sessions by hand is cumbersome. I prefer Desktop for conversations and did not want to keep testing reverse-proxy setups with more accounts, so I chose local account switching.

**CASE handles three local tasks:**
Keep working in the official Claude Desktop app while CASE manages **account switching**, **local session index synchronization across accounts**, and **quota queries (manual or optionally scheduled)**.

> **Core principles:** CASE does not provide a reverse proxy or model API relay, or modify Claude application bundles. It preserves the official clients and login flows.
>
> ⚠️ **Subscription scope:** Currently adapted and verified only with **Claude Pro**. The maintainer does not have a Max subscription account; Max has not been adapted or verified.

![CASE English Overview Demo: three accounts with manual usage queries](docs/media/case-overview-en.jpg)

*The demo uses fictional accounts and quota data with the project's actual UI. It does not verify real accounts.*

Want to see more? Check out the [Screenshots and Presentation Gallery](docs/SCREENSHOTS.md), or try the [Interactive Browser Demo](docs/demo/README.md) locally.

---

## Key Highlights

- 🔒 **Separate Account Login Directories**: Each Claude Desktop account uses a separate official profile directory (`--user-data-dir`) for login state. Local session indexes are shared according to the sync configuration.
- ⚡ **Streamlined, Misclick-Resistant UI**:
  - Clicking an account row merely selects it. Each row features a dedicated, fixed "Switch" button to prevent accidental clicks.
  - An inline refresh icon queries that specific account on demand. The top "Refresh All" button runs sequentially—even if one account times out or encounters network trouble, it won't block the rest.
  - Full right-click context menus, bottom action bars, and distinct keyboard shortcuts are ready whenever you need them.
- 🌐 **Chinese and English**: Select a language in `Settings → General`. Both windows and native menus update immediately, and the preference persists across restarts. Account names and other user data are not translated.
- 📊 **Claude-Style Usage Bars**:
  - Shows the **5-hour limit** and **weekly limit (all models)**.
  - Displays each limit's reset notice and **used percentage**, with observation time and source.
  - Hover for full reset dates, time zones and remaining percentages. Calculated reset times are marked as estimates; missing or unrecognized data is explicitly unavailable.
- 🔑 **Isolated, Direct-to-CLI Quota Queries**:
  - Manage per-account queries, authorization, reauthorization, error details and independent CLI launchers in `Settings → Usage queries`.
  - Account authorizations run official Claude Code login flows in your Terminal, directing OAuth to isolated Electron popups. **It never borrows cookies or session tokens from your everyday browser or Desktop app**, and CASE never reads your macOS Keychain secrets.
  - Each profile is assigned an isolated CLI configuration directory, strictly matching account and org UUIDs against your Desktop profile.
- 💻 **Open an Account's Independent CLI**: Launch Terminal in an authorized account's CLI environment and reuse its login. Running `claude` in a regular Terminal still uses the default configuration. Official Claude Code must be installed separately; the authorization window also requires the Electron component. Users confirm initial workspace trust themselves. See the [authorization guide](docs/MANUAL-USAGE.md).
- ⏱️ **Gentle Scheduled Refresh (Optional)**:
  - Off by default—CASE only checks quotas when you ask it to.
  - Set an interval in minutes or hours, from 5 minutes to 7 days in whole minutes. The 2-hour and 5-hour preset buttons and next-refresh time are shown in Settings.
  - Runs only while CASE is running. After wake or reopening CASE, at most one missed cycle runs. Accounts are queried serially; failures return empty readings without immediate retries. Enabling or changing the interval restarts the timer. Turning it off lets the current query finish and stops remaining accounts. See [scheduled refresh](docs/SCHEDULED-USAGE.md).
- 👁️ **Desktop Panel Fallback**: Without an independent CLI binding, Read panel uses macOS Accessibility to observe the open account's Claude Usage popover and may briefly open and close it. Original reset notices are retained, and calculated times stay anchored to the observation instead of moving forward on repaint.
- 📜 **Local History Inspection**: Easily read back historical logs from Claude's local `plan-usage-history.json` and CASE's previous panel observations for diagnostic comparisons.
- 🔄 **Local Session Index Sync**: An independent local engine merges configured Claude Code Desktop account indexes and preserves portable parent/child relationships. Claude retains transcript bodies; index sync does not copy them.
- 🛡️ **Switching and Recovery Status**: Shows waiting, validation, sync and launch stages. Interrupted operations retain a snapshot or transaction identifier; unresolved transactions block further writes and require recovery.

> 📌 **Scope:**
>
> CASE does not modify Claude binaries, upload transcripts, migrate cloud chats, or reset or increase quotas.
>
> Local index sync cannot transfer server-side model context caches across accounts. Use local handoff notes for long tasks.
>
> Detailed reading: [Multi-Account Authorization & Usage](docs/MANUAL-USAGE.md) | [Native Panel Fallback Guide](docs/NATIVE-USAGE.md) (no CodexBar needed).

---

## Why No Reverse Proxies?

CASE is intentionally designed without reverse proxies, API gateway relays, or MITM interception:

1. **Official Clients**: Continue using official Claude Desktop. CASE does not rewrite its API endpoints or modify its application bundle.
2. **No Existing Credential Extraction**: CASE does not read, export or copy existing Desktop or system-browser cookies, tokens or Keychain credentials. The official CLI and each isolated Electron login window store their own account's authorization data.
3. **Official CLI Queries**: The unmodified Claude Code CLI runs `/usage` and manages its own credentials. CASE reads non-credential identity metadata, official login status and quota output. It sends no model chat prompts.
4. **Independent Electron Login Windows**: Each account has a separate, persistent Chromium login directory, reused for its reauthorization; it is not an incognito or disposable session. CASE does not spoof fingerprints, assign proxies, inject page scripts or intercept authorization codes. Users confirm OAuth and workspace trust; the official CLI handles callbacks and credential exchange.

*(Note: "No reverse proxies" refers to CASE's architecture. Official apps and CLIs still respect any system-level proxies or environment variables you have configured; CASE never touches, reloads, or restarts your Clash/Mihomo setups).*

---

## Why Decouple Account Switching from Quota Queries?

Desktop switching and quota queries use different login states and are managed separately:

| Path | Data & Login Scope | Trigger & Runtime Impact |
| :--- | :--- | :--- |
| **Desktop Switching** | Dedicated `--user-data-dir` per account profile | Explicit Switch action; requests normal Claude exit, validates and syncs local indexes, then launches the target profile |
| **Quota Authorization** | Isolated CLI config directory, blank workspace, and Electron container | Configured once explicitly in Settings; verifies account and org UUIDs against Desktop |
| **Quota Querying** | Official CLI `/usage` scoped to the bound account | Manual single refresh, Refresh All, or an explicitly enabled schedule; **no Desktop switch, client exit or automatic login** |

**The Four Reasons Behind This Architecture:**

1. **Different Login States**: A signed-in Desktop profile does not authorize its independent CLI. Each account needs official authorization and initial setup. CASE does not copy Desktop credentials or change the default CLI login to bypass those steps.
2. **Queries Preserve the Active Desktop Session**: Check Account B's quota while working in Account A without closing or switching the current Desktop profile.
3. **Query Failures Are Handled Separately**: Network or service errors clear the current reading and report a reason. Retry, opening the account CLI, reauthorization and switching are separate actions. An account with an existing binding can open its CLI for diagnosis without first reauthorizing.
4. **Identity and Freshness Checks**: Results bind the Desktop profile path, account/org SHA-256 values and binding revision, checked before and after queries. Capture also verifies official request-success evidence and rejects stale fallback percentages after failures. Official same-account snapshots under 60 seconds old retain their original observation time.

Queries distinguish `success`, `auth_failed`, `unavailable`, `in_progress`, `failed` and `timeout`.
Failures return `usage: null`; later status polling cannot restore an old percentage.
Local state polling and time-label repainting do not request quota.

---

## Testing & Verification

Offline tests, installed-app checks and independent audits have separate scopes:

- **Scheduled Refresh Offline Verification (2026-10-26)**: **237 tests passed** (Python 120, Rust unit 76 + bridge 3, Node 38), plus Clippy. New tests use mocks. The browser demo checks interface behavior without a real scheduling worker. This version has been installed locally and its Settings entry checked; real-account queries at scheduled deadlines have not been verified.
- **Grok Audit and Fix Tracking (2026-10-25)**: Grok checked 77 file hashes and reran 15 Node tests against the preceding audit package, without rerunning the complete suite or verifying real accounts. The implementer fixed the panel-lock status, background-activity parsing and multi-window batch-count issues. **Independent re-review is pending**; see the [audit follow-up](docs/AUDIT-FOLLOWUP-20261025.md).
- **Preceding Installed-App Acceptance (`103dd9f`, 2026-10-25)**: That batch passed 214 checks. Manual queries succeeded for three independently authorized Pro accounts, showing both used percentages and reset times; row selection, single/bulk refresh and independent CLI entry points were checked. No real Desktop switch was executed. This does not establish real-account acceptance for later audit fixes or scheduled refresh.

Compatibility currently covers verified Claude Pro environments. Official client changes, network conditions and service rate limits may still affect queries.

---

## Installation, Building & Updating

The complete integration targets **macOS**. Windows source retains compilation checks, but sync and official usage integration are incomplete; no Windows installer is published.

**First use requires a prepared sync configuration, official Claude Code and the Electron authorization component. Building or copying the app alone does not initialize them; there is no general one-click initializer.** See [sync boundaries](docs/LOCAL-SYNC.md) and [authorization setup](docs/MANUAL-USAGE.md).

### Updating Existing Installations
If sync is already configured and the release explicitly states that the external engine is unchanged:

1. Back up the old app, external engine, bridge and hook.
2. Quit CASE normally and replace `~/Applications/CASE.app` with the new build or release.
3. Preserve account settings, `ClaudeSessionSync` and all account login directories. Do not rerun initial migration or force-quit Claude to update CASE.

👉 [Browse Releases on GitHub](https://github.com/dundunge/claude-account-switcher-engine/releases)

---

### Building from Source

Run in macOS Terminal / zsh from this repository's root. Rust and Xcode Command Line Tools are required; install Tauri CLI v2 first if it is not already available:

```zsh
# Install Tauri CLI v2
cargo install tauri-cli --version '^2' --locked

# Build the release bundle
cargo tauri build --bundles app --ci
```

The result is `target/release/bundle/macos/CASE.app`. The app uses a local ad-hoc signature and is not notarized by Apple; follow normal macOS security prompts on first launch.

**Run the Full Verification Suite:**

```zsh
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
python3 -m unittest discover -s tests/python -q
node --test tests/*.test.cjs
```

---

## Local Sync Architecture

Sync scripts and automated test suites are included in the repository:

- `engine/claude_session_sync.py`: Version 0.3.0 index merger, snapshot creator, and rollback engine.
- `tools/case_sync_bridge.py`: Manages profile health checks and safely orchestrates sequential transitions.
- `tools/migrate_cockpit_to_case.py`: **Initial migration for an existing Cockpit sync configuration**, requiring a reviewed `ClaudeSessionSync/config.json`. Previews by default; `--apply` writes changes and refuses to overwrite an existing CASE installation or account directory. Previously migrated users **must not rerun it**.

> ⚠️ **First-Time Sync Prerequisite:**
>
> A valid `ClaudeSessionSync/CASE/hook.json` is required before launching an account. Missing or invalid hooks block launch. After hook validation, uninitialized profiles can open to complete official sign-in.
>
> Claude must exit normally before index writes. An inability to exit or an unresolved transaction stops the operation; CASE does not force-terminate clients. See [local sync boundaries](docs/LOCAL-SYNC.md) for configuration and update constraints.

---

## License

Distributed under the [MIT License](LICENSE).

Original work copyright (c) Patryk Starostka. Independent edition modifications copyright (c) dundunge, available under the same license.

*Disclaimer: This is an independent open-source utility and is not affiliated with, sponsored by, or endorsed by Anthropic.*
