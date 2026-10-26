// A browser surface only. The official CLI owns OAuth and credentials.
// No preload, page scripting, cookie access, credential export or external opener.
'use strict'
const { app, BrowserWindow, session } = require('electron')
const path = require('node:path')
const fs = require('node:fs')

function initialUrl (value) {
  const url = new URL(value)
  if (url.protocol !== 'https:' || url.username || url.password ||
      !['https://claude.com/cai/oauth/authorize', 'https://claude.ai/oauth/authorize'].includes(url.origin + url.pathname)) throw new Error('url')
  const callbacks = url.searchParams.getAll('redirect_uri')
  if (callbacks.length !== 1) throw new Error('callback')
  const callback = new URL(callbacks[0])
  if (callback.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(callback.hostname) ||
      !callback.port || callback.pathname !== '/callback' || callback.username || callback.password || callback.search || callback.hash) throw new Error('callback')
  return { url, callback }
}

async function start (data) {
  const { url, callback } = initialUrl(data.url)
  if (!path.isAbsolute(data.userData) || fs.realpathSync(path.dirname(data.userData)) !== path.dirname(data.userData) ||
      (fs.existsSync(data.userData) && fs.lstatSync(data.userData).isSymbolicLink())) throw new Error('profile')
  fs.mkdirSync(data.userData, { recursive: true, mode: 0o700 })
  const label = String(data.label || '').replace(/[\x00-\x1f\x7f]/g, '').slice(0, 160)
  const title = `CASE 独立额度授权${label ? ' · ' + label : ''}`
  app.setName('CASE Usage Authorization')
  app.setPath('userData', data.userData)
  app.setPath('sessionData', data.userData)
  app.setPath('logs', path.join(data.userData, 'Logs'))
  if (!app.requestSingleInstanceLock()) throw new Error('busy')
  await app.whenReady()
  const browserSession = session.fromPartition('persist:case-usage-auth')
  browserSession.setPermissionRequestHandler((_contents, _permission, answer) => answer(false))
  browserSession.setPermissionCheckHandler(() => false)
  const preferences = { session: browserSession, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, devTools: false }
  const canNavigate = value => {
    try {
      const target = new URL(value)
      return !target.username && !target.password && (target.protocol === 'https:' ||
        (target.origin === callback.origin && target.pathname === callback.pathname && !target.hash))
    } catch { return false }
  }
  app.on('web-contents-created', (_event, contents) => {
    for (const event of ['will-navigate', 'will-redirect']) {
      contents.on(event, (navigation, target) => { if (!canNavigate(target)) navigation.preventDefault() })
    }
    contents.on('will-attach-webview', event => event.preventDefault())
    contents.on('will-prevent-unload', event => event.preventDefault())
    contents.on('page-title-updated', event => event.preventDefault())
    contents.setWindowOpenHandler(({ url: target }) => (canNavigate(target) || target === 'about:blank')
      ? { action: 'allow', overrideBrowserWindowOptions: { title, width: 960, height: 760, autoHideMenuBar: true, webPreferences: preferences } }
      : { action: 'deny' })
  })
  const win = new BrowserWindow({ title, width: 1120, height: 850, minWidth: 760, minHeight: 600, autoHideMenuBar: true, webPreferences: preferences })
  win.setMenu(null)
  win.on('closed', () => app.quit())
  // Report creation promptly; page/network errors stay visible in this window.
  // The CLI performs the callback, token exchange and final identity check.
  win.loadURL(url.href).catch(() => {})
  win.show()
  win.focus()
  process.stdout.write('CASE_AUTH_READY\n')
}

// Read before returning to Electron's event loop so sessionData is set before
// ready. Parent closes this pipe immediately after sending one bounded object.
try {
  const input = fs.readFileSync(0, 'utf8')
  if (input.length > 16384) throw new Error('input')
  start(JSON.parse(input)).catch(() => app.exit(1))
} catch { app.exit(1) }
app.on('window-all-closed', () => app.quit())
