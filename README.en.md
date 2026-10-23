# CASE independent edition

[中文](README.md)

Based on [CASE by Patryk Starostka](https://github.com/pstarostka/claude-account-switcher-engine),
version 4.1.0. The original MIT copyright is retained; see [NOTICE.md](NOTICE.md).
This repository is maintained independently. Update checks, source and release links
use `dundunge/claude-account-switcher-engine` exclusively; upstream is never merged automatically.

## Features

- Separate official Claude Desktop login directories.
- Chinese / English selection in Settings → General, applied to both windows and native menus,
  remembered after restart. Account names and other user data are never translated.
- A usage page with separate five-hour and weekly **used** percentages, observation time and source.
- Read from Claude observes the open account's official Claude Code quota panel, saving used percentages,
  observation time and the original reset notices. Sign in once per account in Claude. CASE performs no extra
  OAuth login and reads no cookies or Keychain credentials. Allow CASE in macOS System Settings → Privacy &
  Security → Accessibility on first use.
- Update usage records reads panels for open accounts and saved records for closed accounts, without switching.
  Reading may briefly open the quota popover and close it afterwards; a popover already open stays open.
  Observation time is not a server update timestamp. Reset notices retain their wording at observation time;
  CASE does not turn them into an inferred live countdown.
- Read saved records reads Claude's `plan-usage-history.json` and CASE's last panel observation. These can be old.
  Closed accounts show their last reading and time; missing limits are unknown.
- An offline local index sync engine, preserving parent and child relationships. Claude retains ownership of transcripts.
- Switching shows waiting, validation, sync and launch stages; an interruption keeps its snapshot identifier for recovery.

No reverse proxy, Claude bundle modification, cloud chat migration or quota reset.
Local index sharing cannot transfer another account's model context cache. Use local handoff files for long tasks.

See [docs/NATIVE-USAGE.md](docs/NATIVE-USAGE.md) for implementation and limitations. CodexBar is not required.

## Build and update

The complete integration currently targets **macOS**. Windows source still receives Rust compilation checks;
local sync and official usage integration have not been completed there, and no Windows artifact is released.

From a Terminal in the repository, with Rust, Xcode Command Line Tools and Tauri CLI installed:

```zsh
cargo install tauri-cli --version '^2' --locked
cargo tauri build --bundles app --ci
```

The result is `target/release/bundle/macos/CASE.app`. Quit CASE normally before replacing
`~/Applications/CASE.app`; preserve all profile directories, `CASE/accounts.json` and `ClaudeSessionSync`.
The sync engine is installed separately. In-app updates only check our releases and open the instructions,
so an application replacement cannot silently omit a required engine migration.
The app is ad-hoc signed, not notarized; use the normal macOS security flow on first launch.

```zsh
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
python3 -m unittest discover -s tests/python -q
node --test tests/*.test.cjs
```

## Local sync setup

See [docs/LOCAL-SYNC.md](docs/LOCAL-SYNC.md). The application fails closed before launching a different
profile if its `ClaudeSessionSync/CASE/hook.json` is missing or invalid.
`tools/migrate_cockpit_to_case.py` is only for an initial migration with an existing, reviewed Cockpit
sync configuration. It previews by default and refuses to overwrite existing CASE profiles or configuration.
There is no general first-run sync installer in this version. Already migrated users must not rerun it.

[MIT licence](LICENSE). This project is not affiliated with Anthropic.
