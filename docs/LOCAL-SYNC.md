# 本地同步边界 / Local sync boundaries

## Data

`~/.claude/projects` contains the CLI transcript bodies. Claude Desktop's list is a separate
`claude-code-sessions/<account>/<organization>/local_*.json` index inside each login profile.
The engine merges explicitly configured partitions. It carries portable session fields and
historical hierarchy links, including parent/child links. It does not carry account permissions,
MCP credentials or instructions to wake a parent session. No credential or transcript body is
read or copied by the sync engine. Quota queries are separate: each account can explicitly authorize an
isolated official CLI, then run `/usage` on demand without switching Desktop. CASE also retains the
macOS Accessibility panel fallback for an open account. It reads no login credentials.
See [MANUAL-USAGE.md](MANUAL-USAGE.md) and [NATIVE-USAGE.md](NATIVE-USAGE.md).

## Runtime

跨平台 0.4.0 新安装见 [CROSS-PLATFORM.md](CROSS-PLATFORM.md)。以下路径为 macOS；Windows 使用 `%APPDATA%`，Linux 使用 XDG 配置目录。

- Engine: `~/Library/Application Support/ClaudeSessionSync/engine/claude_session_sync.py`
- Bridge: `~/Library/Application Support/ClaudeSessionSync/CASE/case_sync_bridge.py`
- Hook: `~/Library/Application Support/ClaudeSessionSync/CASE/hook.json`
- CASE list: `~/Library/Application Support/CASE/accounts.json`

The hook points at a reviewed source config and pins the engine by SHA-256. The bridge discovers
new CASE profiles only inside its configured parent directory. Uninitialized profiles can be
opened to complete official sign-in; ambiguous account/organization partitions stop the operation.
Each account must be logged in through Claude itself. Account isolation does not guarantee that a
server session remains valid forever.

## Switching

1. Save work and finish the active Claude task.
2. CASE serializes switches and shows `waiting_exit` while the bridge requests normal Claude exit.
3. It reports `validate`, then the engine stages a plan and preserves a snapshot/journal before `sync` modifies any index.
4. Incomplete recovery, concurrent switches or unexpected profile changes block the launch and retain the snapshot code for recovery.
5. On success CASE reports `launching` and opens the chosen official Claude profile.

Do not use two account managers against the same profiles concurrently. Do not symlink Desktop's
account-scoped index. Use the engine's CLI help and review its plan before any recovery or apply.
An unfinished journal must be reviewed and recovered before another write.

On macOS, the index-sync process guard excludes the exact Squirrel updater path
`Claude.app/Contents/Frameworks/Squirrel.framework/Resources/ShipIt`. That helper updates
the application bundle and may restart after Desktop exits. Claude Desktop and its session
helpers still block index writes, including when the updater is running beside them.

### Existing macOS installations: apply the ShipIt fix

The application uses the external engine pinned by `hook.json`. Updating `CASE.app` alone
does not update that engine. From a terminal at the repository root, preview the targeted fix:

```sh
python3 tools/patch_macos_shipit.py
```

Finish any active Claude task and quit Claude normally before applying it:

```sh
python3 tools/patch_macos_shipit.py --apply
```

The tool supports existing 0.3.0 and 0.4.0 engines, preserves their version and bridge,
backs up the original engine and hook, then updates the engine and its SHA-256 pin together.
It refuses an unexpected hash, customized guard, unsafe path or overlapping sync operation.
An already patched installation returns `unchanged` without writes. The reported backup
directory contains `engine-before.py` and `hook-before.json`; restore these together if a
program rollback is needed, with CASE and Claude closed.

This code update does not recover or clear unfinished transactions. If the preview reports
`unfinished_transactions` above zero, review the corresponding snapshot and matching runtime
config under `state/` before using the pinned engine's `restore` command. Preview recovery
first; only explicitly apply it after reviewing the changes. Recovery conflicts stop rather
than overwrite newer session edits. No automatic index rollback or account launch is performed.

## Updating an existing installation

Back up CASE.app and the external engine, bridge and hook before changing them. Check the hook's
engine hash and retain the existing opt-in configuration. This edition does not automate engine
upgrades; replace only the application when the release explicitly says the engine is unchanged.
Never rerun the initial migration tool over an existing installation.

## Acceptance limits

Synthetic tests verify merge, hierarchy, transactions, permissions and rollback without live accounts.
They do not prove compatibility with every later Claude update or transfer a cloud account's privileges.
Keep saved quota observations labeled with their original time. A UI observation does not prove a new server
fetch. The panel fallback cannot refresh an inactive profile; an independently authorized account can instead
use a manual official CLI query. Neither operation changes the account's quota.
