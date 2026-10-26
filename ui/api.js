'use strict'

// The surface both windows talk to: one method per command in src/main.rs, and
// one subscription per event it emits. tests/bridge.rs holds this file, the
// pages and the Rust side to the same names, so a renamed command fails there
// rather than in a menu nobody opened.

const { invoke } = window.__TAURI__.core
const { listen } = window.__TAURI__.event

/** Subscribe to an event's payload. Returns its own remover. */
const on = (event, cb) => {
  const pending = listen(event, e => cb(e.payload))
  return () => { pending.then(off => off()) }
}

window.api = {
  list: () => invoke('accounts_list'),
  status: () => invoke('accounts_status'),
  adopt: picked => invoke('accounts_adopt', { picked }),
  add: name => invoke('accounts_add', { name }),
  update: (id, fields) => invoke('accounts_update', { id, ...fields }),
  remove: (id, deleteData, confirm) => invoke('accounts_remove', { id, deleteData, confirm }),
  launch: id => invoke('accounts_launch', { id }),
  size: id => invoke('accounts_size', { id }),
  archiveAccount: id => invoke('accounts_archive', { id }),
  archiveRestore: id => invoke('archive_restore', { id }),
  archiveDelete: (id, confirm) => invoke('archive_delete', { id, confirm }),
  reveal: id => invoke('accounts_reveal', { id }),
  quitAccount: id => invoke('accounts_quit', { id }),
  hide: () => invoke('app_hide'),
  dismiss: () => invoke('app_dismiss'),
  showHealth: () => invoke('app_show_health'),
  openLink: which => invoke('app_open_link', { which }),
  claudeInstalled: () => invoke('app_claude_installed'),
  openSettings: section => invoke('settings_open', { section }),
  getSettings: () => invoke('settings_get'),
  getUsageSchedule: () => invoke('usage_schedule_status'),
  setUsageSchedule: (enabled, intervalMinutes) => invoke('settings_set_usage_schedule', { enabled, intervalMinutes }),
  setLanguage: language => invoke('settings_set_language', { language }),
  refreshUsage: (id, native = true) => invoke('usage_refresh', { id, native }),
  connectUsageMonitor: id => invoke('usage_monitor_connect', { id }),
  setupUsageMonitor: id => invoke('usage_monitor_setup', { id }),
  setHotkey: (hotkey, enabled) => invoke('settings_set_hotkey', { hotkey, enabled }),
  setPresentation: (menuBar, hideDock) => invoke('settings_set_presentation', { menuBar, hideDock }),
  setSorting: sortByRecent => invoke('settings_set_sorting', { sortByRecent }),
  setLoginItem: enabled => invoke('settings_set_login_item', { enabled }),
  setAutoUpdate: enabled => invoke('settings_set_auto_update', { enabled }),
  shortcutStatus: () => invoke('shortcut_status'),
  shortcutCreate: () => invoke('shortcut_create'),
  chromeList: accountId => invoke('chrome_list', { accountId }),
  chromePair: (accountId, dir, openOnLaunch) => invoke('chrome_pair', { accountId, dir, openOnLaunch }),
  chromeExtension: dir => invoke('chrome_extension', { dir }),
  chromeNewProfile: () => invoke('chrome_new_profile'),
  scanHealth: () => invoke('health_scan'),
  rebuildSession: (accountId, orphan) => invoke('health_rebuild', { accountId, orphan }),
  revealPath: target => invoke('health_reveal', { target }),
  updateCheck: () => invoke('update_check'),
  updateInstall: () => invoke('update_install'),
  onAccountsChanged: cb => on('accounts:changed', () => cb()),
  onLaunchStage: cb => on('account:launch-stage', cb),
  onHealthUpdate: cb => on('health:update', cb),
  onShowHealth: cb => on('health:show', () => cb()),
  onNewAccount: cb => on('account:new', () => cb()),
  onSettingsSection: cb => on('settings:section', cb),
  onLanguageChanged: cb => on('language:changed', cb),
  onUpdateAvailable: cb => on('update:available', cb),
  // An install can be attempted more than once, and each attempt's callback
  // writes into a card that may be gone by the next — hence the remover.
  onUpdateProgress: cb => on('update:progress', cb),
  // Not a command: the pages need this on every keystroke, to tell Ctrl from
  // Command.
  platform: navigator.userAgent.includes('Windows') ? 'win32' : 'darwin'
}
