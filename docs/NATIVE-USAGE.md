# 从 Claude 读取额度 / Read quota inside Claude

This document describes the existing panel-only fallback. The current source also adds
[manual multi-account queries](MANUAL-USAGE.md) through separately authorized official CLI scopes.
The panel path uses macOS Accessibility to read the quota panel that Claude itself renders.
This panel path has no separate usage login, HTTP usage client, browser-cookie import or OAuth refresh.
Claude remains responsible for authenticating and obtaining its own usage data.

## Flow

1. Sign in normally in a CASE-managed Claude profile and open its Code view.
2. In CASE, choose Usage → Read from Claude. On first use, grant CASE Accessibility access in macOS settings.
   This is a local application permission, shared by all CASE accounts. An app replacement or macOS update
   may require granting it again; it is not an account authorization.
3. CASE identifies the profile's main Claude process by open profile files and its executable path.
   Missing or ambiguous processes stop the read; CASE never launches an inactive account for this action.
4. CASE discovers the Usage control from the application tree, then reads labeled quota indicators only
   inside the Usage popover subtree. If needed, it presses Claude's existing Usage popover control,
   reads it, then presses the same control to close it. It never sends Escape, text or a model request.
   An already open panel stays open. Read actions are serialized to prevent competing popover operations.
5. Only the current session and Weekly · all models indicators map to five-hour and weekly usage.
   Model-specific limits and context utilization do not substitute for these windows.
6. Observations are stored in `~/Library/Application Support/CASE/native-usage/` with profile path and a hash
   of the account identity from Claude's config. Identity is checked before and after capture and whenever
   a saved observation is restored. No credential or chat text is saved.

With no independent CLI binding, users can read an open profile's panel or explicitly re-read saved records.
Switching accounts is still a separate action. New source includes sync engine 0.4.0; existing macOS 0.3.0 hooks remain compatible and are not automatically replaced. See CROSS-PLATFORM.md.

## Limits

- A panel read is an observation, not proof that Claude has just refreshed its server-side quota.
- Claude may retain stale UI values or change its labels and accessibility structure after an update.
  An unrecognized panel returns an error with no usage value for that refresh; the persisted observation
  is left untouched so a later successful local read can still inspect it.
- A closed account has no current UI to read. Its last value must remain labeled with its observation time.
- Claude's history file may be much older than its panel. Re-reading it cannot make a new sample appear.
- Reset notices retain their original text. Recognized relative notices are anchored to the original observation
  and displayed as estimated resets; repainting does not move the target time. Full dates, supported local
  clocks and weekday notices show local reset dates in their tooltip. Unknown or ambiguous times stay unavailable.
  Passed reset times request a manual query; they do not reset the sampled percentage to zero.
- This version supports Claude Code's Usage popover on macOS. Other Claude views may require opening Code
  and its Usage control first. No automatic background UI clicking is scheduled.
- macOS Accessibility is a broad permission. The implementation skips chat-message subtrees and text inputs,
  persists only quota fields and has no chat-content export path. It does not change macOS permissions itself.

## Verification

Offline tests cover window mapping, invalid/conflicting indicators, unknown windows and account-bound cache
restoration. Live acceptance requires granting CASE Accessibility access and comparing its output with Claude.
Reading the same panel with a separate UI tool establishes availability, not acceptance of CASE's own reader.

The current source changes have offline coverage only. Before release, perform a separate macOS acceptance
run with an already signed-in profile and record the exact App, engine and bridge hashes. That run should
cover an already open Usage popover, CASE opening and closing it, a cold Claude renderer startup, a second
profile, and more than one Claude window. Compare both quota windows with Claude's own panel, confirm the
observation timestamp is retained by a local reread, and record any Accessibility prompt or unrecognized
panel result as a failure requiring investigation. Do not treat synthetic tests or an old App bundle as
evidence that the new reader has passed live acceptance.
