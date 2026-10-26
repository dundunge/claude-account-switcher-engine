'use strict'

// All state is invented and stays in this page. No native IPC, credentials,
// storage, model requests or real Claude processes are available here.
const demoParams = new URLSearchParams(location.search)
const demoEnglish = demoParams.get('lang') === 'en'
window.CASE_LOCALE = demoEnglish ? 'en' : 'zh-CN'
window.CASE_SECTION = demoParams.get('section') || 'usage'
document.documentElement.dataset.demoTheme = demoParams.get('theme') === 'light' ? 'light' : 'dark'
const demoHandlers = new Map()
const demoNow = Date.now()
let demoVersion = 10
const demoAccounts = [
  ['daily', demoEnglish ? 'Personal' : '主力号', 'sparkles', 24, 36, 23, 42, true, 186],
  ['build', demoEnglish ? 'Build' : '开发号', 'code', 210, 67, 84, 168, false, 94],
  ['backup', demoEnglish ? 'Backup' : '备用号', 'leaf', 145, 8, 16, 256, false, 32]
].map(([id, name, icon, hue, fh, sd, minutes, running, sessions], idx) => {
  const usage = { at: demoNow - 120000, age: 0, source: 'claude-cli', fh, sd,
    fhResetAt: demoNow + minutes * 60000, sdResetAt: demoNow + (2 + idx) * 86400000,
    fhResetEstimated: false, sdResetEstimated: false }
  return { id, name, icon, hue, idx, running, sessions, exists: true,
    dir: '/Demo/Claude-' + id, lastUsedAt: demoNow - (idx + 1) * 3600000,
    usage, monitor: { configured: true, version: ++demoVersion, status: 'success', usage, lastAttemptAt: usage.at } }
})
const demoSettings = { version: '4.2.1', language: window.CASE_LOCALE,
  hotkey: 'Alt+Command+C', defaultHotkey: 'Alt+Command+C', hotkeyEnabled: true,
  menuBar: true, hideDock: false, openAtLogin: false, sortByRecent: false,
  autoUpdate: false, syncEdition: 'CASE_LOCAL_SYNC_EDITION_V1',
  dataDir: '/Demo/CASE', backupsDir: '/Demo/Backups' }
const demoCopy = value => JSON.parse(JSON.stringify(value))
const demoEmit = (name, payload) => (demoHandlers.get(name) || []).forEach(cb => cb({ payload: demoCopy(payload) }))

async function demoInvoke (command, args = {}) {
  switch (command) {
    case 'accounts_list': return demoCopy({ needsSetup: false, accounts: demoAccounts, archived: [], chromeProfiles: [] })
    case 'accounts_status': return demoCopy(demoAccounts)
    case 'app_claude_installed': return { installed: true, hint: '' }
    case 'settings_get': return demoCopy(demoSettings)
    case 'health_scan': return { accounts: demoAccounts.map(a => ({ id: a.id, name: a.name, orphans: [], failures: null })) }
    case 'settings_set_language':
      demoSettings.language = args.language
      demoEmit('language:changed', args.language)
      return demoCopy(demoSettings)
    case 'settings_open':
      location.href = '/app/settings.html?' + new URLSearchParams({ lang: window.CASE_LOCALE, theme: document.documentElement.dataset.demoTheme, section: args.section || 'usage' })
      return null
    case 'usage_refresh': {
      const account = demoAccounts.find(a => a.id === args.id)
      if (!account) return { ok: false, status: 'failed', code: 'ACCOUNT_NOT_FOUND', usage: null }
      const usage = { ...account.usage, at: Date.now() }
      account.usage = null
      account.monitor = { ...account.monitor, version: ++demoVersion, status: 'in_progress', usage: null, lastAttemptAt: Date.now() }
      await new Promise(resolve => setTimeout(resolve, 450))
      const failed = demoParams.get('scenario') === 'failed' && account.id === 'build'
      account.usage = failed ? null : usage
      account.monitor = { ...account.monitor, version: ++demoVersion, status: failed ? 'unavailable' : 'success', code: failed ? 'CLI_USAGE_UNAVAILABLE' : null, usage: account.usage }
      return demoCopy({ ok: !failed, status: account.monitor.status, code: account.monitor.code, usage: account.usage, monitor: account.monitor })
    }
    case 'accounts_launch':
      demoAccounts.forEach(a => { a.running = a.id === args.id })
      demoEmit('accounts:changed', {})
      return { ok: true }
    case 'app_dismiss': case 'app_hide': return null
    case 'update_check': return { ok: true, available: false, packaged: false, version: '4.2.1' }
    default: return { ok: false, error: demoEnglish ? 'Demo only — no system action is performed.' : '这是演示页面，未执行系统操作。' }
  }
}
window.__TAURI__ = { core: { invoke: demoInvoke }, event: {
  listen: async (name, callback) => {
    if (!demoHandlers.has(name)) demoHandlers.set(name, [])
    demoHandlers.get(name).push(callback)
    return () => demoHandlers.set(name, demoHandlers.get(name).filter(cb => cb !== callback))
  }
} }
document.addEventListener('DOMContentLoaded', () => {
  const chrome = document.createElement('div')
  chrome.className = 'demo-window-chrome'
  chrome.setAttribute('aria-hidden', 'true')
  chrome.innerHTML = '<i></i><i></i><i></i>'
  const stamp = document.createElement('div')
  stamp.className = 'demo-window-stamp'
  stamp.textContent = demoEnglish ? 'CASE · DEMO' : 'CASE · 演示'
  document.body.append(chrome, stamp)
})
