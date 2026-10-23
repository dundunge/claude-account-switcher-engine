# 本地同步边界 / Local sync boundaries

## Data

`~/.claude/projects` contains the CLI transcript bodies. Claude Desktop's list is a separate
`claude-code-sessions/<account>/<organization>/local_*.json` index inside each login profile.
The engine merges explicitly configured partitions. It carries portable session fields and
historical hierarchy links, including parent/child links. It does not carry account permissions,
MCP credentials or instructions to wake a parent session. No credential or transcript body is
read or copied by the sync engine. The optional usage query helper is a separate component
that reads a session cookie in memory for the official usage request.

## Runtime

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
2. CASE serializes switches. The bridge requests normal Claude exit and checks that it has fully stopped.
3. The engine stages a plan and preserves a snapshot/journal before modifying any index.
4. Incomplete recovery, concurrent switches or unexpected profile changes block the launch.
5. On success CASE opens the chosen official Claude profile.

Do not use two account managers against the same profiles concurrently. Do not symlink Desktop's
account-scoped index. Use the engine's CLI help and review its plan before any recovery or apply.
An unfinished journal must be reviewed and recovered before another write.

## Updating an existing installation

Back up CASE.app and the external engine, bridge and hook before changing them. Check the hook's
engine hash and retain the existing opt-in configuration. This edition does not automate engine
upgrades; replace only the application when the release explicitly says the engine is unchanged.
Never rerun the initial migration tool over an existing installation.

## Acceptance limits

Synthetic tests verify merge, hierarchy, transactions, permissions and rollback without live accounts.
They do not prove compatibility with every later Claude update or transfer a cloud account's privileges.
Official usage queries can require browser verification; do not bypass it. Keep cached data labeled
with its observation time and use Claude's own Usage settings for the current official value.
